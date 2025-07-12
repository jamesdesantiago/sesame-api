# tests/lists/test_crud_happy.py
import pytest
from httpx import AsyncClient
from fastapi import status


@pytest.mark.asyncio
async def test_list_crud_lifecycle(client: AsyncClient, test_user1: dict, make_auth_header):
    """
    End-to-end sanity check:

    1.  Create a list   (POST /lists)
    2.  Fetch it        (GET  /lists/{id})
    3.  Update name     (PATCH /lists/{id})
    4.  Add a place     (POST /lists/{id}/places)
    5.  Confirm place   (GET  /lists/{id}/places)
    6.  Delete the list (DELETE /lists/{id})
    7.  Verify 404 on subsequent GET
    """
    headers = make_auth_header(test_user1)

    # 1️⃣  CREATE ──────────────────────────────────────────────────────────────
    create_payload = {"name": "Road-trip 2025"}
    r = await client.post("/api/v1/lists", json=create_payload, headers=headers)
    assert r.status_code == status.HTTP_201_CREATED, r.text
    list_id = r.json()["id"]

    # 2️⃣  READ ────────────────────────────────────────────────────────────────
    r = await client.get(f"/api/v1/lists/{list_id}", headers=headers)
    assert r.status_code == status.HTTP_200_OK, r.text
    data = r.json()
    assert data["name"] == create_payload["name"]

    # 3️⃣  UPDATE ──────────────────────────────────────────────────────────────
    new_name = "Epic USA Road-trip"
    r = await client.patch(f"/api/v1/lists/{list_id}", json={"name": new_name}, headers=headers)
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.json()["name"] == new_name

    # 4️⃣  ADD PLACE ───────────────────────────────────────────────────────────
    place_payload = {
        "placeId":   "ChIJN1t_tDeuEmsRUsoyG83frY4",
        "name":      "Sydney Opera House",
        "address":   "Bennelong Point, Sydney NSW 2000, Australia",
        "latitude":  -33.8568,
        "longitude": 151.2153,
    }
    r = await client.post(f"/api/v1/lists/{list_id}/places", json=place_payload, headers=headers)
    assert r.status_code == status.HTTP_201_CREATED, r.text
    place_id = r.json()["id"]

    # 5️⃣  CONFIRM PLACE PRESENT ───────────────────────────────────────────────
    r = await client.get(f"/api/v1/lists/{list_id}/places", headers=headers)
    assert r.status_code == status.HTTP_200_OK, r.text
    assert any(p["id"] == place_id for p in r.json()["items"])

    # 6️⃣  DELETE LIST ─────────────────────────────────────────────────────────
    r = await client.delete(f"/api/v1/lists/{list_id}", headers=headers)
    assert r.status_code == status.HTTP_204_NO_CONTENT, r.text

    # 7️⃣  VERIFY 404 ──────────────────────────────────────────────────────────
    r = await client.get(f"/api/v1/lists/{list_id}", headers=headers)
    assert r.status_code == status.HTTP_404_NOT_FOUND
