# ── pick up again inside env.py ─────────────────────────────────────────
from __future__ import annotations

import asyncio
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import MetaData, pool
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy.engine.url import make_url

config = context.config                     # ← ADD
if config.config_file_name:                 # ← ADD (optional logging)
    from logging.config import fileConfig
    fileConfig(config.config_file_name)

from dotenv import load_dotenv

# Load .env(.test) exactly once
load_dotenv(Path(__file__).resolve().parent.parent / ".env.test")

# → bring settings into scope here
from app.core.config import settings  

# ───── build a clean URL for Alembic ──────────────────────────────
url_obj = make_url(settings.DATABASE_URL)

# Drop libpq-only query keys that upset asyncpg
bad_keys = {"sslmode", "sslrootcert", "sslcert", "sslkey"}
clean_qs = {k: v for k, v in url_obj.query.items() if k not in bad_keys}

DATABASE_URL = (
    url_obj.set(query=clean_qs)       # <- cleaned query string
           .set(drivername="postgresql+asyncpg")  # make it async
           .render_as_string(hide_password=False)
)
# Metadata that will be filled by reflection at runtime
target_metadata = MetaData()

AUTOGEN_KW = dict(
    target_metadata=target_metadata,
    compare_type=True,
    compare_server_default=True,
)

# -------------- OFFLINE (—sql) -----------------
def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL,
                      literal_binds=True,
                      dialect_opts={"paramstyle": "named"},
                      **AUTOGEN_KW)
    with context.begin_transaction():
        context.run_migrations()

# -------------- ONLINE (real DB) ---------------
async def do_run_migrations() -> None:
    engine: AsyncEngine = create_async_engine(DATABASE_URL, poolclass=pool.NullPool)

    async with engine.connect() as conn:
        # reflect live DB or skip if you point target_metadata at models
        await conn.run_sync(
            lambda sync_conn: target_metadata.reflect(bind=sync_conn)
        )

        # configure Alembic with that metadata
        await conn.run_sync(
            lambda sync_conn: context.configure(connection=sync_conn, **AUTOGEN_KW)
        )

        async with context.begin_transaction():
            await conn.run_sync(lambda _conn: context.run_migrations())  # ← fixed

def run_migrations_online() -> None:
    asyncio.run(do_run_migrations())
# ------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
