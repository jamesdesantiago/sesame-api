# backend/tests/utils.py

import os
import asyncpg
import pytest # For pytest.fail
from typing import Dict, Any, Optional
from unittest.mock import MagicMock # For mocking records
import asyncio # For sleep
import datetime # For timestamps if needed in creation

# --- Mocking Helpers ---

def create_mock_record(data: Dict[str, Any]) -> MagicMock:
    """ Creates a mock asyncpg.Record for unit testing CRUD functions. """
    mock = MagicMock(spec=asyncpg.Record)
    # Configure __getitem__ to return values from the dictionary
    mock.__getitem__.side_effect = lambda key: data.get(key)
    # Allow get method access
    mock.get.side_effect = lambda key, default=None: data.get(key, default)
    # Allow direct attribute access if needed (less common for Record)
    for key, value in data.items():
        setattr(mock, key, value)
    # Make it behave like a dictionary for ** expansion if needed
    mock.items.return_value = data.items()
    mock.keys.return_value = data.keys()
    # Add _asdict method if your endpoint mapping relies on it (Pydantic orm_mode)
    # Pydantic V2 uses `from_attributes=True` and doesn't strictly need _asdict
    # but adding it doesn't hurt compatibility.
    mock._asdict = lambda: data
    return mock

# --- Direct DB Data Creation Helpers (for Integration Tests) ---
# These interact directly with the database connection provided by the test.
# They DO NOT contain cleanup logic; cleanup is handled by the transaction rollback
# in the db_tx fixture used by the test function.


async def create_test_user_direct(db_conn: asyncpg.Connection, suffix: str, make_unique: bool = True, username: Optional[str] = None) -> Dict[str, Any]:
    """Creates a user directly in the DB for test setup. Handles potential conflicts."""
    unique_part = f"_{os.urandom(3).hex()}" if make_unique else ""
    email = f"test_{suffix}{unique_part}@example.com"
    fb_uid = f"test_fb_uid_{suffix}{unique_part}"
    user_name = username if username is not None else f"testuser_{suffix}{unique_part}"
    display_name = f"Test User {suffix}"
    user_id = None
    try:
        # Use a transaction here IF this helper might be called outside of the main db_tx fixture
        # But assuming it's always called within db_tx, we don't need nested transactions.
        user_id = await db_conn.fetchval(
            """
            INSERT INTO users (email, firebase_uid, username, display_name, created_at, updated_at)
            VALUES ($1, $2, $3, $4, NOW(), NOW())
            ON CONFLICT (email) DO NOTHING -- Basic conflict handling for email
            RETURNING id
            """,
            email, fb_uid, user_name, display_name
        )
        # If INSERT did nothing due to email conflict, fetch the existing user
        if not user_id:
             user_id = await db_conn.fetchval("SELECT id FROM users WHERE email = $1", email)

        # If still not found (e.g., conflict on firebase_uid if that's also unique), try that
        if not user_id:
             user_id = await db_conn.fetchval("SELECT id FROM users WHERE firebase_uid = $1", fb_uid)

        # If still not found, try username (if username is unique and non-null)
        if not user_id and user_name is not None:
             user_id = await db_conn.fetchval("SELECT id FROM users WHERE username = $1", user_name)


        if not user_id:
             pytest.fail(f"Failed to create or find test user {email}/{fb_uid} (suffix: {suffix}) in helper.")

        # Fetch the full record to return consistent structure, including defaults
        user_record = await db_conn.fetchrow("SELECT id, email, firebase_uid,  username, display_name, profile_picture, profile_is_public, lists_are_public, allow_analytics FROM users WHERE id = $1", user_id)
        if not user_record:
             pytest.fail(f"Failed to fetch record for user {user_id} after insertion in helper.")
        return dict(user_record) # Return as dict

    except Exception as e:
         pytest.fail(f"Error in create_test_user_direct helper for {suffix}: {e}")

async def create_test_list_direct(
    db: asyncpg.Connection,
    owner_id: int,
    name: str,
    is_private: bool | None = None,
    *,
    is_public: bool | None = None,
    description: str | None = None,
    make_unique: bool = True,
):
    """
    Accept both  `is_private`  (old style) **and**  `is_public`  (new style).
    If both are given the caller wins.
    """
    if is_private is None and is_public is None:
        raise ValueError("Either is_private or is_public must be supplied")

    # normalise to the column the DB actually stores
    is_private = bool(is_private) if is_private is not None else not bool(is_public)

    if make_unique:
        name += f" {os.urandom(2).hex()}"

    row = await db.fetchrow(
        """
        INSERT INTO lists (owner_id, name, description, is_private)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        owner_id,
        name,
        description,
        is_private,
    )
    return dict(row)

async def create_test_place_direct(
    db_conn: asyncpg.Connection, list_id: int, name: str, address: str, place_id_ext: str, # External place ID
    notes: Optional[str] = None, rating: Optional[str] = None, visit_status: Optional[str] = None,
    latitude: float = 0.0, longitude: float = 0.0
) -> Dict[str, Any]:
    """ Directly creates a place in the DB for test setup. """
    try:
        place_db_id = await db_conn.fetchval(
            """
            INSERT INTO places (list_id, place_id, name, address, latitude, longitude, rating, notes, visit_status, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW())
            ON CONFLICT (list_id, place_id) DO NOTHING -- Handle potential conflict
            RETURNING id
            """,
            list_id, place_id_ext, name, address, latitude, longitude, rating, notes, visit_status
        )
        # Refetch if conflict
        if not place_db_id:
             place_db_id = await db_conn.fetchval("SELECT id FROM places WHERE list_id = $1 AND place_id = $2", list_id, place_id_ext)

        if not place_db_id: pytest.fail(f"Failed to create/find place '{name}' (ext: {place_id_ext}) for list {list_id}")
        print(f"   [Helper] Created/Found Place DB ID: {place_db_id} in List ID: {list_id}")
        # Fetch the full record for consistency
        place_record = await db_conn.fetchrow(
            "SELECT id, list_id, place_id, name, address, latitude, longitude, rating, notes, visit_status FROM places WHERE id = $1",
            place_db_id
        )
        if not place_record:
             pytest.fail(f"Failed to fetch record for place {place_db_id} after insertion in helper.")
        return dict(place_record) # Return as dict
    except Exception as e:
         pytest.fail(f"Error in create_test_place_direct helper for {name}: {e}")


async def add_collaborator_direct(db_conn: asyncpg.Connection, list_id: int, user_id: int):
     """ Directly adds a collaborator relationship, ignoring conflicts. """
     try:
         await db_conn.execute(
             "INSERT INTO list_collaborators (list_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
             list_id, user_id
         )
         print(f"   [Helper] Ensured collaborator User ID: {user_id} on List ID: {list_id}")
     except Exception as e:
         pytest.fail(f"Error in add_collaborator_direct helper for list {list_id}, user {user_id}: {e}")

async def create_notification_direct(db_conn: asyncpg.Connection, user_id: int, title: str, message: str, is_read: bool = False, timestamp: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Creates a notification directly in DB."""
    ts = timestamp if timestamp is not None else datetime.datetime.now()
    try:
        notif_record = await db_conn.fetchrow(
            "INSERT INTO notifications (user_id, title, message, is_read, timestamp) VALUES ($1, $2, $3, $4, $5) RETURNING id, title, message, is_read, timestamp",
            user_id, title, message, is_read, ts
        )
        if not notif_record: pytest.fail(f"Failed to create notification for user {user_id}")
        print(f"   [Helper] Created Notification ID: {notif_record['id']} for User ID: {user_id}")
        return dict(notif_record) # Return as dict
    except Exception as e:
         pytest.fail(f"Error in create_notification_direct helper for user {user_id}: {e}")

async def create_follow_direct(db_conn: asyncpg.Connection, follower_id: int, followed_id: int):
     """Creates a follow relationship directly in DB."""
     try:
         await db_conn.execute(
             "INSERT INTO user_follows (follower_id, followed_id, created_at) VALUES ($1, $2, NOW()) ON CONFLICT DO NOTHING",
             follower_id, followed_id
         )
         print(f"   [Helper] Ensured Follow Exists: {follower_id} -> {followed_id}")
     except Exception as e:
          pytest.fail(f"Error in create_follow_direct helper {follower_id}->{followed_id}: {e}")