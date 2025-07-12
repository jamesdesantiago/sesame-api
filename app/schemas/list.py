# app/schemas/list.py
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field
from pydantic import ConfigDict   # Pydantic-v2 helper


# --------------------------------------------------------------------------- #
#                              Base / mix-in                                  #
# --------------------------------------------------------------------------- #

class _ModelCfgMixin:
    """Common config so schemas accept either alias or raw-DB field names."""
    model_config = ConfigDict(
        populate_by_name=True,   # allow source=snake_case, output=camelCase
        from_attributes=True     # let asyncpg.Record / ORM objects map in
    )


# --------------------------------------------------------------------------- #
#                               Requests                                      #
# --------------------------------------------------------------------------- #

class ListBase(BaseModel):
    """Fields a client can supply when creating/updating a list."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    isPrivate: bool = Field(False, description="True → private, False → public")


class ListCreate(ListBase):
    """POST /lists body – nothing extra yet."""
    pass


class ListUpdate(BaseModel):
    """PATCH /lists/{id} body (all optional)."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    isPrivate: Optional[bool] = None


class CollaboratorAdd(BaseModel):
    email: EmailStr


# --------------------------------------------------------------------------- #
#                               Responses                                     #
# --------------------------------------------------------------------------- #

class ListViewResponse(_ModelCfgMixin, BaseModel):
    """
    Compact list info returned by discovery & pagination endpoints.
    """
    id: int
    name: str
    description: Optional[str] = None

    # ── field names in DB → camelCase in JSON ───────────────────────────── #
    is_private: bool = Field(..., alias="isPrivate")
    place_count: int = 0


class ListDetailResponse(_ModelCfgMixin, BaseModel):
    """
    Full list details (owner or collaborator view).
    """
    id: int
    name: str
    description: Optional[str] = None

    is_private: bool = Field(..., alias="isPrivate")
    is_owner:   bool = Field(..., alias="isOwner")
    collaborators: List[EmailStr] = Field(default_factory=list)

    # Tell Pydantic we accept snake- or camel-case on input and always
    # populate attributes using *internal* snake-case names.
    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
    )


class PaginatedListResponse(_ModelCfgMixin, BaseModel):
    items: List[ListViewResponse]

    page: int
    page_size: int
    total_items: int
    total_pages: int
