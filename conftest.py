"""
conftest.py  – Full test-fixtures for the Sesame backend.

Key points
----------
* One *session-wide* `asyncio` event-loop → avoids “attached to a different
  loop” RuntimeErrors.
* One *session-wide* asyncpg pool, created via `init_db_pool`, closed via
  `close_db_pool`.
* Per-test transaction rollback keeps the DB clean.
* httpx.AsyncClient with dependency overrides for DB + auth.

Tip
---
Add this to **pytest.ini** so pytest-asyncio auto-detects coroutine tests::

    [pytest]
    asyncio_mode = auto
"""

import asyncio
from typing import Callable, Dict, Any

import pytest
import pytest_asyncio
import asyncpg
from httpx import AsyncClient, ASGITransport
import inspect
from main import app as fastapi_app

from app.crud import crud_list
from app.schemas import list as list_schemas

import sentry_sdk

# --------------------------------------------------------------------------
# Session-scoped event-loop  (THE FIX)
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def event_loop():
    """Create one event-loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# --------------------------------------------------------------------------
# Session-scoped asyncpg pool
# --------------------------------------------------------------------------
@pytest_asyncio.fixture()
async def db_pool():
    from app.core.config import settings

    # Prefer TEST_DATABASE_URL, else DATABASE_URL, else assemble from parts
    dsn = (
        getattr(settings, "TEST_DATABASE_URL", None)
        or getattr(settings, "DATABASE_URL", None)
        or f"postgresql://{settings.DB_USER}:{settings.DB_PASSWORD}"
           f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    )

    pool = await asyncpg.create_pool(dsn=dsn)
    try:
        yield pool
    finally:
        await pool.close()


# --------------------------------------------------------------------------
# Function-scoped DB connection wrapped in a rollback-only transaction
# --------------------------------------------------------------------------
@pytest_asyncio.fixture()
async def db_conn(db_pool):
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            yield conn


# --------------------------------------------------------------------------
# httpx.AsyncClient with dependency override for `get_db`
# --------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="function")
async def client(db_conn, db_pool):
    from main import app
    from app.api import deps

    async def override_get_db():
        yield db_conn

    app.dependency_overrides[deps.get_db] = override_get_db

    # httpx 0.25+ only needs lifespan arg when supported
    if "lifespan" in inspect.signature(ASGITransport).parameters:
        transport = ASGITransport(app=app, lifespan="auto")
    else:
        transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport,
                           base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Helper fixtures for common test data
# --------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="function")
async def test_user1(db_conn: asyncpg.Connection) -> Dict[str, Any]:
    from tests.utils import create_test_user_direct
    return await create_test_user_direct(db_conn, "user1_api")


@pytest_asyncio.fixture(scope="function")
async def test_user2(db_conn: asyncpg.Connection) -> Dict[str, Any]:
    from tests.utils import create_test_user_direct
    return await create_test_user_direct(db_conn, "user2_api")


@pytest_asyncio.fixture(scope="function")
async def test_list1(db_conn: asyncpg.Connection, test_user1: Dict[str, Any]) -> Dict[str, Any]:
    from tests.utils import create_test_list_direct
    return await create_test_list_direct(
        db_conn, owner_id=test_user1["id"], name="Test List 1", is_public=False
    )


# --------------------------------------------------------------------------
# Auth-mocking helpers
# --------------------------------------------------------------------------
@pytest.fixture(scope="function")
def mock_auth(test_user1: Dict[str, Any]):
    from main import app
    from app.api import deps
    from app.schemas.token import FirebaseTokenData

    token = FirebaseTokenData(uid=test_user1["firebase_uid"], email=test_user1["email"])

    async def override() -> FirebaseTokenData:
        return token

    app.dependency_overrides[deps.get_verified_token_data] = override
    yield
    app.dependency_overrides.pop(deps.get_verified_token_data, None)


@pytest.fixture(scope="function")
def mock_auth_invalid():
    from main import app
    from app.api import deps
    from fastapi import HTTPException, status

    async def override():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Mock Auth: Invalid Token"
        )

    app.dependency_overrides[deps.get_verified_token_data] = override
    yield
    app.dependency_overrides.pop(deps.get_verified_token_data, None)


@pytest.fixture(scope="function")
def mock_auth_optional(test_user1: Dict[str, Any]):
    from main import app
    from app.api import deps
    from app.schemas.token import FirebaseTokenData

    token = FirebaseTokenData(uid=test_user1["firebase_uid"], email=test_user1["email"])

    async def override():
        return token

    app.dependency_overrides[deps.get_optional_verified_token_data] = override
    yield
    app.dependency_overrides.pop(deps.get_optional_verified_token_data, None)


@pytest.fixture(scope="function")
def mock_auth_optional_unauthenticated():
    from main import app
    from app.api import deps

    async def override():
        return None

    app.dependency_overrides[deps.get_optional_verified_token_data] = override
    yield
    app.dependency_overrides.pop(deps.get_optional_verified_token_data, None)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """
    Ensure SlowAPI’s in-memory storage is empty for every test.
    We clear it *before* the test runs (to remove anything left
    by a test in another worker) and again afterwards just to
    keep things tidy.
    """
    limiter = getattr(fastapi_app.state, "limiter", None)
    if limiter:
        limiter.reset()      # ---- pre-test wipe
    yield
    if limiter:
        limiter.reset()      # ---- post-test wipe

# --------------------------------------------------------------------------
# Automatic rollback to a pristine DB before every test
# --------------------------------------------------------------------------

_TABLES = [
    "follows",
    "notifications",
    "list_collaborators",
    "list_places",
    "places",
    "lists",
    "users",
]

async def _wipe_db(conn: asyncpg.Connection) -> None:
    """
    TRUNCATE all data-bearing tables safely.

    On very old versions of Postgres the `IF EXISTS` clause is not supported,
    so we catch UndefinedTableError instead of relying on it.
    """
    for t in _TABLES:
        try:
            # old-version-friendly – no `IF EXISTS`
            await conn.execute(f"TRUNCATE {t} RESTART IDENTITY CASCADE;")
        except asyncpg.UndefinedTableError:
            # Table does not exist yet (first test run) – ignore.
            pass

@pytest_asyncio.fixture(autouse=True, scope="function")
async def _clean_db(db_pool):
    # ---------- before test ----------
    async with db_pool.acquire() as conn:
        await _wipe_db(conn)

    yield                          # -------- test runs --------

    # ---------- after test ----------
    async with db_pool.acquire() as conn:
        await _wipe_db(conn)

# ---------- helper: create a list for an owner and return its id ----------
@pytest.fixture
def create_list(db_conn: asyncpg.Connection) -> Callable[[int], int]:
    """
    Synchronous helper usable inside async tests:

        list_id = await create_list(owner_id)
    """
    async def _create(owner_id: int) -> int:
        data = list_schemas.ListCreate(name="pytest-list", isPrivate=False)
        rec = await crud_list.create_list(db=db_conn, list_in=data, owner_id=owner_id)
        return rec["id"]

    # we return the *coroutine function*, not the awaited result
    return _create


# ---------- helper: build an Authorization header for a given user ----------
@pytest.fixture
def make_auth_header():
    """
    Tests call:  headers = make_auth_header(test_user)
    """
    def _make(user: dict[str, str], token_type: str = "Bearer") -> dict[str, str]:
        return {"Authorization": f"{token_type} {user['firebase_uid']}"}

    return _make

@pytest.fixture(scope="session", autouse=True)
def _close_sentry():
    """
    Flush the event queue and disable the client after the test
    session ends, if a client was initialised.
    """
    yield

    # Always drain the queue
    sentry_sdk.flush()

    # Only the client object has .close()
    client = sentry_sdk.get_client()
    if client is not None:          # SDK was initialised
        client.close(timeout=2.0)   # or whatever timeout you prefer