import pytest
from fastapi import status

API_V1 = "/api/v1"

@pytest.mark.asyncio
async def test_check_username_rate_limit(client, mock_auth):
    """After 7 calls /min the 8th should hit the SlowAPI limit."""
    url = f"{API_V1}/users/check-username"
    
    # First 7 requests succeed (200)
    for _ in range(7):
        resp = await client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["needsUsername"] in (True, False)
    
    # 8th request should be blocked
    resp = await client.get(url)
    assert resp.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    # Payload may differ by framework version; just check text
    assert "rate" in resp.text.lower()
