# app/api/endpoints/users.py
import logging
import math
from typing import List

import asyncpg
from fastapi import (APIRouter, Depends, HTTPException, Header, Query, Request,
                     Response, status)
# Using fastapi.Response and status directly
from fastapi.responses import JSONResponse

# Import dependencies, schemas, crud functions
from app.api import deps
# Import specific CRUD exceptions
from app.crud.crud_user import (UserNotFoundError, UsernameAlreadyExistsError,
                                 DatabaseInteractionError)
from app.crud import crud_user # Need crud_user instance to call its methods
from app.schemas import token as token_schemas
from app.schemas import user as user_schemas # Use aliased schemas

from app.core.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])
notifications_router = APIRouter() 

# Define tags for OpenAPI documentation grouping
user_tags = ["User"]
friend_tags = ["Friends"]
notification_tags = ["Notifications"]
settings_tags = ["Settings", "User"]

# === User Account & Profile Endpoints ===

@router.get("/me", response_model=user_schemas.UserBase, tags=user_tags)
async def read_users_me(
    current_user_record: asyncpg.Record = Depends(deps.get_current_user_record)
):
    """
    Get profile of the currently authenticated user.
    """
    # The dependency already fetched the record, just return it
    # deps.get_current_user_record handles UserNotFoundError and maps to 404/500
    # Pydantic will automatically map based on the response_model
    return current_user_record # Returns asyncpg.Record, which Pydantic maps with from_attributes=True


@router.patch("/me", response_model=user_schemas.UserBase, tags=user_tags)
async def update_user_me(
    profile_update: user_schemas.UserProfileUpdate,
    current_user_id: int = Depends(deps.get_current_user_id),
    db: asyncpg.Connection = Depends(deps.get_db)
):
    """
    Update the profile (display name, profile picture) for the currently authenticated user.
    """
    # Check if any fields are actually provided for update
    # crud_user.update_user_profile now handles the case where no fields are set and returns current data
    # So, we don't strictly need the explicit check and 400 BAD REQUEST here,
    # unless we prefer that API behavior over returning the current state.
    # Let's keep the CRUD behavior and rely on it.

    try:
        # crud_user.update_user_profile returns the updated record or the current one if no changes
        # It raises UserNotFoundError if the user is not found (unlikely after dependency)
        # or DatabaseInteractionError for DB issues.
        updated_user_record = await crud_user.update_user_profile(
            db=db, user_id=current_user_id, profile_in=profile_update
        )
        # Return the record, Pydantic maps it.
        return updated_user_record

    except UserNotFoundError as e:
         # This case is highly unlikely given the deps.get_current_user_id dependency,
         # but good practice to catch if the user disappears concurrently.
         logger.error(f"User {current_user_id} not found during profile update: {e}", exc_info=True)
         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except DatabaseInteractionError as e:
        logger.error(f"DB interaction error updating profile for {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error updating profile")
    except Exception as e:
        logger.error(f"Unexpected error updating profile for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error updating profile")

@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT, tags=user_tags)
async def delete_user_me(
    current_user_id: int = Depends(deps.get_current_user_id),
    db: asyncpg.Connection = Depends(deps.get_db)
):
    """
    Delete the account of the currently authenticated user.
    """
    try:
        # crud_user.delete_user_account returns True if deleted, False if not found.
        # It raises DatabaseInteractionError for DB issues.
        deleted = await crud_user.delete_user_account(db=db, user_id=current_user_id)
        if not deleted:
            # This shouldn't happen if get_current_user_id succeeded, but handle defensively
            logger.error(f"Attempted to delete user {current_user_id}, but CRUD reported not found.")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found for deletion.")
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DatabaseInteractionError as e:
        logger.error(f"DB interaction error deleting account {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error deleting account.")
    except Exception as e:
        logger.error(f"Unexpected error deleting account for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error deleting account")

# === User Settings Endpoints ===

@router.get("/me/settings", response_model=user_schemas.PrivacySettingsResponse, tags=settings_tags)
async def read_privacy_settings_me(
    current_user_id: int = Depends(deps.get_current_user_id),
    db: asyncpg.Connection = Depends(deps.get_db)
):
    """
    Get the privacy settings for the currently authenticated user.
    """
    try:
        # crud_user.get_privacy_settings raises UserNotFoundError if user isn't found
        # and DatabaseInteractionError for DB issues.
        settings_record = await crud_user.get_privacy_settings(db=db, user_id=current_user_id)
        # Map record to Pydantic response model
        return settings_record
    except UserNotFoundError as e:
         # Propagate UserNotFoundError from CRUD as 404 HTTPException
         logger.error(f"Settings not found for user {current_user_id}: {e}", exc_info=True)
         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except DatabaseInteractionError as e:
        logger.error(f"DB error fetching settings for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error fetching settings.")
    except Exception as e:
        logger.error(f"Unexpected error fetching settings for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error fetching settings.")


@router.patch("/me/settings", response_model=user_schemas.PrivacySettingsResponse, tags=settings_tags)
async def update_privacy_settings_me(
    settings_update: user_schemas.PrivacySettingsUpdate,
    current_user_id: int = Depends(deps.get_current_user_id),
    db: asyncpg.Connection = Depends(deps.get_db)
):
    """
    Update privacy settings for the currently authenticated user.
    """
    # Check if any fields provided for update
    # crud_user.update_privacy_settings now handles this case and returns current settings
    # So, we don't need the explicit check and 400 here.
    # Let's keep the CRUD behavior and rely on it.

    try:
        # crud_user.update_privacy_settings raises UserNotFoundError or DatabaseInteractionError
        updated_settings_record = await crud_user.update_privacy_settings(
            db=db, user_id=current_user_id, settings_in=settings_update
        )
        # Map record to Pydantic response model
        return updated_settings_record
    except UserNotFoundError as e:
         # Propagate UserNotFoundError from CRUD as 404 HTTPException
         logger.error(f"User {current_user_id} not found during settings update: {e}", exc_info=True)
         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except DatabaseInteractionError as e:
        logger.error(f"DB interaction error updating settings for {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error updating settings")
    except Exception as e:
        logger.error(f"Unexpected error updating settings for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error updating settings")


# === Existing Endpoints (Username Check, Set Username, Friends/Followers, Notifications) ===

@router.get("/check-username", response_model=user_schemas.UsernameCheckResponse, tags=user_tags)
@limiter.limit("7/minute")
async def check_username(
    request: Request, # For limiter state
    token_data: token_schemas.FirebaseTokenData = Depends(deps.get_verified_token_data),
    db: asyncpg.Connection = Depends(deps.get_db)
):
    # Implementation unchanged from previous version
    try:
        # crud_user.get_or_create_user_by_firebase raises ValueError (if token missing email)
        # or DatabaseInteractionError for DB issues.
        _, needs_username = await crud_user.get_or_create_user_by_firebase(db=db, token_data=token_data)
        return user_schemas.UsernameCheckResponse(needsUsername=needs_username)
    except ValueError as ve: # Raised by CRUD if email is missing in token
        logger.warning(f"Value error checking username for uid {token_data.uid}: {ve}", exc_info=False) # Avoid logging token data in trace
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except DatabaseInteractionError as e: # Catch DB errors from get_or_create
        logger.error(f"DB error checking username for uid {token_data.uid}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error checking username status.")
    except HTTPException as he: # Propagate HTTP exceptions from dependencies
        raise he
    except Exception as e: # Catch any other unexpected errors
        logger.error(f"Unexpected error checking username for uid {token_data.uid}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error checking username status")

@router.post("/set-username", response_model=user_schemas.UsernameSetResponse, status_code=status.HTTP_200_OK, tags=user_tags)
@limiter.limit("2/minute")
async def set_username(
    request: Request, # For limiter state
    data: user_schemas.UsernameSet,
    current_user_id: int = Depends(deps.get_current_user_id),
    db: asyncpg.Connection = Depends(deps.get_db)
):
    # Implementation unchanged from previous version
    try:
        # crud_user.set_user_username raises UsernameAlreadyExistsError, UserNotFoundError, DatabaseInteractionError
        await crud_user.set_user_username(db=db, user_id=current_user_id, username=data.username)
        return user_schemas.UsernameSetResponse(message="Username set successfully")
    except UsernameAlreadyExistsError as e:
         # Map specific CRUD error to 409 Conflict
         logger.warning(f"Username conflict setting username for user {current_user_id}: {e}", exc_info=False)
         raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except UserNotFoundError as e:
         # This case is unlikely after deps.get_current_user_id, but handle defensively
         logger.error(f"User {current_user_id} not found when setting username: {e}", exc_info=True)
         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except DatabaseInteractionError as e: # Catch generic DB errors from set_user_username
         logger.error(f"DB interaction error setting username for {current_user_id}: {e}", exc_info=True)
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error setting username")
    except HTTPException as he: # Propagate HTTP exceptions from dependencies
        raise he
    except Exception as e: # Catch any other unexpected errors
        logger.error(f"Unexpected error setting username for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error setting username")

@router.get("/following", response_model=user_schemas.PaginatedUserResponse, tags=friend_tags)
@limiter.limit("10/minute")
async def get_following(
    request: Request, # For limiter state
    page: int = Query(1, ge=1, description="Page number to retrieve"),
    page_size: int = Query(20, ge=1, le=100, description="Number of users per page"),
    current_user_id: int = Depends(deps.get_current_user_id),
    db: asyncpg.Connection = Depends(deps.get_db)
):
    # Implementation unchanged from previous version
    try:
        # crud_user.get_following raises DatabaseInteractionError
        following_records, total_items = await crud_user.get_following(
            db=db, user_id=current_user_id, page=page, page_size=page_size
        )
        total_pages = math.ceil(total_items / page_size) if page_size > 0 else 0
        # is_following should be True for all returned items in this endpoint's context
        # UserFollowInfo schema expects `is_following`. We explicitly set it for clarity,
        # although the query in crud_user.get_following could return this if needed.
        # Based on the current crud_user.get_following, it returns user columns only,
        # so mapping here is correct.
        items = [user_schemas.UserFollowInfo(**record, is_following=True) for record in following_records]
        return user_schemas.PaginatedUserResponse(
            items=items, page=page, page_size=page_size,
            total_items=total_items, total_pages=total_pages
        )
    except DatabaseInteractionError as e: # Catch DB errors from crud
        logger.error(f"DB error fetching following list for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error fetching following list.")
    except Exception as e:
        logger.error(f"Unexpected error fetching following list for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error fetching following list")

@router.get("/followers", response_model=user_schemas.PaginatedUserResponse, tags=friend_tags)
@limiter.limit("5/minute")
async def get_followers(
    request: Request, # For limiter state
    page: int = Query(1, ge=1, description="Page number to retrieve"),
    page_size: int = Query(20, ge=1, le=100, description="Number of users per page"),
    current_user_id: int = Depends(deps.get_current_user_id),
    db: asyncpg.Connection = Depends(deps.get_db)
):
    # Implementation unchanged from previous version
    try:
        # crud_user.get_followers raises DatabaseInteractionError
        # crud_user.get_followers is expected to return records including the `is_following` boolean flag
        follower_records, total_items = await crud_user.get_followers(
            db=db, user_id=current_user_id, page=page, page_size=page_size
        )
        total_pages = math.ceil(total_items / page_size) if page_size > 0 else 0
        # UserFollowInfo schema expects `is_following`.
        items = [user_schemas.UserFollowInfo(**record) for record in follower_records]
        return user_schemas.PaginatedUserResponse(
            items=items, page=page, page_size=page_size,
            total_items=total_items, total_pages=total_pages
        )
    except DatabaseInteractionError as e: # Catch DB errors from crud
        logger.error(f"DB error fetching followers list for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error fetching followers list.")
    except Exception as e:
        logger.error(f"Unexpected error fetching followers list for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error fetching followers list")

@router.get("/search", response_model=user_schemas.PaginatedUserResponse, tags=friend_tags)
@limiter.limit("30/minute")
async def search_users(
    request: Request, # For limiter state
    q: str = Query(..., min_length=1, description="Email or username fragment to search for."), # Changed 'email' to 'q' for generic query
    page: int = Query(1, ge=1, description="Page number to retrieve"),
    page_size: int = Query(10, ge=1, le=50, description="Number of users per page"),
    current_user_id: int = Depends(deps.get_current_user_id),
    db: asyncpg.Connection = Depends(deps.get_db)
):
    # Implementation unchanged from previous version
    try:
        # crud_user.search_users raises DatabaseInteractionError
        # crud_user.search_users is expected to return records including the `is_following` flag
        users_found_records, total_items = await crud_user.search_users(
            db=db, current_user_id=current_user_id, query=q, page=page, page_size=page_size # Pass 'q' as query
        )
        total_pages = math.ceil(total_items / page_size) if page_size > 0 else 0
        # UserFollowInfo schema expects `is_following`.
        items = [user_schemas.UserFollowInfo(**user) for user in users_found_records]
        return user_schemas.PaginatedUserResponse(
            items=items, page=page, page_size=page_size,
            total_items=total_items, total_pages=total_pages
        )
    except DatabaseInteractionError as e: # Catch DB errors from crud
        logger.error(f"DB error searching users with term '{q}' by user {current_user_id}: {e}", exc_info=True) # Log 'q'
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error searching users")
    except Exception as e:
        logger.error(f"Unexpected error searching users with term '{q}' by user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error searching users")

@router.post("/{user_id}/follow", status_code=status.HTTP_201_CREATED, tags=friend_tags, responses={
    status.HTTP_200_OK: {"description": "Already following the user", "model": user_schemas.UsernameSetResponse}, # Use UsernameSetResponse for consistency
    status.HTTP_201_CREATED: {"description": "Successfully followed the user", "model": user_schemas.UsernameSetResponse}, # Use UsernameSetResponse
    status.HTTP_400_BAD_REQUEST: {"description": "Cannot follow yourself"},
    status.HTTP_404_NOT_FOUND: {"description": "User to follow not found"},
})
@limiter.limit("10/minute")
async def follow_user(
    request: Request, # For limiter state
    user_id: int, # Target user ID from path
    current_user_id: int = Depends(deps.get_current_user_id),
    db: asyncpg.Connection = Depends(deps.get_db)
):
    # Implementation unchanged from previous version
    if current_user_id == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot follow yourself")
    try:
        # crud_user.follow_user returns True if already following, False otherwise.
        # It raises UserNotFoundError if the target user doesn't exist,
        # and DatabaseInteractionError for other DB issues.
        already_following = await crud_user.follow_user(db=db, follower_id=current_user_id, followed_id=user_id)
        if already_following:
             # Return 200 OK if the relationship already existed
             return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "Already following this user"})
        # Return 201 Created for a new relationship
        return user_schemas.UsernameSetResponse(message="User followed")
    except UserNotFoundError as e:
        # Propagate UserNotFoundError from CRUD as 404
        logger.warning(f"User {current_user_id} attempted to follow non-existent user {user_id}: {e}", exc_info=False)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except DatabaseInteractionError as e: # Catch generic DB errors from follow_user
         logger.error(f"DB interaction error during follow {current_user_id}->{user_id}: {e}", exc_info=True)
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error processing follow request")
    except HTTPException as he: # Propagate HTTP exceptions (e.g. Cannot follow self)
        raise he
    except Exception as e: # Catch any other unexpected errors
        logger.error(f"Unexpected error processing follow {current_user_id}->{user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error processing follow request")

@router.delete("/{user_id}/follow", status_code=status.HTTP_200_OK, tags=friend_tags, response_model=user_schemas.UsernameSetResponse, responses={
    status.HTTP_200_OK: {"description": "User successfully unfollowed or was not being followed"},
    status.HTTP_204_NO_CONTENT: {"description": "User successfully unfollowed"}, # Although we return 200 OK with message
    status.HTTP_404_NOT_FOUND: {"description": "User to unfollow not found"},
})
@limiter.limit("10/minute")
async def unfollow_user(
    request: Request,
    user_id: int,
    current_user_id: int = Depends(deps.get_current_user_id),
    db: asyncpg.Connection = Depends(deps.get_db),
):
    try:
        deleted = await crud_user.unfollow_user(
            db=db, follower_id=current_user_id, followed_id=user_id
        )

        if not deleted:
            target_exists = await crud_user.check_user_exists(db=db, user_id=user_id)
            if not target_exists:
                logger.warning(
                    "User %s attempted to unfollow non-existent user %s",
                    current_user_id,
                    user_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User to unfollow not found",
                )
            logger.info(
                "User %s attempted to unfollow %s, but no follow relationship found",
                current_user_id,
                user_id,
            )
            return user_schemas.UsernameSetResponse(message="Not following this user")

        logger.info("User %s unfollowed user %s", current_user_id, user_id)
        return user_schemas.UsernameSetResponse(message="User unfollowed")

    # 1️⃣  propagate the 404/409, etc.
    except HTTPException:
        raise

    # 2️⃣  your DB-specific wrapper
    except DatabaseInteractionError as e:
        logger.error(
            "DB interaction error during unfollow %s→%s: %s",
            current_user_id,
            user_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error during unfollow operation",
        )

    # 3️⃣  true “unknown” errors
    except Exception as e:
        logger.error(
            "Unexpected error unfollowing user %s by %s: %s",
            user_id,
            current_user_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error unfollowing user",
        )

@limiter.limit("5/minute")
@notifications_router.get(
    "/notifications",
    response_model=user_schemas.PaginatedNotificationResponse,
    tags=notification_tags,
)
async def get_notifications(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    current_user_id: int = Depends(deps.get_current_user_id),
    db: asyncpg.Connection = Depends(deps.get_db),
):
    # Implementation unchanged from previous version
    try:
        # crud_user.get_user_notifications raises DatabaseInteractionError
        notification_records, total_items = await crud_user.get_user_notifications(
            db=db, user_id=current_user_id, page=page, page_size=page_size
        )
        total_pages = math.ceil(total_items / page_size) if page_size > 0 else 0
        # NotificationItem schema expects `isRead` (alias for is_read)
        items = [user_schemas.NotificationItem(**n) for n in notification_records]
        return user_schemas.PaginatedNotificationResponse(
            items=items, page=page, page_size=page_size,
            total_items=total_items, total_pages=total_pages
        )
    except DatabaseInteractionError as e: # Catch DB errors from crud
        logger.error(f"DB error fetching notifications for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error fetching notifications.")
    except Exception as e:
        logger.error(f"Unexpected error fetching notifications for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error fetching notifications")
    
@router.get("/{user_id}", response_model=user_schemas.UserBase, tags=user_tags)
async def read_user_by_id(
    user_id: int,
    # Require authentication to view any profile for now
    # Note: The dependency deps.get_current_user_id implicitly requires auth.
    # If you wanted public access to public profiles, you'd use deps.get_optional_current_user_id
    # and modify the logic below. For now, keeping it auth-required.
    requester_id: int = Depends(deps.get_current_user_id),
    db: asyncpg.Connection = Depends(deps.get_db)
):
    """
    Get public profile information for a specific user by their ID.
    Requires authentication.
    """
    try:
        # crud_user.get_user_by_id returns Optional[asyncpg.Record]. It raises DatabaseInteractionError.
        user_record = await crud_user.get_user_by_id(db=db, user_id=user_id)
        if not user_record:
            # Explicitly raise 404 if CRUD returns None
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # --- Privacy Check based on DB settings ---
        # Read privacy settings from the fetched user_record
        # Use .get() with a default in case the column is missing (e.g., schema change)
        profile_is_public = user_record.get('profile_is_public', True) # Default to public if column doesn't exist

        # If profile is private AND the requester is NOT the user themselves
        if not profile_is_public and requester_id != user_id:
            logger.warning(f"User {requester_id} attempted to view private profile of user {user_id}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This profile is private")
        # --- End Privacy Check ---

        # Return the record, Pydantic will map the relevant fields to UserBase response model
        return user_record

    except HTTPException as he:
        # Re-raise HTTP exceptions raised for privacy checks
        raise he
    except DatabaseInteractionError as e:
        logger.error(f"DB error fetching user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error fetching user profile.")
    except Exception as e:
        logger.error(f"Unexpected error fetching user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error fetching user profile.")
