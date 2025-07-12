from typing import Sequence, Optional
from asyncpg import Record
from app.db.base import db_pool               # you already expose this

async def add_member(list_id: int, user_id: int, role: str = "viewer") -> Record:
    async with db_pool.acquire() as conn:
        return await conn.fetchrow(
            """
            INSERT INTO list_members (list_id, user_id, role, accepted_at)
            VALUES ($1, $2, $3,
                    CASE WHEN $3 = 'owner' THEN NOW() ELSE NULL END)
            RETURNING *;
            """,
            list_id,
            user_id,
            role,
        )

async def list_members(list_id: int) -> Sequence[Record]:
    async with db_pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT lm.*, u.display_name
            FROM list_members lm
            JOIN users u ON u.id = lm.user_id
            WHERE lm.list_id = $1
            ORDER BY role, invited_at;
            """,
            list_id,
        )

async def update_role(member_id: int, new_role: str) -> Record | None:
    async with db_pool.acquire() as conn:
        return await conn.fetchrow(
            """
            UPDATE list_members
            SET role = $2
            WHERE id = $1
            RETURNING *;
            """,
            member_id,
            new_role,
        )

async def remove_member(member_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM list_members WHERE id = $1", member_id)
