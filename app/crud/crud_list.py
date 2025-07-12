"""
CRUD helpers for working with “lists” (plus collaborators & discovery).

Every public function:
• takes an `asyncpg.Connection`
• returns plain Python data (dict / list / bool) or raises a custom error
• never commits/rolls back – the calling layer controls transactions
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from app.schemas import list as list_schemas

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Custom error types                                                         #
# --------------------------------------------------------------------------- #
class ListNotFoundError(Exception):
    """List does not exist in the DB."""


class ListAccessDeniedError(Exception):
    """Current user is not owner nor collaborator."""


class CollaboratorAlreadyExistsError(Exception):
    """Attempt to add an e-mail / user that is already a collaborator (or owner)."""


class DatabaseInteractionError(Exception):
    """Any unexpected DB-layer failure."""


# --------------------------------------------------------------------------- #
#  Internal helpers                                                           #
# --------------------------------------------------------------------------- #
async def _get_collaborator_emails(db: asyncpg.Connection, list_id: int) -> List[str]:
    """Return all collaborator e-mail addresses for `list_id`."""
    try:
        rows = await db.fetch(
            """
            SELECT u.email
            FROM list_collaborators lc
            JOIN users u ON lc.user_id = u.id
            WHERE lc.list_id = $1
            """,
            list_id,
        )
        return [r["email"] for r in rows]
    except Exception as exc:  # pragma: no cover
        logger.error("Error reading collaborators for list %s: %s", list_id, exc, exc_info=True)
        raise DatabaseInteractionError("Database error fetching collaborators.") from exc


# --------------------------------------------------------------------------- #
#  Core CRUD                                                                  #
# --------------------------------------------------------------------------- #
async def create_list(
    db: asyncpg.Connection, list_in: list_schemas.ListCreate, owner_id: int
) -> asyncpg.Record:
    """Insert a new list row and return the DB record that was created."""
    try:
        rec = await db.fetchrow(
            """
            INSERT INTO lists (name, description, owner_id, created_at, is_private)
            VALUES ($1, $2, $3, now(), $4)
            RETURNING id, name, description, is_private
            """,
            list_in.name,
            list_in.description,
            owner_id,
            list_in.isPrivate,
        )
        if rec is None:
            raise DatabaseInteractionError("Insert returned no row.")

        logger.info("Created list %s for owner %s", rec["id"], owner_id)
        return rec

    except asyncpg.PostgresError as pg:  # pragma: no cover
        logger.error("PostgresError creating list: %s", pg, exc_info=True)
        raise DatabaseInteractionError("Database error creating list.") from pg
    except Exception as exc:  # pragma: no cover
        logger.error("Unexpected error creating list: %s", exc, exc_info=True)
        raise DatabaseInteractionError("Unexpected error creating list.") from exc


async def get_list_by_id(db: asyncpg.Connection, list_id: int) -> Optional[asyncpg.Record]:
    """Fetch a list row by primary key; returns `None` if absent."""
    try:
        return await db.fetchrow(
            "SELECT id, owner_id, name, description, is_private FROM lists WHERE id = $1",
            list_id,
        )
    except Exception as exc:  # pragma: no cover
        logger.error("Error fetching list %s: %s", list_id, exc, exc_info=True)
        raise DatabaseInteractionError("Database error fetching list by ID.") from exc


async def get_list_details(db: asyncpg.Connection, list_id: int) -> Optional[Dict[str, Any]]:
    """
    Convenience for endpoints: metadata + collaborators.

    Returns `None` when the list doesn’t exist.
    """
    try:
        rec = await db.fetchrow(
            """
            SELECT id, name, description, is_private
            FROM lists
            WHERE id = $1
            """,
            list_id,
        )
        if rec is None:
            return None

        detail: Dict[str, Any] = dict(rec)
        detail["collaborators"] = await _get_collaborator_emails(db, list_id)
        return detail

    except DatabaseInteractionError:
        raise
    except Exception as exc:  # pragma: no cover
        logger.error("Error fetching details for list %s: %s", list_id, exc, exc_info=True)
        raise DatabaseInteractionError("Database error fetching list details.") from exc


# --------------------------------------------------------------------------- #
#  Pagination helpers                                                         #
# --------------------------------------------------------------------------- #
_ORDER_BY = "ORDER BY l.created_at DESC, l.id DESC"  # stable secondary key


async def get_user_lists_paginated(
    db: asyncpg.Connection, owner_id: int, page: int, page_size: int
) -> Tuple[List[asyncpg.Record], int]:
    """Return (records, total) for the owner’s own lists."""
    offset = (page - 1) * page_size
    try:
        total: int = await db.fetchval("SELECT COUNT(*) FROM lists WHERE owner_id = $1", owner_id) or 0
        if total == 0:
            return [], 0

        rows = await db.fetch(
            f"""
            SELECT l.id,
                   l.name,
                   l.description,
                   l.is_private,
                   (SELECT COUNT(*) FROM places p WHERE p.list_id = l.id) AS place_count
            FROM lists l
            WHERE l.owner_id = $1
            {_ORDER_BY}
            LIMIT $2 OFFSET $3
            """,
            owner_id,
            page_size,
            offset,
        )
        return rows, total
    except Exception as exc:  # pragma: no cover
        logger.error("Error paginating user lists: %s", exc, exc_info=True)
        raise DatabaseInteractionError("Database error fetching user lists.") from exc


async def get_public_lists_paginated(
    db: asyncpg.Connection, page: int, page_size: int
) -> Tuple[List[asyncpg.Record], int]:
    """Public discovery listing."""
    offset = (page - 1) * page_size
    try:
        total: int = await db.fetchval("SELECT COUNT(*) FROM lists WHERE is_private = FALSE") or 0
        if total == 0:
            return [], 0

        rows = await db.fetch(
            f"""
            SELECT l.id,
                   l.name,
                   l.description,
                   l.is_private,
                   (SELECT COUNT(*) FROM places p WHERE p.list_id = l.id) AS place_count
            FROM lists l
            WHERE l.is_private = FALSE
            {_ORDER_BY}
            LIMIT $1 OFFSET $2
            """,
            page_size,
            offset,
        )
        return rows, total
    except Exception as exc:  # pragma: no cover
        logger.error("Error paginating public lists: %s", exc, exc_info=True)
        raise DatabaseInteractionError("Database error fetching public lists.") from exc


async def search_lists_paginated(
    db: asyncpg.Connection,
    query: str,
    user_id: Optional[int],
    page: int,
    page_size: int,
) -> Tuple[List[asyncpg.Record], int]:
    """Case-insensitive search in name/description, respecting privacy."""
    offset = (page - 1) * page_size
    q_like = f"%{query.lower()}%"

    where_parts = ["(LOWER(l.name) LIKE $1 OR LOWER(l.description) LIKE $1)"]
    params: list[Any] = [q_like]

    if user_id is None:
        where_parts.append("l.is_private = FALSE")
    else:
        where_parts.append("(l.is_private = FALSE OR l.owner_id = $2)")
        params.append(user_id)

    where_sql = " AND ".join(where_parts)
    base_from = f"FROM lists l WHERE {where_sql}"

    try:
        total: int = await db.fetchval(f"SELECT COUNT(*) {base_from}", *params) or 0
        if total == 0:
            return [], 0

        params.extend([page_size, offset])  # $n for LIMIT/OFFSET
        rows = await db.fetch(
            f"""
            SELECT l.id,
                   l.name,
                   l.description,
                   l.is_private,
                   (SELECT COUNT(*) FROM places p WHERE p.list_id = l.id) AS place_count
            {base_from}
            {_ORDER_BY}
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return rows, total
    except Exception as exc:  # pragma: no cover
        logger.error("Error searching lists: %s", exc, exc_info=True)
        raise DatabaseInteractionError("Database error searching lists.") from exc


async def get_recent_lists_paginated(
    db: asyncpg.Connection, user_id: int, page: int, page_size: int
) -> Tuple[List[asyncpg.Record], int]:
    """Newest public lists plus any private lists owned by `user_id`."""
    offset = (page - 1) * page_size
    try:
        total: int = await db.fetchval(
            """
            SELECT COUNT(*)
            FROM lists l
            WHERE l.is_private = FALSE OR l.owner_id = $1
            """,
            user_id,
        ) or 0
        if total == 0:
            return [], 0

        rows = await db.fetch(
            f"""
            SELECT l.id,
                   l.name,
                   l.description,
                   l.is_private,
                   (SELECT COUNT(*) FROM places p WHERE p.list_id = l.id) AS place_count
            FROM lists l
            WHERE l.is_private = FALSE OR l.owner_id = $1
            {_ORDER_BY}
            LIMIT $2 OFFSET $3
            """,
            user_id,
            page_size,
            offset,
        )
        return rows, total
    except Exception as exc:  # pragma: no cover
        logger.error("Error fetching recent lists: %s", exc, exc_info=True)
        raise DatabaseInteractionError("Database error fetching recent lists.") from exc


# --------------------------------------------------------------------------- #
#  Update / delete                                                            #
# --------------------------------------------------------------------------- #
async def update_list(
    db: asyncpg.Connection, list_id: int, list_in: list_schemas.ListUpdate
) -> Optional[Dict[str, Any]]:
    """PATCH a list row and return full details (including collaborators)."""
    fields = list_in.model_dump(exclude_unset=True)
    if not fields:
        # nothing to change – just echo current state
        return await get_list_details(db, list_id)

    sets: list[str] = []
    params: list[Any] = []
    idx = 1

    if "name" in fields:
        sets.append(f"name = ${idx}")
        params.append(fields["name"])
        idx += 1
    if "isPrivate" in fields:
        sets.append(f"is_private = ${idx}")
        params.append(fields["isPrivate"])
        idx += 1

    params.append(list_id)  # WHERE param

    try:
        rec = await db.fetchrow(
            f"""
            UPDATE lists
            SET {', '.join(sets)}, updated_at = now()
            WHERE id = ${idx}
            RETURNING id, name, description, is_private
            """,
            *params,
        )
        if rec is None:
            return None

        detail = dict(rec)
        detail["collaborators"] = await _get_collaborator_emails(db, list_id)
        return detail

    except Exception as exc:  # pragma: no cover
        logger.error("Error updating list %s: %s", list_id, exc, exc_info=True)
        raise DatabaseInteractionError("Database error updating list.") from exc


async def delete_list(db: asyncpg.Connection, list_id: int) -> bool:
    """True if a row was deleted, False if the id did not exist."""
    try:
        status = await db.execute("DELETE FROM lists WHERE id = $1", list_id)
        return int(status.split(" ")[1]) > 0
    except Exception as exc:  # pragma: no cover
        logger.error("Error deleting list %s: %s", list_id, exc, exc_info=True)
        raise DatabaseInteractionError("Database error deleting list.") from exc


# --------------------------------------------------------------------------- #
#  Collaborator operations                                                    #
# --------------------------------------------------------------------------- #
async def add_collaborator_to_list(
    db: asyncpg.Connection, list_id: int, collaborator_email: str
) -> None:
    """Add `collaborator_email` to `list_id`; may create a placeholder user."""
    try:
        # 1. Resolve user id – create placeholder if needed
        user_id = await db.fetchval("SELECT id FROM users WHERE email = $1", collaborator_email)
        if user_id is None:
            user_id = await db.fetchval(
                """
                INSERT INTO users (email, created_at, updated_at)
                VALUES ($1, now(), now())
                ON CONFLICT (email) DO UPDATE SET updated_at = now()
                RETURNING id
                """,
                collaborator_email,
            )

        # 2. Disallow adding the owner
        owner_id = await db.fetchval("SELECT owner_id FROM lists WHERE id = $1", list_id)
        if owner_id == user_id:
            raise CollaboratorAlreadyExistsError("Owner is already a collaborator.")

        # 3. Fail fast if already collaborator
        exists = await db.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM list_collaborators
                WHERE list_id = $1 AND user_id = $2
            )
            """,
            list_id,
            user_id,
        )
        if exists:
            raise CollaboratorAlreadyExistsError("User is already a collaborator.")

        # 4. Insert relation
        await db.execute(
            "INSERT INTO list_collaborators (list_id, user_id) VALUES ($1, $2)",
            list_id,
            user_id,
        )
        logger.info("Added collaborator %s (%s) to list %s", user_id, collaborator_email, list_id)

    except CollaboratorAlreadyExistsError:
        # let the API layer map this to 409
        raise
    except Exception as exc:  # pragma: no cover
        logger.error("Error adding collaborator: %s", exc, exc_info=True)
        raise DatabaseInteractionError("Database error adding collaborator.") from exc


async def delete_collaborator_from_list(
    db: asyncpg.Connection, list_id: int, collaborator_user_id: int
) -> bool:
    """Remove collaborator; returns True if removed, False if nothing deleted."""
    try:
        # protect owner
        owner_id = await db.fetchval("SELECT owner_id FROM lists WHERE id = $1", list_id)
        if owner_id == collaborator_user_id:
            return False

        status = await db.execute(
            "DELETE FROM list_collaborators WHERE list_id = $1 AND user_id = $2",
            list_id,
            collaborator_user_id,
        )
        return int(status.split(" ")[1]) > 0
    except Exception as exc:  # pragma: no cover
        logger.error("Error deleting collaborator: %s", exc, exc_info=True)
        raise DatabaseInteractionError("Database error removing collaborator.") from exc


# --------------------------------------------------------------------------- #
#  Permission utilities (used by dependency helpers)                          #
# --------------------------------------------------------------------------- #
async def check_list_ownership(db: asyncpg.Connection, list_id: int, user_id: int) -> None:
    """Raises 404/403 custom errors when ownership check fails."""
    try:
        owner_match = await db.fetchval(
            "SELECT EXISTS (SELECT 1 FROM lists WHERE id = $1 AND owner_id = $2)",
            list_id,
            user_id,
        )
        if owner_match:
            return

        list_exists = await db.fetchval("SELECT EXISTS (SELECT 1 FROM lists WHERE id = $1)", list_id)
        raise (ListAccessDeniedError if list_exists else ListNotFoundError)()
    except (ListNotFoundError, ListAccessDeniedError):
        raise
    except Exception as exc:  # pragma: no cover
        logger.error("Ownership check failed: %s", exc, exc_info=True)
        raise DatabaseInteractionError("Database error during ownership check.") from exc


async def check_list_access(
    db: asyncpg.Connection, *, list_id: int, user_id: int
) -> None:
    """
    Raise:
        ListNotFoundError        – list_id doesn't exist
        ListAccessDeniedError    – user_id is neither owner nor collaborator
    """
    # 1️⃣  Does the list exist and is user either owner or collaborator?
    rec = await db.fetchrow(
        """
        SELECT 1
        FROM   lists            l
        LEFT JOIN list_collaborators lc
               ON lc.list_id = l.id
              AND lc.user_id = $2          -- ← correct column name
        WHERE  l.id       = $1
          AND (l.owner_id = $2 OR lc.user_id IS NOT NULL)
        """,
        list_id,
        user_id,
    )

    if rec:
        return                        # ✅ access ok

    # 2️⃣  Distinguish “not found” vs “no access”
    exists = await db.fetchval(
        "SELECT 1 FROM lists WHERE id = $1",
        list_id,
    )
    if not exists:
        raise ListNotFoundError(list_id)
    raise ListAccessDeniedError(list_id, user_id)

# --------------------------------------------------------------------------- #
#  Collaboration helpers (owner / member / add / list)                        #
# --------------------------------------------------------------------------- #
async def is_owner(db: asyncpg.Connection, list_id: int, user_id: int) -> bool:
    return bool(
        await db.fetchval(
            "SELECT 1 FROM lists WHERE id=$1 AND owner_id=$2",
            list_id,
            user_id,
        )
    )


async def is_member(db: asyncpg.Connection, list_id: int, user_id: int) -> bool:
    return bool(
        await db.fetchval(
            """
            SELECT 1
            FROM lists              WHERE id=$1 AND owner_id=$2
            UNION ALL
            SELECT 1
            FROM list_collaborators WHERE list_id=$1 AND user_id=$2
            LIMIT 1
            """,
            list_id,
            user_id,
        )
    )


async def add_member(
    db: asyncpg.Connection, *, list_id: int, user_id: int, role: str = "viewer"
) -> None:
    await db.execute(
        """
        INSERT INTO list_collaborators (list_id, user_id, role)
        VALUES ($1, $2, $3)
        ON CONFLICT (list_id, user_id) DO UPDATE SET role = EXCLUDED.role
        """,
        list_id,
        user_id,
        role,
    )


async def fetch_members(db: asyncpg.Connection, list_id: int) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT u.id, u.display_name, u.email
        FROM list_collaborators lc
        JOIN users u ON u.id = lc.user_id
        WHERE lc.list_id = $1
        UNION ALL            -- include the owner
        SELECT u.id, u.display_name, u.email
        FROM lists l JOIN users u ON u.id = l.owner_id
        WHERE l.id = $1
        """,
        list_id,
    )

