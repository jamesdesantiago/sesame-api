# tests/auth/test_permission_edges.py
import pytest
from httpx import AsyncClient
from fastapi import status

from tests.utils import create_test_user_direct 

API = "/api/v1"

###############################################################################
#  Helpers / tables of routes
###############################################################################

# Each entry:  (HTTP-method, url-template, needs_owner_setup?)
# For templates that include “{list_id}” we’ll create a list for user1 and
# substitute the real id in the parametrised test itself.
PROTECTED_ROUTES = [
    ("GET",  f"{API}/lists",                       False),
    ("POST", f"{API}/lists",                       False),
    ("GET",  f"{API}/lists/{{list_id}}",           True),
    ("PATCH",f"{API}/lists/{{list_id}}",           True),
    ("DELETE",f"{API}/lists/{{list_id}}",          True),
    ("POST", f"{API}/lists/{{list_id}}/places",    True),
    # …add any others you care about …
]

OWNER_ONLY_ROUTES = [
    ("GET",    f"{API}/lists/{{list_id}}"),
    ("PATCH",  f"{API}/lists/{{list_id}}"),
    ("DELETE", f"{API}/lists/{{list_id}}"),
    ("POST",   f"{API}/lists/{{list_id}}/places"),
]


###############################################################################
#  1. Missing / expired token  -> 401
###############################################################################

@pytest.mark.asyncio
@pytest.mark.parametrize("method,template,_", PROTECTED_ROUTES)
async def test_requires_authentication(
    client: AsyncClient,
    method: str,
    template: str,
    _,
    create_list,          # fixture that inserts a list for user1 + returns id
    test_user1
):
    url = template
    if "{list_id}" in template:
        list_id = await create_list(owner_id=test_user1["id"])
        url = template.format(list_id=list_id)

    r = await client.request(method, url)          # NO auth header
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Could not validate credentials" in r.json()["detail"]

###############################################################################
#  2. Token for another user  -> 403 or 404 (depends on endpoint)
###############################################################################

@pytest.mark.asyncio
@pytest.mark.parametrize("method,template", OWNER_ONLY_ROUTES)
async def test_wrong_user_cannot_access(
    client: AsyncClient,
    method: str,
    template: str,
    create_list,
    test_user1,
    test_user2,
    make_auth_header,
):
    list_id = await create_list(owner_id=test_user1["id"])
    url = template.replace("{list_id}", str(list_id))

    headers = make_auth_header(test_user2)
    payload = dummy_body(method, url)

    r = await client.request(method, url, headers=headers, json=payload)

    assert r.status_code in {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}

###############################################################################
#  3. Token issued for a user that was later deleted/disabled  -> 401/403
###############################################################################

@pytest.mark.asyncio
@pytest.mark.parametrize("method,template,_", PROTECTED_ROUTES)
async def test_deleted_user_token_rejected(
    client: AsyncClient,
    db_conn,
    method: str,
    template: str,
    _,
    create_list,
    test_user1,
    make_auth_header,
):
    ghost = await create_test_user_direct(db_conn, "ghost@example.com", "ghost-fb")
    ghost_headers = make_auth_header(ghost)
    await db_conn.execute("DELETE FROM users WHERE id = $1", ghost["id"])

    list_id = await create_list(owner_id=test_user1["id"])
    url = template.replace("{list_id}", str(list_id))

    payload = dummy_body(method, url)
    r = await client.request(method, url, headers=ghost_headers, json=payload)

    assert r.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}

def dummy_body(method: str, url: str):
    if method == "PATCH":
        return {"name": "patched"}          # List-update
    if method == "POST" and "/places" in url:
        return {
            "placeId": "dummy",
            "name": "Dummy place",
            "address": "123 Anywhere",
            "latitude": 0.0,
            "longitude": 0.0,
        }
    if method == "POST" and url.endswith("/lists"):
        return {"name": "dummy list"}
    return None