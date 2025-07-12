import pytest
from httpx import AsyncClient
from fastapi import status

# ---------------------------------------------------------------------------
# Helper to generate a unique-ish Google Place ID for dummy data
# ---------------------------------------------------------------------------

def _fake_place_id(idx: int) -> str:
    """Return a deterministic fake placeId like 'FakePlace0001'."""
    return f"FakePlace{idx:04d}"


# ---------------------------------------------------------------------------
# 1) Pagination sanity on GET /lists/{id}/places
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_places_pagination(client: AsyncClient, test_user1: dict, make_auth_header):
    """Seed 25 places then request page 2 (page_size=10) and assert behaviour."""

    headers = make_auth_header(test_user1)

    # Create a new list first
    create_resp = await client.post("/api/v1/lists", json={"name": "Pagination List"}, headers=headers)
    assert create_resp.status_code == status.HTTP_201_CREATED
    list_id = create_resp.json()["id"]

    # Seed 25 dummy places
    for i in range(25):
        payload = {
            "placeId":   _fake_place_id(i),
            "name":      f"Dummy Place #{i}",
            "address":   f"{i} Test Street, Testville",
            "latitude":  10.0 + i * 0.01,
            "longitude": 20.0 + i * 0.01,
        }
        r = await client.post(f"/api/v1/lists/{list_id}/places", json=payload, headers=headers)
        assert r.status_code == status.HTTP_201_CREATED, r.text

    # ── Actual pagination request ───────────────────────────────────────────
    page = 2
    page_size = 10
    resp = await client.get(f"/api/v1/lists/{list_id}/places?page={page}&page_size={page_size}", headers=headers)
    assert resp.status_code == status.HTTP_200_OK, resp.text

    body = resp.json()
    # Structural assertions
    assert body["page"] == page
    assert body["page_size"] == page_size
    assert body["total_items"] == 25
    assert body["total_pages"] == 3  # 10, 10, 5

    items = body["items"]
    assert len(items) == page_size               # page 2 should be full

    # Which ten should we get if the list is DESC-ordered?
    #
    # total_items  = 25
    # page         = 2         (1-based)
    # page_size    = 10
    # indices we expect → 14 … 5   (inclusive)
    start = body["total_items"] - page * page_size        # 25 − 20 = 5
    end   = start + page_size                             # 5  + 10 = 15
    expected_names = {f"Dummy Place #{i}" for i in range(start, end)}
    returned_names = {p["name"] for p in items}
    assert returned_names == expected_names


# ---------------------------------------------------------------------------
# 2) Duplicate‑place rejection (same placeId into same list)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_place_rejected(client: AsyncClient, test_user1: dict, make_auth_header):
    headers = make_auth_header(test_user1)

    # fresh list
    r = await client.post("/api/v1/lists", json={"name": "Dup Check"}, headers=headers)
    list_id = r.json()["id"]

    payload = {
        "placeId":   "DUPLICATE01",
        "name":      "Duplicate Target",
        "address":   "1 Dup Ave",
        "latitude":  0.0,
        "longitude": 0.0,
    }

    first = await client.post(f"/api/v1/lists/{list_id}/places", json=payload, headers=headers)
    assert first.status_code == status.HTTP_201_CREATED

    second = await client.post(f"/api/v1/lists/{list_id}/places", json=payload, headers=headers)

    # Decide your API semantics: treat as 400 Bad Request or 409 Conflict.
    # Here we assert either is fine, adjust if you have a single canonical code.
    assert second.status_code in {status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT}, second.text


# ---------------------------------------------------------------------------
# 3) Validation‑error: missing required field
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_place_validation_error(client: AsyncClient, test_user1: dict, make_auth_header):
    headers = make_auth_header(test_user1)
    r = await client.post("/api/v1/lists", json={"name": "Validation"}, headers=headers)
    list_id = r.json()["id"]

    invalid_payload = {
        "placeId": "NO_NAME_01",
        # name is intentionally omitted
        "address": "Somewhere",
        "latitude": 0,
        "longitude": 0,
    }

    resp = await client.post(f"/api/v1/lists/{list_id}/places", json=invalid_payload, headers=headers)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # optional: make sure error message references the missing field
    assert any(err["loc"][-1] == "name" for err in resp.json()["errors"])
