# app/api/endpoints/collaborators.py
from __future__ import annotations

import logging
from typing import Literal

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from app.api import deps
from app.crud import crud_list, crud_user
from app.crud.crud_list import (
    CollaboratorAlreadyExistsError,
    ListAccessDeniedError,
    ListNotFoundError,
    DatabaseInteractionError as ListDBError,
)
from app.schemas import collaboration
from app.schemas import user as user_schemas
from app.core.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/{list_id}/collaborators",
    tags=["Collaborators", "Lists"],
)


# --------------------------------------------------------------------------- #
#  Pydantic payloads                                                          #
# --------------------------------------------------------------------------- #
class CollaboratorAdd(BaseModel):
    """Payload for POST …/collaborators – **tests send only the email**."""
    email: EmailStr = Field(..., examples=["friend@example.com"])
    role: Literal["viewer", "editor"] = "viewer"

CollaboratorAdd.model_rebuild()


# --------------------------------------------------------------------------- #
#  POST /lists/{id}/collaborators                                             #
# --------------------------------------------------------------------------- #
@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=user_schemas.UsernameSetResponse,
)
@limiter.limit("20/minute")
async def add_collaborator(
    request: Request,
    collaborator: CollaboratorAdd = Body(...),
    list_record: asyncpg.Record = Depends(deps.get_list_and_verify_ownership),
    db: asyncpg.Connection = Depends(deps.get_db),
):
    """
    Invite a collaborator by **e-mail address**.  
    Only the list owner can call this endpoint.
    """
    list_id = list_record["id"]

    try:
        await crud_list.add_collaborator_to_list(
            db=db,
            list_id=list_id,
            collaborator_email=collaborator.email,
        )
        return user_schemas.UsernameSetResponse(message="Collaborator added")

    except CollaboratorAlreadyExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ListDBError as exc:
        logger.error("DB error adding collaborator: %s", exc, exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Database error") from exc


# --------------------------------------------------------------------------- #
#  DELETE /lists/{id}/collaborators/{user_id}                                 #
# --------------------------------------------------------------------------- #
@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@limiter.limit("20/minute")
async def remove_collaborator(
    request: Request,
    list_id: int = Path(..., gt=0),
    user_id: int = Path(..., gt=0),
    _=Depends(deps.verify_list_ownership),  # raises 403/404 for non-owners
    db: asyncpg.Connection = Depends(deps.get_db),
):
    """
    Remove a collaborator by **user ID**.  
    Owner cannot remove themselves.
    """
    try:
        removed = await crud_list.delete_collaborator_from_list(
            db=db, list_id=list_id, collaborator_user_id=user_id
        )
        if not removed:
            # 0 rows affected → either user isn’t a collab, or it was the owner
            list_row = await crud_list.get_list_by_id(db, list_id)
            if list_row and list_row["owner_id"] == user_id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Cannot remove the list owner as a collaborator.",
                )
            # distinguish user-exists vs totally unknown id
            if not await crud_user.check_user_exists(db, user_id):
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, "Collaborator user not found."
                )
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "User is not a collaborator on this list.",
            )

        # removed → nothing to return
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except (ListNotFoundError, ListAccessDeniedError) as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND if isinstance(exc, ListNotFoundError) else status.HTTP_403_FORBIDDEN,
            str(exc),
        ) from exc
    except ListDBError as exc:
        logger.error("DB error removing collaborator: %s", exc, exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Database error") from exc
