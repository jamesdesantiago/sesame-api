# backend/app/api/endpoints/discovery.py
import logging
import math
from typing import List, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request, status

# Import dependencies, schemas, crud functions
from app.api import deps
from app.schemas import list as list_schemas
# Import specific CRUD functions needed
from app.crud import crud_list # Import crud_list
from app.crud import crud_user # Import crud_user (needed by optional user dep)
# Import specific CRUD exceptions
from app.crud.crud_list import DatabaseInteractionError as ListDBError
# Note: UserCRUDNotFoundError from crud_user.get_or_create_user_by_firebase in deps.get_optional_current_user_id
# is not re-raised as HTTPException by that dependency, it just returns None,
# so no need to catch UserCRUDNotFoundError here.

from app.core.rate_limit import limiter

logger = logging.getLogger(__name__)
router = APIRouter()

tags = ["Discovery"]

# Dependency for optional user ID
async def get_optional_current_user_id(
    db: asyncpg.Connection = Depends(deps.get_db),
    token_data: Optional[deps.token_schemas.FirebaseTokenData] = Depends(deps.get_optional_verified_token_data)
) -> Optional[int]:
    """
    Dependency that attempts to get the user ID if authenticated, returning None otherwise.
    Catches errors during user lookup and returns None instead of raising HTTPException.
    """
    if not token_data:
        return None
    try:
        # Use crud_user.get_or_create_user_by_firebase which handles finding/creating
        # This might raise DatabaseInteractionError or ValueError (if token invalid).
        user_id, _ = await crud_user.get_or_create_user_by_firebase(db=db, token_data=token_data)
        return user_id
    except Exception: # Catch any exception (including DB errors or ValueError) during user lookup
        # Log error but don't fail the request, just proceed as unauthenticated
        # The optional dependency pattern means failure to authenticate or look up
        # the user should result in None, not an error that stops the request.
        logger.error("Error getting user ID for optional auth dependency.", exc_info=True)
        return None


@router.get("/public-lists", response_model=list_schemas.PaginatedListResponse, tags=tags)
@limiter.limit("10/minute")
async def get_public_lists(
    request: Request, # For limiter state
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: asyncpg.Connection = Depends(deps.get_db)
):
    """Get publicly available lists (paginated)."""
    try:
        # crud_list.get_public_lists_paginated raises DatabaseInteractionError (ListDBError)
        list_records, total_items = await crud_list.get_public_lists_paginated(db, page=page, page_size=page_size)
        total_pages = math.ceil(total_items / page_size) if page_size > 0 else 0
        # Note: ListViewResponse expects 'place_count' which should be returned by CRUD
        items = [list_schemas.ListViewResponse(**lst) for lst in list_records]
        return list_schemas.PaginatedListResponse(
            items=items, page=page, page_size=page_size,
            total_items=total_items, total_pages=total_pages
        )
    except ListDBError as e: # Catch specific DB errors from CRUD
        logger.error(f"DB error fetching public lists: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error fetching public lists")
    except Exception as e:
        logger.error(f"Unexpected error fetching public lists: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error fetching public lists")

@router.get("/search-lists", response_model=list_schemas.PaginatedListResponse, tags=tags)
@limiter.limit("15/minute")
async def search_lists(
    request: Request, # For limiter state
    q: str = Query(..., min_length=1, description="Search query for list name or description"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    # Use optional user ID dependency - it handles its own errors by returning None
    current_user_id: Optional[int] = Depends(get_optional_current_user_id),
    db: asyncpg.Connection = Depends(deps.get_db)
):
    """
    Search lists by query. Includes public lists and private lists owned by the user if authenticated.
    """
    try:
        # crud_list.search_lists_paginated raises DatabaseInteractionError (ListDBError)
        list_records, total_items = await crud_list.search_lists_paginated(
            db, query=q, user_id=current_user_id, page=page, page_size=page_size
        )
        total_pages = math.ceil(total_items / page_size) if page_size > 0 else 0
        # Note: ListViewResponse expects 'place_count'
        items = [list_schemas.ListViewResponse(**lst) for lst in list_records]
        return list_schemas.PaginatedListResponse(
            items=items, page=page, page_size=page_size,
            total_items=total_items, total_pages=total_pages
        )
    except ListDBError as e: # Catch specific DB errors from CRUD
        logger.error(f"DB error searching lists for '{q}': {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error searching lists")
    except Exception as e:
        logger.error(f"Unexpected error searching lists for '{q}': {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error searching lists")

@router.get("/recent-lists", response_model=list_schemas.PaginatedListResponse, tags=tags)
@limiter.limit("10/minute")
async def get_recent_lists(
    request: Request, # For limiter state
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50), # Smaller page size for recent?
    # Requires authentication to see user's recent + public
    # deps.get_current_user_id handles 401/404 errors for the user itself
    current_user_id: int = Depends(deps.get_current_user_id),
    db: asyncpg.Connection = Depends(deps.get_db)
):
    """Get recently created lists (public or owned by the user, paginated)."""
    try:
        # crud_list.get_recent_lists_paginated raises DatabaseInteractionError (ListDBError)
        list_records, total_items = await crud_list.get_recent_lists_paginated(
            db, user_id=current_user_id, page=page, page_size=page_size
        )
        total_pages = math.ceil(total_items / page_size) if page_size > 0 else 0
        # Note: ListViewResponse expects 'place_count'
        items = [list_schemas.ListViewResponse(**lst) for lst in list_records]
        return list_schemas.PaginatedListResponse(
            items=items, page=page, page_size=page_size,
            total_items=total_items, total_pages=total_pages
        )
    except ListDBError as e: # Catch specific DB errors from CRUD
        logger.error(f"DB error fetching recent lists for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error fetching recent lists")
    except HTTPException as he: # Propagate errors from dependency
         raise he
    except Exception as e:
        logger.error(f"Unexpected error fetching recent lists for user {current_user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error fetching recent lists")