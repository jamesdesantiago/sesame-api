# app/utils/list_helpers.py
"""
Utilities for turning raw DB records (or dicts) into the Pydantic response
models declared in `app/schemas/list.py`.
"""
from typing import Any, Mapping, MutableMapping, Sequence, Union

import asyncpg

from app.schemas import list as list_schemas


def _ensure_mutable(record: Union[Mapping[str, Any], asyncpg.Record]) -> MutableMapping[str, Any]:
    """
    asyncpg.Record behaves like a Mapping but is immutable.  
    Convert to dict so we can add / tweak keys.
    """
    return dict(record) if isinstance(record, (asyncpg.Record, Mapping)) else {}


def _compute_is_owner(data: MutableMapping[str, Any], requester_id: int) -> bool:
    """
    Figure out whether *requester_id* is the owner of this list.

    Priority:
    1. If CRUD already supplied an `is_owner` / `isOwner` flag, use it.
    2. Otherwise, if `owner_id` is present, compare it.
    3. As a last resort (e.g. immediately after creation) fall back to `True`
       because the caller *is* the creator.
    """
    if "is_owner" in data:
        return bool(data["is_owner"])
    if "isOwner" in data:                       # camelCase variant
        return bool(data["isOwner"])
    if "owner_id" in data:                      # raw DB column
        return requester_id == data["owner_id"]
    # owner_id not present – assume the requester *is* the owner
    return True


def build_list_detail(
    record: Union[Mapping[str, Any], asyncpg.Record],
    *,
    requester_id: int,
) -> list_schemas.ListDetailResponse:
    """
    Convert a DB record/dict into `ListDetailResponse`, guaranteeing that
    `isOwner` and `collaborators` are always present.

    Parameters
    ----------
    record
        Row returned by `crud_list.get_list_details` (asyncpg.Record or dict).
    requester_id
        Authenticated user making the request – used to determine ownership.
    """
    data: MutableMapping[str, Any] = _ensure_mutable(record)

    # --- derived / guaranteed fields ------------------------------------- #
    data["is_owner"] = _compute_is_owner(data, requester_id)
    data.setdefault("collaborators", [])        # always an array

    # Let Pydantic handle alias conversion & extra-field stripping
    return list_schemas.ListDetailResponse(**data)

