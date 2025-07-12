# backend/tests/api/test_users_api.py

import pytest
from httpx import AsyncClient
from fastapi import status
from typing import Dict, Any, Optional, List
import asyncpg
import os
import math
import asyncio # For sleep

# Import app components
from app.core.config import settings #
from app.schemas import user as user_schemas # For asserting response structure #
from app.api import deps # For mocking dependencies if needed #
from app.schemas.token import FirebaseTokenData # For mocking #
from app.crud import crud_user # Import crud_user to use check_user_exists #
from unittest.mock import patch, MagicMock # For mocking

# Import helpers from utils (these imports stay relative to tests/)
from tests.utils import (
    create_test_user_direct,
    create_follow_direct,
    create_notification_direct,
    # create_test_list_direct, # Added if needed by any user tests indirectly
    # create_test_place_direct, # Added if needed by any user tests indirectly
    # add_collaborator_direct, # Added if needed by any user tests indirectly
)

# API Prefix
API_V1 = settings.API_V1_STR

# --- REMOVED LOCAL TEST HELPER FUNCTIONS ---
# async def create_test_user(...): ... REMOVED
# async def create_follow(...): ... REMOVED
# async def create_notification(...): ... REMOVED


# =====================================================
# Test User Account & Profile Endpoints
# =====================================================
# GET /users/me
# ... (no changes needed here, it relies on deps.get_current_user_record) ...

# PATCH /users/me
# ... (error handling updated previously) ...

# DELETE /users/me
# ... (error handling updated previously) ...

# GET /users/{user_id}
# ... (error handling updated previously) ...

# GET /users/me/settings
# ... (error handling updated previously) ...

# PATCH /users/me/settings
# ... (error handling updated previously) ...

# =====================================================
# Test Username Check & Set Endpoints
# =====================================================

# test_check_username_needs_it now uses create_test_user_direct from utils and db_conn
async def test_check_username_needs_it(client: AsyncClient, test_user1: Dict[str, Any], db_conn: asyncpg.Connection, mock_auth):
    """Test GET /users/check-username - User needs to set a username."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # Use db_conn and create_test_user_direct from utils
    # test_user1 fixture already creates the user via create_test_user_direct(db_conn, ...)
    # We just need to explicitly set the username to NULL for this specific test scenario
    await db_conn.execute("UPDATE users SET username = NULL WHERE id = $1", test_user1["id"])
    # mock_auth fixture handles auth
    response = await client.get(f"{API_V1}/users/check-username")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"needsUsername": True}

# test_check_username_does_not_need_it now uses create_test_user_direct from utils and db_conn
async def test_check_username_does_not_need_it(client: AsyncClient, test_user1: Dict[str, Any], db_conn: asyncpg.Connection, mock_auth):
    """Test GET /users/check-username - User does not need to set a username."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # test_user1 fixture creates the user with a username (via create_test_user_direct).
    # We just need to ensure it's set for this test scenario.
    # It's usually set by default in create_test_user_direct, but explicitly setting is safer.
    await db_conn.execute("UPDATE users SET username = $1 WHERE id = $2", test_user1["username"], test_user1["id"])
    # mock_auth fixture handles auth
    response = await client.get(f"{API_V1}/users/check-username")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"needsUsername": False}

# test_set_username_valid now uses create_test_user_direct from utils and db_conn
async def test_set_username_valid(client: AsyncClient, test_user1: Dict[str, Any], mock_auth, db_conn: asyncpg.Connection):
    """Test POST /users/set-username - Successfully set a valid username."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth
    new_username = f"valid_user_{os.urandom(3).hex()}"
    payload = {"username": new_username}
    # Ensure user needs a username initially for this test scenario
    await db_conn.execute("UPDATE users SET username = NULL WHERE id = $1", test_user1["id"])
    response = await client.post(f"{API_V1}/users/set-username", json=payload)
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["message"] == "Username set successfully"
    db_username = await db_conn.fetchval("SELECT username FROM users WHERE id = $1", test_user1["id"])
    assert db_username == new_username

# test_set_username_too_short is a validation test, no DB or CRUD needed
async def test_set_username_too_short(client: AsyncClient, mock_auth, test_user1: Dict[str, Any]):
    """Test POST /users/set-username - Fails validation if username is too short."""
    # This test requires the client and mock_auth working.
    # mock_auth fixture handles auth
    payload = {"username": ""} # Use empty string as min_length is 1
    response = await client.post(f"{API_V1}/users/set-username", json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

async def test_set_username_invalid_chars(client: AsyncClient, mock_auth, test_user1: Dict[str, Any]):
    """Test POST /users/set-username - Fails validation if username has invalid characters."""
    # This test requires the client and mock_auth working.
    # mock_auth fixture handles auth
    payload = {"username": "invalid username!"} # Contains invalid characters
    response = await client.post(f"{API_V1}/users/set-username", json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    # Check for the specific error message related to the pattern
    errors = response.json()["errors"]
    assert any("string does not match regex" in err["msg"] for err in errors)

# test_set_username_conflict now uses create_test_user_direct from utils and db_conn
async def test_set_username_conflict(client: AsyncClient, test_user1: Dict[str, Any], test_user2: Dict[str, Any], db_conn: asyncpg.Connection, mock_auth):
    """Test POST /users/set-username - Username conflict returns 409."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1
    # Arrange: Ensure test_user2 has a specific username
    conflicting_username = f"taken_{os.urandom(3).hex()}"
    await db_conn.execute("UPDATE users SET username = $1 WHERE id = $2", conflicting_username, test_user2["id"])
    # Ensure test_user1 needs a username initially
    await db_conn.execute("UPDATE users SET username = NULL WHERE id = $1", test_user1["id"])

    payload = {"username": conflicting_username}
    response = await client.post(f"{API_V1}/users/set-username", json=payload)
    assert response.status_code == status.HTTP_409_CONFLICT
    assert "already taken" in response.json()["detail"].lower()


# =====================================================
# Test Friends/Followers Endpoints
# =====================================================

# test_get_following_pagination now uses create_test_user_direct and create_follow_direct from utils and db_conn
async def test_get_following_pagination(client: AsyncClient, test_user1: Dict[str, Any], db_conn: asyncpg.Connection, mock_auth):
    """Test /users/following - Pagination logic."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (the follower)
    follower_id = test_user1["id"]
    followed_users = []
    # Arrange: Create users and follow relationships using imported helpers and db_conn
    for i in range(5):
        # Use imported helper
        user = await create_test_user_direct(db_conn, f"following_tgt_{i}_tx")
        followed_users.append(user)
        # Use imported helper
        await create_follow_direct(db_conn, follower_id=follower_id, followed_id=user["id"])

    resp1 = await client.get(f"{API_V1}/users/following", params={"page": 1, "page_size": 2})
    assert resp1.status_code == status.HTTP_200_OK
    data1 = resp1.json()
    assert data1["total_items"] == 5
    assert data1["total_pages"] == math.ceil(5 / 2)
    assert len(data1["items"]) == 2
    ids1 = {item["id"] for item in data1["items"]}

    resp2 = await client.get(f"{API_V1}/users/following", params={"page": 2, "page_size": 2})
    assert resp2.status_code == status.HTTP_200_OK
    data2 = resp2.json()
    assert len(data2["items"]) == 2
    ids2 = {item["id"] for item in data2["items"]}

    resp3 = await client.get(f"{API_V1}/users/following", params={"page": 3, "page_size": 2})
    assert resp3.status_code == status.HTTP_200_OK
    data3 = resp3.json()
    assert len(data3["items"]) == 1
    ids3 = {item["id"] for item in data3["items"]}


    all_retrieved_ids = ids1.union(ids2).union(ids3)
    expected_ids = {u["id"] for u in followed_users}
    assert all_retrieved_ids == expected_ids
    # The `db_conn` fixture will truncate tables, handling cleanup


# test_get_followers_pagination_and_following_flag now uses create_test_user_direct and create_follow_direct from utils and db_conn
async def test_get_followers_pagination_and_following_flag(client: AsyncClient, test_user1: Dict[str, Any], db_conn: asyncpg.Connection, mock_auth):
    """Test /users/followers - Pagination and check is_following flag."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (the user whose followers are listed)
    user_id = test_user1["id"]
    followers = []
    followed_back_ids = set()
    # Arrange: Create follower users and relationships using imported helpers and db_conn
    for i in range(4):
        follower = await create_test_user_direct(db_conn, f"follower_{i}_tx")
        followers.append(follower)
        await create_follow_direct(db_conn, follower_id=follower["id"], followed_id=user_id) # This user follows user_id
        if i % 2 == 0:
             # user_id follows this follower back
             await create_follow_direct(db_conn, follower_id=user_id, followed_id=follower["id"])
             followed_back_ids.add(follower["id"])

    resp1 = await client.get(f"{API_V1}/users/followers", params={"page": 1, "page_size": 3})
    assert resp1.status_code == status.HTTP_200_OK
    data1 = resp1.json()
    assert data1["total_items"] == 4
    assert data1["total_pages"] == math.ceil(4 / 3)
    assert len(data1["items"]) == 3
    for item in data1["items"]: assert item["is_following"] == (item["id"] in followed_back_ids)
    ids1 = {item["id"] for item in data1["items"]}

    resp2 = await client.get(f"{API_V1}/users/followers", params={"page": 2, "page_size": 3})
    assert resp2.status_code == status.HTTP_200_OK
    data2 = resp2.json()
    assert len(data2["items"]) == 1
    for item in data2["items"]: assert item["is_following"] == (item["id"] in followed_back_ids)
    ids2 = {item["id"] for item in data2["items"]}

    all_retrieved_ids = ids1.union(ids2)
    expected_ids = {f["id"] for f in followers}
    assert all_retrieved_ids == expected_ids
    # The `db_conn` fixture will truncate tables, handling cleanup


# test_search_users_pagination now uses create_test_user_direct from utils and db_conn
async def test_search_users_pagination(client: AsyncClient, test_user1: Dict[str, Any], db_conn: asyncpg.Connection, mock_auth):
    """Test /users/search - Pagination."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (the searcher)
    searcher_id = test_user1["id"]
    matching_users = []
    non_matching = None
    # Arrange: Create users using imported helpers and db_conn
    for i in range(3):
        user = await create_test_user_direct(db_conn, f"searchme_{i}_tx")
        matching_users.append(user)
    non_matching = await create_test_user_direct(db_conn, "dontfindme_tx")

    resp1 = await client.get(f"{API_V1}/users/search", params={"q": "searchme", "page": 1, "page_size": 2})
    assert resp1.status_code == status.HTTP_200_OK
    data1 = resp1.json()
    assert data1["total_items"] == 3
    assert data1["total_pages"] == math.ceil(3 / 2)
    assert len(data1["items"]) == 2
    ids1 = {item["id"] for item in data1["items"]}

    resp2 = await client.get(f"{API_V1}/users/search", params={"q": "searchme", "page": 2, "page_size": 2})
    assert resp2.status_code == status.HTTP_200_OK
    data2 = resp2.json()
    assert len(data2["items"]) == 1
    ids2 = {item["id"] for item in data2["items"]}

    all_retrieved_ids = ids1.union(ids2)
    expected_ids = {u["id"] for u in matching_users}
    assert all_retrieved_ids == expected_ids
    assert non_matching["id"] not in all_retrieved_ids
    assert searcher_id not in all_retrieved_ids # Ensure self is excluded
    # The `db_conn` fixture will truncate tables, handling cleanup


# test_search_users_no_results no DB setup needed beyond fixtures
async def test_search_users_no_results(client: AsyncClient, test_user1: Dict[str, Any], mock_auth):
    """Test /users/search - No results found."""
    # This test requires the client and mock_auth working.
    # mock_auth handles auth
    response = await client.get(f"{API_V1}/users/search", params={"q": "willnotmatchanything"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["items"] == []
    assert data["total_items"] == 0


# test_follow_user_already_following now uses create_follow_direct from utils and db_conn
async def test_follow_user_already_following(client: AsyncClient, test_user1, test_user2, db_conn: asyncpg.Connection, mock_auth):
    """Test POST /users/{user_id}/follow - Already following returns 200 OK."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (the follower)
    user_to_follow_id = test_user2["id"]
    # Arrange: Create follow relationship using imported helper and db_conn
    await create_follow_direct(db_conn, follower_id=test_user1["id"], followed_id=user_to_follow_id)
    response = await client.post(f"{API_V1}/users/{user_to_follow_id}/follow")
    assert response.status_code == status.HTTP_200_OK # Expected status for "already following"
    assert "already following" in response.json()["message"].lower()
    # The `db_conn` fixture will truncate tables, handling cleanup


# test_follow_user_not_found no DB setup needed beyond fixtures
async def test_follow_user_not_found(client: AsyncClient, test_user1, mock_auth):
    """Test POST /users/{user_id}/follow - Target user not found returns 404."""
    # This test requires the client and mock_auth working.
    # mock_auth handles auth for test_user1
    non_existent_user_id = 99998
    response = await client.post(f"{API_V1}/users/{non_existent_user_id}/follow")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "user to follow not found" in response.json()["detail"].lower()


# test_follow_user_self no DB setup needed beyond fixtures
async def test_follow_user_self(client: AsyncClient, test_user1, mock_auth):
    """Test POST /users/{user_id}/follow - Trying to follow self returns 400."""
    # This test requires the client and mock_auth working.
    # mock_auth handles auth
    self_id = test_user1["id"]
    response = await client.post(f"{API_V1}/users/{self_id}/follow")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "cannot follow yourself" in response.json()["detail"].lower()


# test_unfollow_user_not_following no DB setup needed beyond fixtures
async def test_unfollow_user_not_following(client: AsyncClient, test_user1, test_user2, mock_auth):
    """Test DELETE /users/{user_id}/follow - Not following returns 200 OK."""
    # This test requires the client and mock_auth working.
    # mock_auth handles auth for test_user1 (the unfollower)
    user_to_unfollow_id = test_user2["id"]
    # Ensure no follow relationship exists (default state after fixture cleanup/rollback)
    response = await client.delete(f"{API_V1}/users/{user_to_unfollow_id}/follow")
    assert response.status_code == status.HTTP_200_OK # Expected status for "not following"
    assert "not following this user" in response.json()["message"].lower()


# test_unfollow_user_not_found no DB setup needed beyond fixtures
async def test_unfollow_user_not_found(client: AsyncClient, test_user1, mock_auth):
    """Test DELETE /users/{user_id}/follow - Target user not found returns 404."""
    # This test requires the client and mock_auth working.
    # mock_auth handles auth for test_user1
    non_existent_user_id = 99997
    response = await client.delete(f"{API_V1}/users/{non_existent_user_id}/follow")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "user to unfollow not found" in response.json()["detail"].lower()


# test_unfollow_user_success now uses create_follow_direct from utils and db_conn
async def test_unfollow_user_success(client: AsyncClient, test_user1, test_user2, db_conn: asyncpg.Connection, mock_auth):
    """Test DELETE /users/{user_id}/follow - Success."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (the unfollower)
    user_to_unfollow_id = test_user2["id"]
    # Arrange: Create follow relationship using imported helper and db_conn
    await create_follow_direct(db_conn, follower_id=test_user1["id"], followed_id=user_to_unfollow_id)
    # Verify relationship exists initially
    exists = await db_conn.fetchval("SELECT EXISTS(SELECT 1 FROM user_follows WHERE follower_id=$1 AND followed_id=$2)", test_user1["id"], user_to_unfollow_id)
    assert exists is True

    # Act
    response = await client.delete(f"{API_V1}/users/{user_to_unfollow_id}/follow")

    # Assert
    assert response.status_code == status.HTTP_200_OK # Expected status for successful unfollow
    assert "user unfollowed" in response.json()["message"].lower()
    # Verify relationship is gone in DB
    exists_after = await db_conn.fetchval("SELECT EXISTS(SELECT 1 FROM user_follows WHERE follower_id=$1 AND followed_id=$2)", test_user1["id"], user_to_unfollow_id)
    assert exists_after is False
    # The `db_conn` fixture will truncate tables, handling cleanup


# =====================================================
# Test Notification Endpoints
# =====================================================

# test_get_notifications_empty no DB setup needed beyond fixtures
async def test_get_notifications_empty(client: AsyncClient, test_user1, mock_auth):
    """Test GET /notifications - Empty list when no notifications exist."""
    # This test requires the client and mock_auth working.
    # mock_auth handles auth for test_user1
    response = await client.get(f"{API_V1}/notifications")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["items"] == []
    assert data["total_items"] == 0

# test_get_notifications_pagination now uses create_notification_direct from utils and db_conn
async def test_get_notifications_pagination(client: AsyncClient, test_user1, db_conn: asyncpg.Connection, mock_auth):
    """Test GET /notifications - Pagination logic."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1
    user_id = test_user1["id"]
    notif_ids = []
    # Arrange: Create notifications using imported helper and db_conn
    for i in range(5):
        # Use imported helper
        notif_data = await create_notification_direct(db_conn, user_id, f"Title {i}", f"Message {i}")
        notif_ids.append(notif_data["id"])
        await asyncio.sleep(0.01) # Ensure distinct timestamps if ordering relies on it

    # Note: Ordering is timestamp DESC in crud_user.get_user_notifications
    resp1 = await client.get(f"{API_V1}/notifications", params={"page": 1, "page_size": 3})
    assert resp1.status_code == status.HTTP_200_OK
    data1 = resp1.json()
    assert data1["total_items"] == 5
    assert data1["total_pages"] == math.ceil(5 / 3)
    assert len(data1["items"]) == 3
    ids1 = {item["id"] for item in data1["items"]}
    # N4, N3, N2, N1, N0 (by creation time) --> IDs: notif_ids[4], notif_ids[3], notif_ids[2], notif_ids[1], notif_ids[0]
    # Page 1 (size 3): N4, N3, N2 --> IDs: notif_ids[4], notif_ids[3], notif_ids[2]
    assert data1["items"][0]["id"] == notif_ids[4]
    assert data1["items"][1]["id"] == notif_ids[3]
    assert data1["items"][2]["id"] == notif_ids[2]


    resp2 = await client.get(f"{API_V1}/notifications", params={"page": 2, "page_size": 3})
    assert resp2.status_code == status.HTTP_200_OK
    data2 = resp2.json()
    assert len(data2["items"]) == 2
    assert data2["items"][0]["id"] == notif_ids[1]
    assert data2["items"][1]["id"] == notif_ids[0]


    all_retrieved_ids = ids1.union(set(item["id"] for item in data2["items"])) # Union with ids from data2
    assert all_retrieved_ids == set(notif_ids)
    # The `db_conn` fixture will truncate tables, handling cleanup


async def test_get_notifications_unauthenticated(client: AsyncClient):
    """Test GET /notifications - Fails without authentication."""
    # This test requires the client working.
    response = await client.get(f"{API_V1}/notifications")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED