import pytest
from httpx import AsyncClient
from starlette import status

@pytest.mark.asyncio
async def test_invite_and_list(
    client: AsyncClient,
    test_user1: dict,               # owner
    test_user2: dict,               # invitee
    make_auth_header,
):
    h_owner  = make_auth_header(test_user1)
    h_member = make_auth_header(test_user2)

    # owner creates a list
    r = await client.post("/api/v1/lists", json={"name": "Collab"}, headers=h_owner)
    assert r.status_code == status.HTTP_201_CREATED
    list_id = r.json()["id"]

    # owner invites member
    r = await client.post(
        f"/api/v1/lists/{list_id}/collaborators",
        json={"user_id": test_user2["id"], "role": "editor"},
        headers=h_owner,
    )
    assert r.status_code == status.HTTP_201_CREATED

    # member can retrieve the collaborators list
    r = await client.get(
        f"/api/v1/lists/{list_id}/collaborators",
        headers=h_member,
    )
    assert r.status_code == status.HTTP_200_OK
    assert any(m["user_id"] == test_user2["id"] for m in r.json())
