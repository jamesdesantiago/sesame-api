# backend/tests/api/test_lists_api.py

import pytest
from httpx import AsyncClient
from fastapi import status
from typing import Dict, Any, Optional, List
import asyncpg
import os
import math # For pagination checks

# Import app components
from main import app # <-- ADDED THIS IMPORT
from app.core.config import settings
from app.schemas import list as list_schemas
from app.schemas import place as place_schemas
from app.schemas import user as user_schemas # Needed for collaborator response
from app.api import deps # For mocking dependencies if needed
from app.schemas.token import FirebaseTokenData # For mocking
from app.crud import crud_list, crud_place, crud_user # To check DB state if needed
from unittest.mock import patch

# Import helpers from utils (these imports stay relative to tests/)
from tests.utils import (
    create_test_list_direct,
    create_test_place_direct,
    add_collaborator_direct,
    create_test_user_direct # Assuming this is also in utils if not using conftest fixtures
)

# API Prefix from settings
API_V1_LISTS = f"{settings.API_V1_STR}/lists" # Base path for this router

# =====================================================
# Test List CRUD Endpoints (POST /, GET /, GET /{id}, PATCH /{id}, DELETE /{id})
# =====================================================

@pytest.mark.parametrize("is_private", [True, False], ids=["Private List", "Public List"])
async def test_create_list_success(client: AsyncClient, mock_auth, test_user1: Dict[str, Any], is_private: bool, db_conn: asyncpg.Connection):
    """Test POST /lists - Creating a list successfully."""
    # This test requires the DB pool initialized and the db_conn fixture working.
    # mock_auth fixture handles setting auth header implicitly via dependency override
    list_name = f"My API New {'Private' if is_private else 'Public'} List {os.urandom(2).hex()}"
    list_desc = "Created via API test"
    payload = {"name": list_name, "description": list_desc, "isPrivate": is_private}

    response = await client.post(API_V1_LISTS, json=payload)

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["name"] == list_name
    assert data["description"] == list_desc
    assert data["isPrivate"] == is_private
    assert "id" in data
    list_id = data["id"]
    assert data["collaborators"] == [] # New lists should have no collaborators initially

    # Verify in DB (optional but good)
    db_list = await db_conn.fetchrow("SELECT owner_id, name, description, is_private FROM lists WHERE id = $1", list_id)
    assert db_list is not None
    assert db_list["owner_id"] == test_user1["id"]
    assert db_list["name"] == list_name
    assert db_list["is_private"] == is_private
    # Cleanup is handled by the `db_conn` fixture which truncates tables

async def test_create_list_missing_name(client: AsyncClient, mock_auth, test_user1: Dict[str, Any]):
    """Test POST /lists - Fails validation if required 'name' is missing."""
    # This test requires the DB pool initialized and the client working.
    # mock_auth fixture handles auth
    payload = {"description": "List without a name", "isPrivate": False}
    response = await client.post(API_V1_LISTS, json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

async def test_create_list_unauthenticated(client: AsyncClient):
    """Test POST /lists - Fails without authentication."""
    # This test requires the DB pool initialized and the client working.
    payload = {"name": "Unauthorized List", "isPrivate": False}
    response = await client.post(API_V1_LISTS, json=payload)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED

async def test_get_user_lists_empty(client: AsyncClient, mock_auth, test_user1: Dict[str, Any]):
    """Test GET /lists - User has no lists."""
    # This test requires the DB pool initialized and the client/auth working.
    # mock_auth fixture handles auth for test_user1
    response = await client.get(API_V1_LISTS) # Get base path for user lists
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["items"] == []
    assert data["total_items"] == 0

async def test_get_user_lists_pagination(client: AsyncClient, db_conn: asyncpg.Connection, mock_auth, test_user1: Dict[str, Any], test_user2: Dict[str, Any]):
    """Test GET /lists - Pagination and ownership check."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth fixture handles auth for test_user1
    user1_lists = []
    user2_list = None
    try:
        for i in range(3): # User1 has 3 lists
            # Use imported helper
            # Set make_unique=False for a consistent suffix within the loop if needed, but os.urandom is fine
            lst = await create_test_list_direct(db_conn, test_user1["id"], f"U1 List {i} {os.urandom(2).hex()}", i % 2 == 0)
            user1_lists.append(lst)
        # User2 has 1 list (should not be returned)
        # Use imported helper
        user2_list = await create_test_list_direct(db_conn, test_user2["id"], "U2 List 0", False)

        # Note: Default order is created_at DESC in crud_list.get_user_lists_paginated.
        # Lists should be returned in reverse order of creation within the loop.
        response1 = await client.get(API_V1_LISTS, params={"page": 1, "page_size": 2})
        assert response1.status_code == status.HTTP_200_OK
        data1 = response1.json()
        assert data1["total_items"] == 3 # Only user1's lists counted
        assert data1["total_pages"] == math.ceil(3 / 2)
        assert len(data1["items"]) == 2
        # Verify order based on creation time DESC
        assert data1["items"][0]["id"] == user1_lists[2]["id"]
        assert data1["items"][1]["id"] == user1_lists[1]["id"]
        # Ensure place_count is included
        assert "place_count" in data1["items"][0]
        assert "place_count" in data1["items"][1]


        response2 = await client.get(API_V1_LISTS, params={"page": 2, "page_size": 2})
        assert response2.status_code == status.HTTP_200_OK
        data2 = response2.json()
        assert len(data2["items"]) == 1
        assert data2["items"][0]["id"] == user1_lists[0]["id"] # The oldest list
        assert "place_count" in data2["items"][0]


        all_retrieved_ids = {item["id"] for page_data in [data1, data2] for item in page_data["items"]}
        expected_ids = {lst["id"] for lst in user1_lists}
        assert all_retrieved_ids == expected_ids
        assert user2_list["id"] not in all_retrieved_ids

    finally:
        # Cleanup is handled by db_conn fixture (table truncation)
        pass # Explicit pass since cleanup is handled

async def test_get_list_detail_success_owner(client: AsyncClient, mock_auth, test_list1: Dict[str, Any], test_user1: Dict[str, Any], test_user2: Dict[str, Any], db_conn: asyncpg.Connection):
    """Test GET /lists/{list_id} - Success for owner, includes collaborators."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # Arrange: Add collaborator using imported helper
    await add_collaborator_direct(db_conn, test_list1["id"], test_user2["id"])
    # mock_auth fixture handles auth for test_user1 (the owner)
    list_id = test_list1["id"]

    response = await client.get(f"{API_V1_LISTS}/{list_id}")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == list_id
    assert data["name"] == test_list1["name"]
    assert test_user2["email"] in data["collaborators"] # Check collaborator email
    assert len(data["collaborators"]) == 1

async def test_get_list_detail_success_collaborator(client: AsyncClient, test_user1: Dict[str, Any], test_user2: Dict[str, Any], db_conn: asyncpg.Connection):
    """Test GET /lists/{list_id} - Success for collaborator on private list."""
    # This test requires the DB pool initialized, db_conn working.
    # Arrange: Create a private list owned by user1, add user2 as collaborator
    private_list = await create_test_list_direct(db_conn, test_user1["id"], "Collab Test Private List", True)
    list_id = private_list["id"]
    await add_collaborator_direct(db_conn, list_id, test_user2["id"])

    # Mock auth for user2 (the collaborator)
    mock_token_user2 = FirebaseTokenData(uid=test_user2["firebase_uid"], email=test_user2["email"])
    async def override_auth_user2(): return mock_token_user2
    app.dependency_overrides[deps.get_verified_token_data] = override_auth_user2

    # Act: User2 fetches the list
    response = await client.get(f"{API_V1_LISTS}/{list_id}")

    # Cleanup mock
    app.dependency_overrides.clear()

    # Assert: Should be successful
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == list_id
    assert data["isPrivate"] is True
    assert test_user2["email"] in data["collaborators"] # User2 should see themselves listed

async def test_get_list_detail_forbidden(client: AsyncClient, mock_auth, test_user1: Dict[str, Any], test_user2: Dict[str, Any], db_conn: asyncpg.Connection):
    """Test GET /lists/{list_id} - Forbidden for non-owner/collaborator of private list."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # Arrange: Create a private list owned by user2
    list_data = await create_test_list_direct(db_conn, test_user2["id"], "Other User Private List", True)
    list_id = list_data["id"]
    # mock_auth fixture handles auth for test_user1 (not owner/collaborator)

    # Act
    response = await client.get(f"{API_V1_LISTS}/{list_id}")

    # Assert
    assert response.status_code == status.HTTP_403_FORBIDDEN

async def test_get_list_detail_not_found(client: AsyncClient, mock_auth, test_user1: Dict[str, Any]):
    """Test GET /lists/{list_id} - List does not exist."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth fixture handles auth for test_user1
    response = await client.get(f"{API_V1_LISTS}/99999")
    assert response.status_code == status.HTTP_404_NOT_FOUND

async def test_update_list_success(client: AsyncClient, mock_auth, test_list1: Dict[str, Any], db_conn: asyncpg.Connection):
    """Test PATCH /lists/{list_id} - Success."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # Arrange: Ensure test_list1 is public initially
    list_id = test_list1["id"]
    await db_conn.execute("UPDATE lists SET is_private = FALSE WHERE id = $1", list_id)
    # mock_auth fixture handles auth for test_user1 (the owner)

    new_name = f"Updated List Name {os.urandom(2).hex()}"
    payload = {"name": new_name, "isPrivate": True}

    # Act
    response = await client.patch(f"{API_V1_LISTS}/{list_id}", json=payload)

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["name"] == new_name
    assert data["isPrivate"] is True
    assert "collaborators" in data # Collaborators should be included in response (empty initially)

    # Verify in DB
    db_list = await db_conn.fetchrow("SELECT name, is_private FROM lists WHERE id = $1", list_id)
    assert db_list["name"] == new_name
    assert db_list["is_private"] is True

async def test_update_list_forbidden(client: AsyncClient, mock_auth, test_user1: Dict[str, Any], test_user2: Dict[str, Any], db_conn: asyncpg.Connection):
    """Test PATCH /lists/{list_id} - Non-owner cannot update."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # Arrange: Create list owned by user2
    list_data = await create_test_list_direct(db_conn, test_user2["id"], "Another User List", False)
    list_id = list_data["id"]
    # mock_auth fixture handles auth for test_user1 (not owner)
    payload = {"name": "Hacked!"}

    # Act
    response = await client.patch(f"{API_V1_LISTS}/{list_id}", json=payload)

    # Assert
    assert response.status_code == status.HTTP_403_FORBIDDEN

async def test_delete_list_success(client: AsyncClient, mock_auth, test_list1: Dict[str, Any], db_conn: asyncpg.Connection):
    """Test DELETE /lists/{list_id} - Success."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    list_id = test_list1["id"]
    # Arrange: Add a place to ensure cascade works
    await create_test_place_direct(db_conn, list_id, "Place in deleted list", "Addr", f"ext_del_list_{os.urandom(3).hex()}")
    assert await db_conn.fetchval("SELECT EXISTS (SELECT 1 FROM lists WHERE id = $1)", list_id)
    assert await db_conn.fetchval("SELECT COUNT(*) FROM places WHERE list_id = $1", list_id) > 0

    # Act (mock_auth handles auth)
    del_response = await client.delete(f"{API_V1_LISTS}/{list_id}")

    # Assert
    assert del_response.status_code == status.HTTP_204_NO_CONTENT
    # Verify deleted in DB
    assert not await db_conn.fetchval("SELECT EXISTS (SELECT 1 FROM lists WHERE id = $1)", list_id)
    assert await db_conn.fetchval("SELECT COUNT(*) FROM places WHERE list_id = $1", list_id) == 0 # Verify cascade

async def test_delete_list_forbidden(client: AsyncClient, mock_auth, test_user1: Dict[str, Any], test_user2: Dict[str, Any], db_conn: asyncpg.Connection):
    """Test DELETE /lists/{list_id} - Non-owner cannot delete."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # Arrange: Create list owned by user2
    list_data = await create_test_list_direct(db_conn, test_user2["id"], "Another User List To Delete", False)
    list_id = list_data["id"]
    # mock_auth handles auth for test_user1 (not owner)
    response = await client.delete(f"{API_V1_LISTS}/{list_id}")
    assert response.status_code == status.HTTP_403_FORBIDDEN

# =====================================================
# Test Collaborator Endpoints
# =====================================================
async def test_add_collaborator_success(client: AsyncClient, mock_auth, test_list1: Dict[str, Any], test_user2: Dict[str, Any], db_conn: asyncpg.Connection):
    """Test POST /{list_id}/collaborators - Success adding."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (the owner)
    list_id = test_list1["id"]
    collaborator_email = test_user2["email"]
    payload = {"email": collaborator_email}

    # Act
    response = await client.post(f"{API_V1_LISTS}/{list_id}/collaborators", json=payload)

    # Assert
    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["message"] == "Collaborator added"

    # Verify in DB
    is_collab = await db_conn.fetchval("SELECT EXISTS(SELECT 1 FROM list_collaborators WHERE list_id = $1 AND user_id = $2)", list_id, test_user2["id"])
    assert is_collab is True

    # Verify by getting list details via API (requires owner or collaborator access)
    detail_response = await client.get(f"{API_V1_LISTS}/{list_id}") # Owner gets details
    assert test_user2["email"] in detail_response.json()["collaborators"]

async def test_add_collaborator_already_exists(client: AsyncClient, mock_auth, test_list1: Dict[str, Any], test_user2: Dict[str, Any], db_conn: asyncpg.Connection):
    """Test POST /{list_id}/collaborators - Collaborator already present."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (the owner)
    list_id = test_list1["id"]
    collaborator_email = test_user2["email"]
    # Arrange: Add first using imported helper
    await add_collaborator_direct(db_conn, list_id, test_user2["id"])
    payload = {"email": collaborator_email}

    # Act
    response = await client.post(f"{API_V1_LISTS}/{list_id}/collaborators", json=payload)

    # Assert
    assert response.status_code == status.HTTP_409_CONFLICT
    assert "already a collaborator" in response.json()["detail"].lower()

async def test_add_collaborator_owner_is_collaborator(client: AsyncClient, mock_auth, test_list1: Dict[str, Any], test_user1: Dict[str, Any]):
    """Test POST /{list_id}/collaborators - Cannot add owner as collaborator."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (the owner)
    list_id = test_list1["id"]
    owner_email = test_user1["email"] # Owner's email
    payload = {"email": owner_email}

    # Act
    response = await client.post(f"{API_V1_LISTS}/{list_id}/collaborators", json=payload)

    # Assert
    # CRUD now raises CollaboratorAlreadyExistsError if owner email is used because it finds the user ID
    # and the check for collaboration (is_owner or is_collaborator) passes, triggering the error.
    assert response.status_code == status.HTTP_409_CONFLICT
    assert "already a collaborator" in response.json()["detail"].lower()

async def test_delete_collaborator_success(client: AsyncClient, mock_auth, test_list1: Dict[str, Any], test_user2: Dict[str, Any], db_conn: asyncpg.Connection):
    """Test DELETE /{list_id}/collaborators/{user_id} - Success."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (the owner)
    list_id = test_list1["id"]
    collaborator_id_to_remove = test_user2["id"]
    # Arrange: Add first using imported helper
    await add_collaborator_direct(db_conn, list_id, collaborator_id_to_remove)
    assert await db_conn.fetchval("SELECT EXISTS (SELECT 1 FROM list_collaborators WHERE list_id = $1 AND user_id = $2)", list_id, collaborator_id_to_remove)

    # Act
    response = await client.delete(f"{API_V1_LISTS}/{list_id}/collaborators/{collaborator_id_to_remove}")

    # Assert
    assert response.status_code == status.HTTP_204_NO_CONTENT
    # Verify deleted in DB
    assert not await db_conn.fetchval("SELECT EXISTS (SELECT 1 FROM list_collaborators WHERE list_id = $1 AND user_id = $2)", list_id, collaborator_id_to_remove)

async def test_delete_collaborator_forbidden(client: AsyncClient, test_user1: Dict[str, Any], test_user2: Dict[str, Any], db_conn: asyncpg.Connection):
     """Test DELETE /{list_id}/collaborators/{user_id} - Non-owner cannot remove."""
     # This test requires the DB pool initialized, db_conn working.
     # Arrange: Create a list owned by user1, add user2 as collaborator
     list_data = await create_test_list_direct(db_conn, test_user1["id"], "List for Collab Deletion", False)
     list_id = list_data["id"]
     await add_collaborator_direct(db_conn, list_id, test_user2["id"])

     # Mock auth for user2 (the collaborator, who is not the owner)
     mock_token_user2 = FirebaseTokenData(uid=test_user2["firebase_uid"], email=test_user2["email"])
     async def override_auth_user2(): return mock_token_user2
     app.dependency_overrides[deps.get_verified_token_data] = override_auth_user2

     # Act: User2 tries to remove user2 (or anyone) as collaborator
     response = await client.delete(f"{API_V1_LISTS}/{list_id}/collaborators/{test_user2['id']}")

     # Cleanup mock
     app.dependency_overrides.clear()

     # Assert
     assert response.status_code == status.HTTP_403_FORBIDDEN

async def test_delete_collaborator_not_found(client: AsyncClient, mock_auth, test_list1: Dict[str, Any], db_conn: asyncpg.Connection):
    """Test DELETE /{list_id}/collaborators/{user_id} - Collaborator user not on list."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (owner)
    list_id = test_list1["id"]
    # Arrange: Create a user who is NOT a collaborator on test_list1
    non_collab_user = await create_test_user_direct(db_conn, "non_collab_del_tx")
    non_collab_user_id = non_collab_user["id"]

    # Ensure this user is NOT a collaborator (default state from creation, but double-check)
    await db_conn.execute("DELETE FROM list_collaborators WHERE list_id = $1 AND user_id = $2", list_id, non_collab_user_id)

    # Act
    response = await client.delete(f"{API_V1_LISTS}/{list_id}/collaborators/{non_collab_user_id}")

    # Assert
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "User is not a collaborator on this list" in response.json()["detail"]

async def test_delete_collaborator_non_existent_user(client: AsyncClient, mock_auth, test_list1: Dict[str, Any]):
    """Test DELETE /{list_id}/collaborators/{user_id} - Collaborator user ID does not exist at all."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (owner)
    list_id = test_list1["id"]
    non_existent_user_id = 99999 # Assuming this ID does not exist

    # Act
    response = await client.delete(f"{API_V1_LISTS}/{list_id}/collaborators/{non_existent_user_id}")

    # Assert
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "Collaborator user not found" in response.json()["detail"]

async def test_delete_collaborator_owner_cannot_remove_self(client: AsyncClient, mock_auth, test_list1: Dict[str, Any], test_user1: Dict[str, Any]):
    """Test DELETE /{list_id}/collaborators/{user_id} - Owner cannot remove themselves."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (the owner)
    list_id = test_list1["id"]
    owner_user_id = test_user1["id"]

    # Act
    response = await client.delete(f"{API_V1_LISTS}/{list_id}/collaborators/{owner_user_id}")

    # Assert
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "cannot remove the list owner" in response.json()["detail"].lower()

# =====================================================
# Test Place within List Endpoints
# =====================================================
async def test_get_places_in_list_empty(client: AsyncClient, mock_auth, test_list1: Dict[str, Any]):
    """Test GET /{list_id}/places - Empty list."""
    list_id = test_list1["id"]
    response = await client.get(f"{API_V1_LISTS}/{list_id}/places")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["items"] == []
    assert data["total_items"] == 0

async def test_add_and_get_places_in_list(client: AsyncClient, mock_auth, test_list1: Dict[str, Any]):
    """Test POST + GET /{list_id}/places - Add and retrieve places with pagination."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (owner)
    list_id = test_list1["id"]
    places_created = []
    # Arrange: Add two places using imported helper and db_conn
    # Ensure unique external place IDs for each place addition within the list
    payload1 = {"placeId": f"ext1_{os.urandom(2).hex()}", "name": f"Place A {os.urandom(2).hex()}", "address": "1 Main St", "latitude": 10.0, "longitude": -10.0}
    payload2 = {"placeId": f"ext2_{os.urandom(2).hex()}", "name": f"Place B {os.urandom(2).hex()}", "address": "2 Side St", "latitude": 20.0, "longitude": -20.0}
    
    add_resp1 = await client.post(f"{API_V1_LISTS}/{list_id}/places", json=payload1)
    assert add_resp1.status_code == status.HTTP_201_CREATED
    places_created.append(add_resp1.json()["id"])
    add_resp2 = await client.post(f"{API_V1_LISTS}/{list_id}/places", json=payload2)
    assert add_resp2.status_code == status.HTTP_201_CREATED
    places_created.append(add_resp2.json()["id"])

    # Act & Assert: Get page 1, size 1
    response_p1 = await client.get(f"{API_V1_LISTS}/{list_id}/places", params={"page": 1, "page_size": 1})
    assert response_p1.status_code == status.HTTP_200_OK
    data_p1 = response_p1.json()
    assert data_p1["total_items"] == 2
    assert data_p1["total_pages"] == math.ceil(2 / 1)
    assert len(data_p1["items"]) == 1
    # Assuming newest first ordering in CRUD:
    assert data_p1["items"][0]["id"] == places_created[-1]
    assert data_p1["items"][0]["name"] == payload2["name"]

    # Act & Assert: Get page 2, size 1
    response_p2 = await client.get(f"{API_V1_LISTS}/{list_id}/places", params={"page": 2, "page_size": 1})
    assert response_p2.status_code == status.HTTP_200_OK
    data_p2 = response_p2.json()
    assert len(data_p2["items"]) == 1
    assert data_p2["items"][0]["id"] == places_created[0] # The oldest place
    assert data_p2["items"][0]["name"] == payload1["name"]

async def test_add_place_duplicate_external_id(client: AsyncClient, mock_auth, test_list1: Dict[str, Any]):
    """Test POST /{list_id}/places - Duplicate external place ID returns 409."""
    # This test requires the DB pool initialized and the client/auth working.
    # mock_auth handles auth for test_user1 (owner)
    list_id = test_list1["id"]
    # Use a unique external ID for this test instance
    external_place_id = f"ext_dup_{os.urandom(3).hex()}"
    payload = {"placeId": external_place_id, "name": "Duplicate Place", "address": "1 Dup St", "latitude": 1, "longitude": 1}
    add_resp1 = await client.post(f"{API_V1_LISTS}/{list_id}/places", json=payload)
    assert add_resp1.status_code == status.HTTP_201_CREATED
    # Attempt to add again with the same list_id and placeId
    add_resp2 = await client.post(f"{API_V1_LISTS}/{list_id}/places", json=payload)
    assert add_resp2.status_code == status.HTTP_409_CONFLICT
    assert "Place already exists in this list" in add_resp2.json()["detail"]

async def test_update_place_notes_success(client: AsyncClient, mock_auth, test_list1: Dict[str, Any], db_conn: asyncpg.Connection):
    """Test PATCH /{list_id}/places/{place_id} - Success updating notes."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (owner)
    list_id = test_list1["id"]
    # Arrange: Create a place using imported helper and db_conn
    place_data = await create_test_place_direct(db_conn, list_id, "Place To Update Notes", "Addr", f"ext_upd_notes_{os.urandom(3).hex()}", notes="Initial notes.")
    place_db_id = place_data["id"]
    new_notes = "These are the final updated notes."
    payload = {"notes": new_notes} # Using the PlaceUpdate schema structure

    # Act
    response = await client.patch(f"{API_V1_LISTS}/{list_id}/places/{place_db_id}", json=payload)

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == place_db_id
    assert data["notes"] == new_notes
    assert data["name"] == place_data["name"] # Other fields should be returned

    # Verify in DB
    db_place = await db_conn.fetchrow("SELECT notes FROM places WHERE id = $1", place_db_id)
    assert db_place["notes"] == new_notes

async def test_update_place_partial_success(client: AsyncClient, mock_auth, test_list1: Dict[str, Any], db_conn: asyncpg.Connection):
    """Test PATCH /{list_id}/places/{place_id} - Success updating only one field (notes)."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (owner)
    list_id = test_list1["id"]
    # Arrange: Create a place with initial notes and rating
    place_data = await create_test_place_direct(db_conn, list_id, "Place Partial Update", "Addr", f"ext_partial_upd_{os.urandom(3).hex()}", notes="Initial notes.", rating="MUST_VISIT")
    place_db_id = place_data["id"]
    new_notes = "Only update notes."
    payload = {"notes": new_notes} # Only provide notes

    # Act
    response = await client.patch(f"{API_V1_LISTS}/{list_id}/places/{place_db_id}", json=payload)

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == place_db_id
    assert data["notes"] == new_notes
    assert data["rating"] == place_data["rating"] # Rating should be unchanged
    assert data["name"] == place_data["name"] # Other fields should be returned

    # Verify in DB
    db_place = await db_conn.fetchrow("SELECT notes, rating FROM places WHERE id = $1", place_db_id)
    assert db_place["notes"] == new_notes
    assert db_place["rating"] == place_data["rating"]

async def test_update_place_forbidden(client: AsyncClient, test_user1: Dict[str, Any], test_user2: Dict[str, Any], db_conn: asyncpg.Connection):
     """Test PATCH /{list_id}/places/{place_id} - Forbidden for non-owner/collaborator."""
     # This test requires the DB pool initialized, db_conn working.
     # Arrange: Create list owned by user2, add a place to it
     list_data = await create_test_list_direct(db_conn, test_user2["id"], "List Other User Update Place", False)
     place_data = await create_test_place_direct(db_conn, list_data["id"], "Other Place", "Addr", f"ext_other_upd_{os.urandom(3).hex()}")
     list_id = list_data["id"]
     place_db_id = place_data["id"]

     # Mock auth for user1 (not owner/collaborator)
     mock_token_user1 = FirebaseTokenData(uid=test_user1["firebase_uid"], email=test_user1["email"])
     async def override_auth(): return mock_token_user1
     app.dependency_overrides[deps.get_verified_token_data] = override_auth

     payload = {"notes": "Attempted update"}

     # Act
     response = await client.patch(f"{API_V1_LISTS}/{list_id}/places/{place_db_id}", json=payload)

     # Cleanup mock
     app.dependency_overrides.clear()

     # Assert
     assert response.status_code == status.HTTP_403_FORBIDDEN

async def test_delete_place_success(client: AsyncClient, mock_auth, test_list1: Dict[str, Any], db_conn: asyncpg.Connection):
     """Test DELETE /{list_id}/places/{place_id} - Success."""
     # This test requires the DB pool initialized, db_conn working, and mock_auth working.
     # mock_auth handles auth for test_user1 (owner)
     list_id = test_list1["id"]
     # Arrange: Create a place using imported helper and db_conn
     place_data = await create_test_place_direct(db_conn, list_id, "Place To Delete", "Addr", f"ext_del_{os.urandom(3).hex()}")
     place_db_id = place_data["id"]
     # Verify place exists initially
     assert await db_conn.fetchval("SELECT EXISTS (SELECT 1 FROM places WHERE id=$1 AND list_id=$2)", place_db_id, list_id)

     # Act
     del_response = await client.delete(f"{API_V1_LISTS}/{list_id}/places/{place_db_id}")

     # Assert
     assert del_response.status_code == status.HTTP_204_NO_CONTENT
     # Verify deleted in DB
     assert not await db_conn.fetchval("SELECT EXISTS (SELECT 1 FROM places WHERE id=$1 AND list_id=$2)", place_db_id, list_id)

async def test_delete_place_forbidden(client: AsyncClient, test_user1: Dict[str, Any], test_user2: Dict[str, Any], db_conn: asyncpg.Connection):
     """Test DELETE /{list_id}/places/{place_id} - Forbidden for non-owner/collaborator."""
     # This test requires the DB pool initialized, db_conn working.
     # Arrange: Create list owned by user2, add a place to it
     list_data = await create_test_list_direct(db_conn, test_user2["id"], "List Other User Delete Place", False)
     place_data = await create_test_place_direct(db_conn, list_data["id"], "Other Place Del", "Addr", f"ext_other_del_{os.urandom(3).hex()}")
     list_id = list_data["id"]
     place_db_id = place_data["id"]

     # Mock auth for user1 (not owner/collaborator)
     mock_token_user1 = FirebaseTokenData(uid=test_user1["firebase_uid"], email=test_user1["email"])
     async def override_auth(): return mock_token_user1
     app.dependency_overrides[deps.get_verified_token_data] = override_auth

     # Act
     response = await client.delete(f"{API_V1_LISTS}/{list_id}/places/{place_db_id}")

     # Cleanup mock
     app.dependency_overrides.clear()
     assert response.status_code == status.HTTP_403_FORBIDDEN

async def test_delete_place_not_found_in_list(client, mock_auth, test_list1, test_user1, db_conn):
    """Test DELETE /{list_id}/places/{place_id} - Place ID exists, but not in THIS list."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (owner)
    list_id = test_list1["id"]
    # Arrange: Create a place in a DIFFERENT list
    list_other = await create_test_list_direct(db_conn, test_user1["id"], "Other list", False)
    place_data_other_list = await create_test_place_direct(db_conn, list_other["id"], "Place in other list", "Addr", f"ext_other_list_{os.urandom(3).hex()}")
    place_db_id_other_list = place_data_other_list["id"]

    # Act: Try to delete the place from test_list1, using its ID from the other list
    response = await client.delete(f"{API_V1_LISTS}/{list_id}/places/{place_db_id_other_list}")

    # Assert
    # CRUD returns False -> API returns 404
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "Place not found in this list" in response.json()["detail"]

async def test_delete_place_non_existent_place_id(client: AsyncClient, mock_auth, test_list1: Dict[str, Any]):
    """Test DELETE /{list_id}/places/{place_id} - Place ID does not exist at all."""
    # This test requires the DB pool initialized, db_conn working, and mock_auth working.
    # mock_auth handles auth for test_user1 (owner)
    list_id = test_list1["id"]
    non_existent_place_id = 999999 # Assuming this ID does not exist

    # Act
    response = await client.delete(f"{API_V1_LISTS}/{list_id}/places/{non_existent_place_id}")

    # Assert
    # CRUD returns False -> API returns 404
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "Place not found in this list" in response.json()["detail"]