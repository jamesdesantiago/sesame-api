# backend/tests/api/test_discovery_api.py

import pytest
from httpx import AsyncClient
from fastapi import status
from typing import Dict, Any, Optional
import asyncpg
import asyncio # Needed for sleep if ensuring distinct timestamps
import os # Needed for random names
import math # Needed for pagination checks

# Import app components
from app.core.config import settings
from app.schemas import list as list_schemas # For asserting response structure
from app.api import deps # For mocking dependencies if needed
from app.schemas.token import FirebaseTokenData # For mocking
# No need to import crud here, tests interact via API client

# Import helpers from utils (these imports stay relative to tests/)
from tests.utils import (
    create_test_list_direct,
    # create_test_user_direct, # Users created by conftest fixtures
)

# API Prefix
API_V1 = settings.API_V1_STR

# Helper function to create lists directly for testing setup
# Using the imported helper now
# async def create_test_list(...): ... # REMOVED


# --- Tests for GET /public-lists ---

# These tests require the DB pool to be initialized via lifespan_db_pool_manager fixture,
# and implicitly use db_conn/db_tx via the client fixture dependency override.
# If db_pool is None, the client fixture will fail.

@pytest.mark.asyncio
async def test_get_public_lists_empty(client: AsyncClient, db_conn):
    """Test fetching public lists when none exist."""
    # Debug: Check lists table state
    lists = await db_conn.fetch("SELECT * FROM lists")
    print(f"Lists before API call: {lists}")
    public_lists = await db_conn.fetch("SELECT * FROM lists WHERE is_public = true")
    print(f"Public lists before API call: {public_lists}")

    response = await client.get(f"{API_V1}/public-lists")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["items"] == [], f"Expected empty items, got: {data['items']}"
    assert data["total_items"] == 0
    assert data["page"] == 1

async def test_get_public_lists_success(client: AsyncClient, db_conn: asyncpg.Connection, test_user1, test_user2):
    """Test fetching public lists successfully with pagination."""
    # This test requires the DB pool initialized and the db_tx fixture working.
    # If db_pool is None, the db_tx fixture will fail, resulting in the "DB Pool not available" error.
    # Arrange: Create some public and private lists using the direct helper
    # Use db_conn here
    public_list1 = await create_test_list_direct(db_conn, test_user1["id"], "Public List 1", False)
    private_list1 = await create_test_list_direct(db_conn, test_user1["id"], "User1 Private List", True)
    public_list2 = await create_test_list_direct(db_conn, test_user2["id"], "Public List 2", False)

    # Act: Fetch first page
    # Note: Default order is created_at DESC in crud_list.get_public_lists_paginated. public_list2 was created later than public_list1.
    response_page1 = await client.get(f"{API_V1}/public-lists?page=1&page_size=1")
    assert response_page1.status_code == status.HTTP_200_OK
    data1 = response_page1.json()

    # Assert: Page 1
    assert data1["total_items"] == 2 # Only public lists counted
    assert data1["total_pages"] == 2
    assert data1["page"] == 1
    assert data1["page_size"] == 1
    assert len(data1["items"]) == 1
    # Assuming default order is created_at DESC, public_list2 should be first
    # Check against the IDs returned by the helper functions
    assert data1["items"][0]["id"] == public_list2["id"]
    assert data1["items"][0]["name"] == public_list2["name"]
    assert data1["items"][0]["isPrivate"] is False
    # Ensure place_count is present (defaults to 0 if no places)
    assert "place_count" in data1["items"][0]

    # Act: Fetch second page
    response_page2 = await client.get(f"{API_V1}/public-lists?page=2&page_size=1")
    assert response_page2.status_code == status.HTTP_200_OK
    data2 = response_page2.json()

    # Assert: Page 2
    assert data2["total_items"] == 2
    assert data2["total_pages"] == 2
    assert data2["page"] == 2
    assert data2["page_size"] == 1
    assert len(data2["items"]) == 1
    assert data2["items"][0]["id"] == public_list1["id"]
    assert data2["items"][0]["isPrivate"] is False
    assert "place_count" in data2["items"][0]

    # Cleanup is handled by the `db_conn` fixture which truncates tables

# --- Tests for GET /search-lists ---

async def test_search_lists_unauthenticated(client: AsyncClient, db_conn: asyncpg.Connection, test_user1, test_user2, mock_auth_optional_unauthenticated):
    """Test searching lists without authentication (should only find public)."""
    # This test requires the DB pool initialized and the db_tx fixture working.
    # Arrange: Create lists using the direct helper
    pub1 = await create_test_list_direct(db_conn, test_user1["id"], "Search Public Alpha", False, description="Contains target")
    priv1 = await create_test_list_direct(db_conn, test_user1["id"], "Search Private Alpha", True)
    pub2 = await create_test_list_direct(db_conn, test_user2["id"], "Another Public Search", False)
    priv2 = await create_test_list_direct(db_conn, test_user2["id"], "Other Private Beta", True) # Search term "Alpha" won't match Beta

    # Act: Search for "Search"
    # mock_auth_optional_unauthenticated fixture handles optional auth return None
    response = await client.get(f"{API_V1}/search-lists?q=Search")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    # Assert: Only public lists matching "Search" are found
    # Both pub1 and pub2 contain "Search" in name
    assert data["total_items"] == 2
    assert len(data["items"]) == 2
    found_ids = {item["id"] for item in data["items"]}
    assert found_ids == {pub1["id"], pub2["id"]} # Only public ones
    assert priv1["id"] not in found_ids
    assert priv2["id"] not in found_ids
    assert "place_count" in data["items"][0]


async def test_search_lists_authenticated(client: AsyncClient, db_conn: asyncpg.Connection, test_user1, test_user2, mock_auth_optional):
    """Test searching lists while authenticated (should find public + user's private)."""
    # This test requires the DB pool initialized and the db_tx fixture working.
    # mock_auth_optional mocks optional auth for test_user1, providing test_user1['id'] to dependency
    # Arrange: Create lists using the direct helper
    pub1 = await create_test_list_direct(db_conn, test_user1["id"], "My Search Public Alpha", False, description="Contains target")
    priv1 = await create_test_list_direct(db_conn, test_user1["id"], "My Search Private Alpha", True)
    pub2 = await create_test_list_direct(db_conn, test_user2["id"], "Other Public Search Beta", False) # Contains "Search" and "Beta"
    priv2 = await create_test_list_direct(db_conn, test_user2["id"], "Other Private Beta", True) # Contains "Beta"

    # Use mock_auth_optional fixture implicitly (client + deps mock)
    # The client fixture overrides deps.get_db, and mock_auth_optional overrides deps.get_optional_verified_token_data
    # The endpoint search_lists uses deps.get_optional_current_user_id which depends on get_optional_verified_token_data

    # Act: Search for "Alpha"
    response = await client.get(f"{API_V1}/search-lists?q=Alpha")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    # Assert: Finds user1's public (pub1) and user1's private (priv1) matching "Alpha", but not user2's private (priv2)
    assert data["total_items"] == 2 # pub1 and priv1 match "Alpha" in name
    assert len(data["items"]) == 2
    found_ids = {item["id"] for item in data["items"]}
    assert found_ids == {pub1["id"], priv1["id"]}
    assert pub2["id"] not in found_ids # pub2 doesn't match "Alpha"
    assert priv2["id"] not in found_ids # priv2 doesn't match "Alpha" and is private + not owned by user1
    assert "place_count" in data["items"][0]


    # Act: Search for "Beta"
    response_beta = await client.get(f"{API_V1}/search-lists?q=Beta")
    assert response_beta.status_code == status.HTTP_200_OK
    data_beta = response_beta.json()

    # Assert: Finds pub2 (public, matches "Beta") but not priv2 (private, not owned by user1)
    assert data_beta["total_items"] == 1
    assert len(data_beta["items"]) == 1
    assert data_beta["items"][0]["id"] == pub2["id"]
    assert "place_count" in data_beta["items"][0]


    # Act: Search for "target" (in description)
    response_desc = await client.get(f"{API_V1}/search-lists?q=target")
    assert response_desc.status_code == status.HTTP_200_OK
    data_desc = response_desc.json()

    # Assert: Finds pub1 based on description search
    assert data_desc["total_items"] == 1
    assert len(data_desc["items"]) == 1
    assert data_desc["items"][0]["id"] == pub1["id"]
    assert "place_count" in data_desc["items"][0]


async def test_search_lists_no_results(client: AsyncClient, mock_auth_optional):
    """Test searching when no lists match."""
    # This test requires the DB pool initialized and the db_tx fixture working.
    # Auth is optional, but we test with it on.
    # Create some lists that won't match
    # await create_test_list_direct(db_conn, test_user1["id"], "Some List", False)
    # await create_test_list_direct(db_conn, test_user1["id"], "Another List", True)

    response = await client.get(f"{API_V1}/search-lists?q=nonexistentquery")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["items"] == []
    assert data["total_items"] == 0

# --- Tests for GET /recent-lists ---

async def test_get_recent_lists_unauthenticated(client: AsyncClient):
    """Test getting recent lists requires authentication."""
    # This test requires the DB pool initialized and the client working.
    response = await client.get(f"{API_V1}/recent-lists")
    # This endpoint requires authentication based on its definition in discovery.py
    assert response.status_code == status.HTTP_401_UNAUTHORIZED

async def test_get_recent_lists_success(client: AsyncClient, db_conn: asyncpg.Connection, test_user1, test_user2, mock_auth):
    """Test fetching recent lists (user's + public)."""
    # This test requires the DB pool initialized, db_tx working, and mock_auth working.
    # mock_auth mocks mandatory auth for test_user1, providing test_user1['id']
    # Arrange: Create lists - order matters for recency (created_at defaults to NOW())
    # Order: priv2 (oldest), pub2, priv1, pub1 (newest)
    # Use db_conn here
    priv2 = await create_test_list_direct(db_conn, test_user2["id"], "Other Private Recent", True)
    await asyncio.sleep(0.01) # Ensure distinct timestamps
    pub2 = await create_test_list_direct(db_conn, test_user2["id"], "Other Public Recent", False)
    await asyncio.sleep(0.01)
    priv1 = await create_test_list_direct(db_conn, test_user1["id"], "My Private Recent", True)
    await asyncio.sleep(0.01)
    pub1 = await create_test_list_direct(db_conn, test_user1["id"], "My Public Recent", False)

    # Act: Fetch first page (size 2)
    # Use mock_auth fixture implicitly (client + deps mock)
    response = await client.get(f"{API_V1}/recent-lists?page=1&page_size=2")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    # Assert: Should include user1's lists (pub1, priv1) and public list (pub2), ordered by creation desc
    # Total items should be 3 (pub1, priv1, pub2 are visible to user1)
    assert data["total_items"] == 3
    assert data["total_pages"] == math.ceil(3 / 2)
    assert len(data["items"]) == 2
    # Assuming newest first ordering in CRUD (created_at DESC):
    assert data["items"][0]["id"] == pub1["id"] # Newest
    assert data["items"][1]["id"] == priv1["id"]
    assert "place_count" in data["items"][0]
    assert "place_count" in data["items"][1]


    # Act: Fetch second page
    response_p2 = await client.get(f"{API_V1}/recent-lists?page=2&page_size=2")
    assert response_p2.status_code == status.HTTP_200_OK
    data_p2 = response_p2.json()

    assert data_p2["total_items"] == 3
    assert data_p2["total_pages"] == math.ceil(3 / 2)
    assert len(data_p2["items"]) == 1
    assert data_p2["items"][0]["id"] == pub2["id"] # The public list from user2
    assert "place_count" in data_p2["items"][0]

    # Cleanup is handled by the `db_conn` fixture which truncates tables


async def test_get_recent_lists_empty(client: AsyncClient, test_user1, mock_auth):
    """Test fetching recent lists when user has none and none are public."""
    # This test requires the DB pool initialized, db_tx working, and mock_auth working.
    # mock_auth provides auth for test_user1
    # Ensure no public lists exist and user1 has no lists beyond what fixtures clean up
    response = await client.get(f"{API_V1}/recent-lists")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["items"] == []
    assert data["total_items"] == 0