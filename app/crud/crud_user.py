# backend/app/crud/crud_user.py
import asyncpg
import logging
from typing import Tuple, List, Optional, Dict, Any
import datetime # Used for timestamp in notifications

# Import schemas - adjust paths if necessary
from app.schemas import user as user_schemas
from app.schemas import token as token_schemas

logger = logging.getLogger(__name__)

# --- Custom Exceptions for User CRUD ---
class UserNotFoundError(Exception):
    """Raised when a user is expected but not found."""
    pass

class UsernameAlreadyExistsError(Exception):
    """Raised when attempting to set an already taken username."""
    pass

class DatabaseInteractionError(Exception):
    """Generic error for unexpected DB issues during CRUD operations."""
    pass


# --- CRUD Functions ---

async def get_user_by_id(db: asyncpg.Connection, user_id: int) -> Optional[asyncpg.Record]:
    """Fetches a complete user record by their database ID."""
    logger.debug(f"Fetching user by ID: {user_id}")
    # Fetch all columns needed for UserBase or potentially more if needed elsewhere
    # Include privacy settings here for easy access in endpoints like GET /users/{user_id}
    query = "SELECT id, email, username, display_name, profile_picture, profile_is_public, lists_are_public, allow_analytics FROM users WHERE id = $1"
    try:
        user = await db.fetchrow(query, user_id)
        # Note: We return Optional[asyncpg.Record]. The API layer is responsible for
        # checking if None is returned and raising HTTPException(404) if the user
        # was expected to exist (e.g., for /me endpoints).
        return user
    except Exception as e:
        logger.error(f"Error fetching user by ID {user_id}: {e}", exc_info=True)
        # Wrap any unexpected DB error
        raise DatabaseInteractionError("Database error fetching user by ID.") from e


async def get_user_by_firebase_uid(
    db: asyncpg.Connection,
    firebase_uid: str,
) -> Optional[asyncpg.Record]:
    """Return the complete user row for a given Firebase UID, or None."""
    logger.debug("Fetching user by Firebase UID: %s", firebase_uid)

    try:
        # safer: grab the whole row so every caller has the columns it expects
        return await db.fetchrow(
            "SELECT * FROM users WHERE firebase_uid = $1",
            firebase_uid,
        )
    except Exception as e:
        logger.error(
            "Error fetching user by Firebase UID %s: %s",
            firebase_uid,
            e,
            exc_info=True,
        )
        raise DatabaseInteractionError(
            "Database error fetching user by Firebase UID."
        ) from e
        
async def get_user_by_email(db: asyncpg.Connection, email: str) -> Optional[asyncpg.Record]:
     """Fetches a user record by email."""
     logger.debug(f"Fetching user by email: {email}")
     query = "SELECT id, email, username, firebase_uid FROM users WHERE email = $1"
     try:
         return await db.fetchrow(query, email)
     except Exception as e:
          logger.error(f"Error fetching user by email {email}: {e}", exc_info=True)
          raise DatabaseInteractionError("Database error fetching user by email.") from e

async def check_user_exists(db: asyncpg.Connection, user_id: int) -> bool:
     """Checks if a user exists by their database ID."""
     logger.debug(f"Checking existence of user ID: {user_id}")
     query = "SELECT EXISTS (SELECT 1 FROM users WHERE id = $1)"
     try:
         exists = await db.fetchval(query, user_id)
         return exists or False # Ensure boolean return
     except Exception as e:
          logger.error(f"Error checking existence for user ID {user_id}: {e}", exc_info=True)
          raise DatabaseInteractionError("Database error checking user existence.") from e

async def create_user(db: asyncpg.Connection, email: str, firebase_uid: str, display_name: Optional[str] = None, profile_picture: Optional[str] = None) -> int:
    """Creates a new user entry and returns the new user ID."""
    logger.info(f"Creating new user entry for email: {email}, firebase_uid: {firebase_uid}")
    # Assuming default privacy settings are set by the DB schema defaults
    query = """
        INSERT INTO users (email, firebase_uid, display_name, profile_picture, created_at, updated_at)
        VALUES ($1, $2, $3, $4, NOW(), NOW())
        RETURNING id
    """
    try:
        user_id = await db.fetchval(query, email, firebase_uid, display_name, profile_picture)
        if not user_id:
            logger.error(f"Failed to insert new user for email {email} - no ID returned.")
            # This is an unexpected DB state
            raise DatabaseInteractionError("Database insert failed to return new user ID")
        logger.info(f"New user created with ID: {user_id}")
        return user_id
    except asyncpg.exceptions.UniqueViolationError as e:
        # This specific DB error maps to a business logic error
        logger.error(f"Unique constraint violation during user creation for email {email}: {e}", exc_info=True)
        # Depending on constraints, could be email or firebase_uid conflict.
        # Assuming email is the primary unique identifier for "already exists" business logic here.
        # For precise handling, you might inspect `e.constraint_name`.
        raise UsernameAlreadyExistsError(f"User with email {email} already exists.") from e
    except Exception as e:
        logger.error(f"Unexpected error creating user {email}: {e}", exc_info=True)
        # Catch any other database-related or unexpected error
        raise DatabaseInteractionError("Failed to create user record.") from e


async def update_user_firebase_uid(db: asyncpg.Connection, user_id: int, firebase_uid: str):
    """Updates the Firebase UID for an existing user."""
    logger.warning(f"Updating firebase_uid for user {user_id} to {firebase_uid}")
    query = "UPDATE users SET firebase_uid = $1, updated_at = NOW() WHERE id = $2"
    try:
        status = await db.execute(query, firebase_uid, user_id)
        if status == 'UPDATE 0':
            # This might mean the user wasn't found, or the UID was already the same.
            # In the context of get_or_create, it's usually the latter or the user
            # was found by email then deleted concurrently (rare). We don't necessarily
            # need to raise UserNotFoundError here, as the caller (get_or_create)
            # already knows the user should exist based on prior checks.
            logger.warning(f"Update firebase_uid affected 0 rows for user {user_id}.")
        # Could also catch asyncpg.exceptions.UniqueViolationError if setting UID to an existing one.
    except Exception as e:
         logger.error(f"Error updating firebase_uid for user {user_id}: {e}", exc_info=True)
         raise DatabaseInteractionError("Database error updating firebase UID.") from e


async def get_or_create_user_by_firebase(db: asyncpg.Connection, token_data: token_schemas.FirebaseTokenData) -> Tuple[int, bool]:
    """
    Gets user ID from DB based on firebase token data, creating if necessary.
    Updates Firebase UID if user found by email but UID differs.
    Returns (user_id: int, needs_username: bool).
    """
    firebase_uid = token_data.uid
    email = token_data.email

    # Validate input from token data (basic checks)
    if not email:
        logger.error(f"Firebase token for uid {firebase_uid} missing email.")
        raise ValueError("Email missing from Firebase token data")
    if not firebase_uid:
        logger.error("Firebase token missing UID.") # Should be guaranteed by Firebase but check
        raise ValueError("UID missing from Firebase token data")


    async with db.transaction(): # Use transaction for atomicity
        try:
            # 1. Check by firebase_uid
            user_record_by_uid = await get_user_by_firebase_uid(db, firebase_uid)
            if user_record_by_uid:
                user_id = user_record_by_uid['id']
                # Fetch the full record to check username status correctly
                full_record = await get_user_by_id(db, user_id)
                 # Ensure full_record is not None, although unlikely if get_user_by_firebase_uid returned a record
                needs_username = full_record['username'] is None if full_record else True
                logger.debug(f"User found by firebase_uid: {user_id}, NeedsUsername: {needs_username}")
                return user_id, needs_username

            # 2. Check by email
            user_record_by_email = await get_user_by_email(db, email)
            if user_record_by_email:
                user_id = user_record_by_email['id']
                existing_firebase_uid = user_record_by_email['firebase_uid']
                # Fetch the full record to check username status correctly
                full_record = await get_user_by_id(db, user_id)
                needs_username = full_record['username'] is None if full_record else True
                logger.debug(f"User found by email: {user_id}. Existing UID: {existing_firebase_uid}, Token UID: {firebase_uid}")

                # Update Firebase UID if it's different or null
                if existing_firebase_uid != firebase_uid:
                    # Note: update_user_firebase_uid handles its own DB errors
                    await update_user_firebase_uid(db, user_id, firebase_uid)
                return user_id, needs_username

            # 3. Create new user
            # Extract optional profile info from token if available
            display_name = token_data.name # Use direct access if Pydantic model has the field
            profile_picture = token_data.picture
            # create_user handles its own exceptions (UniqueViolation, DatabaseInteraction)
            user_id = await create_user(db, email, firebase_uid, display_name, profile_picture)
            logger.info(f"New user created for firebase uid {firebase_uid}, ID: {user_id}")
            return user_id, True # New user always needs username

        # Catch specific exceptions from nested calls and re-raise them
        except (ValueError, UserNotFoundError, UsernameAlreadyExistsError, DatabaseInteractionError):
             raise # Re-raise known errors

        # Catch any other unexpected error during the get-or-create flow
        except Exception as e:
             logger.error(f"Unexpected error during get_or_create for firebase uid {firebase_uid}, email {email}: {e}", exc_info=True)
             raise DatabaseInteractionError("Database error during user lookup or creation.") from e


async def set_user_username(db: asyncpg.Connection, user_id: int, username: str):
    """Sets the username for a given user ID, checking for uniqueness."""
    logger.info(f"Attempting to set username for user_id {user_id} to '{username}'")
    try:
        # Check if username exists (case-insensitive) excluding the current user
        check_query = "SELECT id FROM users WHERE LOWER(username) = LOWER($1) AND id != $2"
        existing_user = await db.fetchrow(check_query, username, user_id)
        if existing_user:
            logger.warning(f"Username '{username}' already taken by user {existing_user['id']}.")
            raise UsernameAlreadyExistsError(f"Username '{username}' is already taken.")

        # Attempt to update
        update_query = "UPDATE users SET username = $1, updated_at = NOW() WHERE id = $2"
        status = await db.execute(update_query, username, user_id)

        if status == 'UPDATE 0':
            # This might happen if the user was deleted concurrently.
            # Check if the user actually exists before raising UserNotFoundError
            user_exists = await check_user_exists(db, user_id)
            if not user_exists:
                logger.error(f"Failed to set username: User with ID {user_id} not found.")
                raise UserNotFoundError(f"User with ID {user_id} not found.")
            else:
                # This implies an unexpected issue if user exists but update failed
                logger.error(f"Failed to update username for existing user {user_id} - rowcount 0.")
                raise DatabaseInteractionError("Failed to update username for existing user.")

        logger.info(f"Username successfully set for user_id {user_id}")

    except asyncpg.exceptions.UniqueViolationError as e:
         # This could happen in a race condition if the check passed but another request set the username concurrently.
         # This is still a UsernameAlreadyExistsError from a business perspective.
         logger.warning(f"UniqueViolation setting username for user {user_id} (race condition?): {e}", exc_info=True)
         raise UsernameAlreadyExistsError(f"Username '{username}' became taken during update.") from e
    except (UsernameAlreadyExistsError, UserNotFoundError):
         raise # Re-raise known exceptions
    except Exception as e:
        logger.error(f"Unexpected DB error setting username for user {user_id}: {e}", exc_info=True)
        raise DatabaseInteractionError("Database error setting username.") from e


async def get_following(db: asyncpg.Connection, user_id: int, page: int, page_size: int) -> Tuple[List[asyncpg.Record], int]:
    """Gets users the given user_id is following (paginated)."""
    offset = (page - 1) * page_size
    logger.debug(f"Fetching following for user {user_id}, page {page}, size {page_size}")

    try:
        # Get total count first
        count_query = "SELECT COUNT(*) FROM user_follows WHERE follower_id = $1"
        total_items = await db.fetchval(count_query, user_id) or 0

        if total_items == 0:
             return [], 0

        # Get paginated items - Select all fields needed for UserFollowInfo schema
        fetch_query = """
            SELECT u.id, u.email, u.username, u.display_name, u.profile_picture
            FROM user_follows uf
            JOIN users u ON uf.followed_id = u.id
            WHERE uf.follower_id = $1
            ORDER BY u.username ASC NULLS LAST, u.display_name ASC NULLS LAST -- Order by username, then display name
            LIMIT $2 OFFSET $3
        """
        following_records = await db.fetch(fetch_query, user_id, page_size, offset)
        logger.debug(f"Found {len(following_records)} following users (total: {total_items}) for user {user_id}")
        # Note: The endpoint mapping layer adds `is_following=True`
        return following_records, total_items
    except Exception as e:
        logger.error(f"Error fetching following list for user {user_id}: {e}", exc_info=True)
        raise DatabaseInteractionError("Database error fetching following list.") from e

async def get_followers(db: asyncpg.Connection, user_id: int, page: int, page_size: int) -> Tuple[List[asyncpg.Record], int]:
    """
    Gets users following the given user_id (paginated).
    Includes 'is_following' field indicating if user_id follows the follower back.
    """
    offset = (page - 1) * page_size
    logger.debug(f"Fetching followers for user {user_id}, page {page}, size {page_size}")

    try:
        # Get total count
        count_query = "SELECT COUNT(*) FROM user_follows WHERE followed_id = $1"
        total_items = await db.fetchval(count_query, user_id) or 0

        if total_items == 0:
             return [], 0

        # Fetch query including is_following status relative to user_id
        fetch_query = """
            SELECT
                u.id, u.email, u.username, u.display_name, u.profile_picture,
                EXISTS (
                    SELECT 1 FROM user_follows f_back
                    WHERE f_back.follower_id = $1 -- The user whose followers list is being viewed
                      AND f_back.followed_id = u.id -- Check if they follow this specific follower (u)
                ) AS is_following
            FROM user_follows uf -- The relationship indicating u follows user_id
            JOIN users u ON uf.follower_id = u.id -- Get the follower's details (u)
            WHERE uf.followed_id = $1 -- Filter for followers of user_id
            ORDER BY u.username ASC NULLS LAST, u.display_name ASC NULLS LAST -- Order by username, then display name
            LIMIT $2 OFFSET $3
        """
        follower_records = await db.fetch(fetch_query, user_id, page_size, offset)
        logger.debug(f"Found {len(follower_records)} followers (total: {total_items}) for user {user_id}")
        return follower_records, total_items
    except Exception as e:
        logger.error(f"Error fetching followers list for user {user_id}: {e}", exc_info=True)
        raise DatabaseInteractionError("Database error fetching followers list.") from e

async def search_users(db: asyncpg.Connection, current_user_id: int, query: str, page: int, page_size: int) -> Tuple[List[asyncpg.Record], int]:
     """Searches users by email/username, excluding self, including follow status relative to current_user_id."""
     offset = (page - 1) * page_size
     search_term_lower = f"%{query.lower()}%" # Case-insensitive search
     logger.debug(f"Searching users for '{query}' by user {current_user_id}, page {page}, size {page_size}")

     params = [current_user_id, search_term_lower]
     param_idx = 3 # Next param for LIMIT starts at $3

     # Count query
     count_query = """
         SELECT COUNT(*)
         FROM users u
         WHERE (LOWER(u.email) LIKE $2 OR LOWER(u.username) LIKE $2)
           AND u.id != $1
     """
     try:
        total_items = await db.fetchval(count_query, *params) or 0

        if total_items == 0:
            return [], 0

        # Fetch query including is_following status
        fetch_query = f"""
            SELECT
                u.id, u.email, u.username, u.display_name, u.profile_picture,
                EXISTS (
                    SELECT 1 FROM user_follows uf_check
                    WHERE uf_check.follower_id = $1 -- The searching user's ID
                      AND uf_check.followed_id = u.id
                ) AS is_following
            FROM users u
            WHERE (LOWER(u.email) LIKE $2 OR LOWER(u.username) LIKE $2) -- Search term
              AND u.id != $1 -- Exclude self
            ORDER BY u.username ASC NULLS LAST, u.display_name ASC NULLS LAST, u.email ASC -- Order by username, display name, email
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """
        params.extend([page_size, offset])
        users_found = await db.fetch(fetch_query, *params)
        logger.debug(f"Found {len(users_found)} users matching search (total: {total_items}) for user {current_user_id}")
        return users_found, total_items
     except Exception as e:
         logger.error(f"Error searching users for '{query}' by user {current_user_id}: {e}", exc_info=True)
         raise DatabaseInteractionError("Database error searching users.") from e


async def follow_user(db: asyncpg.Connection, follower_id: int, followed_id: int) -> bool:
    """Creates a follow relationship. Returns True if already following, False otherwise."""
    logger.info(f"User {follower_id} attempting to follow user {followed_id}")
    try:
        # Check if target user exists first
        if not await check_user_exists(db, followed_id):
            logger.warning(f"Attempt to follow non-existent user {followed_id}")
            raise UserNotFoundError("User to follow not found")

        insert_query = """
            INSERT INTO user_follows (follower_id, followed_id, created_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (follower_id, followed_id) DO NOTHING
            RETURNING created_at -- Return something if a row was inserted
        """
        # Using fetchval with RETURNING is a way to check if a row was actually inserted
        # ON CONFLICT DO NOTHING means RETURNING will be NULL if the row already exists
        created_at = await db.fetchval(insert_query, follower_id, followed_id)

        if created_at is not None: # A row was inserted
            logger.info(f"User {follower_id} successfully followed user {followed_id}")
            # TODO: Add notification logic here or trigger async task
            return False # Not already following
        else: # created_at is NULL, meaning ON CONFLICT DO NOTHING was triggered
            logger.warning(f"User {follower_id} already following user {followed_id}")
            return True # Already following

    except (UserNotFoundError):
        raise # Re-raise specific exception
    except Exception as e:
        logger.error(f"DB error during follow {follower_id}->{followed_id}: {e}", exc_info=True)
        raise DatabaseInteractionError("Database error during follow operation.") from e


async def unfollow_user(db: asyncpg.Connection, follower_id: int, followed_id: int) -> bool:
    """Removes a follow relationship. Returns True if unfollowed, False if not following."""
    logger.info(f"User {follower_id} attempting to unfollow user {followed_id}")
    delete_query = "DELETE FROM user_follows WHERE follower_id = $1 AND followed_id = $2"
    try:
        status = await db.execute(delete_query, follower_id, followed_id)
        deleted_count = int(status.split(" ")[1]) # Extracts count from 'DELETE <count>'
        if deleted_count > 0:
            logger.info(f"User {follower_id} unfollowed user {followed_id}")
            return True
        else:
            # Deleted 0 rows. Could be because user was not following, or target user doesn't exist.
            logger.warning(f"User {follower_id} tried to unfollow {followed_id}, but no follow relationship found.")
            return False
    except Exception as e:
        logger.error(f"DB error during unfollow {follower_id}->{followed_id}: {e}", exc_info=True)
        raise DatabaseInteractionError("Database error during unfollow operation.") from e


async def get_user_notifications(db: asyncpg.Connection, user_id: int, page: int, page_size: int) -> Tuple[List[asyncpg.Record], int]:
     """Fetches notifications for a user, ordered by timestamp descending."""
     offset = (page - 1) * page_size
     logger.debug(f"Fetching notifications for user {user_id}, page {page}, size {page_size}")

     try:
        count_query = "SELECT COUNT(*) FROM notifications WHERE user_id = $1"
        total_items = await db.fetchval(count_query, user_id) or 0

        if total_items == 0:
             return [], 0

        fetch_query = """
            SELECT id, title, message, is_read, timestamp
            FROM notifications
            WHERE user_id = $1
            ORDER BY timestamp DESC
            LIMIT $2 OFFSET $3
        """
        notifications = await db.fetch(fetch_query, user_id, page_size, offset)
        logger.debug(f"Found {len(notifications)} notifications (total: {total_items}) for user {user_id}")
        return notifications, total_items
     except Exception as e:
         logger.error(f"Error fetching notifications for user {user_id}: {e}", exc_info=True)
         raise DatabaseInteractionError("Database error fetching notifications.") from e


# --- User Profile and Settings CRUD (Implementations added) ---

async def get_current_user_profile(db: asyncpg.Connection, user_id: int) -> asyncpg.Record:
    """Fetches the user profile data needed for GET /users/me."""
    # This function is primarily for internal use within the CRUD layer
    # to fetch the updated record after an update.
    # It's essentially a wrapper around get_user_by_id but designed to
    # expect the user to exist.
    logger.debug(f"Fetching profile for user_id: {user_id}")
    try:
        # Assuming UserBase schema needs these fields
        query = "SELECT id, email, username, display_name, profile_picture FROM users WHERE id = $1"
        user = await db.fetchrow(query, user_id)
        if not user:
             # Raising UserNotFoundError here makes the contract clear:
             # this function expects the user to exist.
             logger.warning(f"Profile not found for user_id {user_id}")
             raise UserNotFoundError(f"User with ID {user_id} not found.")
        return user
    except UserNotFoundError:
         raise # Re-raise specific exception
    except Exception as e:
        logger.error(f"Error fetching profile for user {user_id}: {e}", exc_info=True)
        raise DatabaseInteractionError("Database error fetching user profile.") from e


async def update_user_profile(db: asyncpg.Connection, user_id: int, profile_in: user_schemas.UserProfileUpdate) -> asyncpg.Record:
    """Updates the user's display name and/or profile picture."""
    logger.info(f"Updating profile for user_id: {user_id}")
    # Use model_dump(exclude_unset=True) from Pydantic V2
    update_fields = profile_in.model_dump(exclude_unset=True) # by_alias is True by default if aliases are used

    if not update_fields:
        # This case should be handled by the API layer before calling CRUD,
        # but as a safeguard, we can fetch and return the current profile.
        logger.warning(f"Update profile called for user {user_id} with no fields to update.")
        # Use the function that expects the user to exist
        return await get_current_user_profile(db, user_id)

    set_clauses = []
    params = []
    param_index = 1

    # Iterate through the Pydantic model fields that are set
    # The keys in update_fields will be the model field names (e.g., 'displayName', 'profilePicture')
    # We need to map these to DB column names (e.g., 'display_name', 'profile_picture')

    # Manual mapping based on schema aliases (displayName -> display_name)
    if 'displayName' in update_fields:
        set_clauses.append(f"display_name = ${param_index}")
        params.append(update_fields['displayName']) # Use value from dump
        param_index += 1
    if 'profilePicture' in update_fields:
        set_clauses.append(f"profile_picture = ${param_index}")
        params.append(update_fields['profilePicture']) # Use value from dump
        param_index += 1

    # This check is redundant if the first check 'if not update_fields:' passes,
    # but keeping it as a safeguard.
    if not set_clauses:
         logger.warning(f"Update profile called for user {user_id}, but no valid update fields found after parsing.")
         return await get_current_user_profile(db, user_id)


    params.append(user_id) # For WHERE clause ($param_index)
    sql = f"""
        UPDATE users
        SET {', '.join(set_clauses)}, updated_at = NOW()
        WHERE id = ${param_index}
        RETURNING id, email, username, display_name, profile_picture
    """

    try:
        updated_record = await db.fetchrow(sql, *params)
        if not updated_record:
            # If the update affected 0 rows, the user might not exist or was deleted.
            # check_user_exists helps distinguish.
            if not await check_user_exists(db, user_id):
                 raise UserNotFoundError(f"User {user_id} not found for profile update.")
            else:
                 # User exists but update returned 0 rows - unexpected issue.
                 logger.error(f"Profile update for user {user_id} returned no record despite user existing. SQL: {sql}", exc_info=True)
                 raise DatabaseInteractionError("Failed to update profile.")
        logger.info(f"Profile updated successfully for user {user_id}")
        return updated_record
    except (UserNotFoundError):
         raise # Re-raise specific exceptions
    except Exception as e:
        logger.error(f"Error updating profile for user {user_id}: {e}", exc_info=True)
        raise DatabaseInteractionError("Database error updating profile.") from e


async def get_privacy_settings(db: asyncpg.Connection, user_id: int) -> asyncpg.Record:
    """Fetches privacy settings for a user."""
    logger.debug(f"Fetching privacy settings for user_id: {user_id}")
    # Assuming privacy settings are columns in the 'users' table
    query = "SELECT profile_is_public, lists_are_public, allow_analytics FROM users WHERE id = $1"
    try:
        settings_record = await db.fetchrow(query, user_id)
        if not settings_record:
            # If user not found, raise specific error
            raise UserNotFoundError(f"User {user_id} not found when fetching privacy settings.")
        return settings_record
    except UserNotFoundError:
         raise # Re-raise specific exception
    except Exception as e:
        logger.error(f"Error fetching settings for user {user_id}: {e}", exc_info=True)
        raise DatabaseInteractionError("Database error fetching privacy settings.") from e

async def update_privacy_settings(db: asyncpg.Connection, user_id: int, settings_in: user_schemas.PrivacySettingsUpdate) -> asyncpg.Record:
    """Updates privacy settings for a user."""
    logger.info(f"Updating privacy settings for user_id: {user_id}")
    update_fields = settings_in.model_dump(exclude_unset=True)

    if not any(v is not None for v in update_fields.values()):
        logger.warning(f"Update privacy settings called for user {user_id} with no fields to update.")
        return await get_privacy_settings(db, user_id)

    set_clauses = []
    params = []
    param_index = 1
    if "profile_is_public" in update_fields:
        set_clauses.append(f"profile_is_public = ${param_index}")
        params.append(update_fields["profile_is_public"])
        param_index += 1
    if "lists_are_public" in update_fields:
        set_clauses.append(f"lists_are_public = ${param_index}")
        params.append(update_fields["lists_are_public"])
        param_index += 1
    if "allow_analytics" in update_fields:
        set_clauses.append(f"allow_analytics = ${param_index}")
        params.append(update_fields["allow_analytics"])
        param_index += 1

    if not set_clauses:
         logger.warning(f"Update privacy settings called for user {user_id}, but no valid update fields found after parsing.")
         return await get_privacy_settings(db, user_id)

    params.append(user_id)
    sql = f"""
        UPDATE users
        SET {', '.join(set_clauses)}, updated_at = NOW()
        WHERE id = ${param_index}
        RETURNING profile_is_public, lists_are_public, allow_analytics
    """
    try:
        # The database operation is the only thing that should be in the try block
        updated_settings = await db.fetchrow(sql, *params)
    except Exception as e:
        logger.error(f"Error updating privacy settings for user {user_id}: {e}", exc_info=True)
        raise DatabaseInteractionError("Database error updating privacy settings.") from e

    # The logic for handling the result is now outside the try/except block
    if not updated_settings:
         if not await check_user_exists(db, user_id):
              raise UserNotFoundError(f"User {user_id} not found for privacy settings update.")
         else:
              logger.error(f"Privacy settings update for user {user_id} returned no record.")
              raise DatabaseInteractionError("Failed to update privacy settings.")

    logger.info(f"Privacy settings updated for user {user_id}")
    return updated_settings

async def delete_user_account(db: asyncpg.Connection, user_id: int) -> bool:
    """
    Deletes a user account and potentially related data (depending on DB constraints).
    Returns True if deleted, False if user not found.
    """
    logger.warning(f"Attempting to delete account for user ID: {user_id}")
    # Ensure foreign key constraints (ON DELETE CASCADE or SET NULL) are set up
    # correctly in your database schema to handle related data (lists, follows, etc.)
    query = "DELETE FROM users WHERE id = $1"
    try:
        status = await db.execute(query, user_id)
        # Check the command tag string 'DELETE <count>'
        deleted_count = int(status.split(" ")[1])
        if deleted_count > 0:
            logger.info(f"Successfully deleted account for user ID: {user_id}")
            return True
        else:
            logger.warning(f"Attempted to delete user {user_id}, but user was not found.")
            return False # User didn't exist
    except Exception as e:
        logger.error(f"Error deleting account for user {user_id}: {e}", exc_info=True)
        raise DatabaseInteractionError("Database error deleting account.") from e