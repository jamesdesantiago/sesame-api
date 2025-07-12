from fastapi import APIRouter, Depends, HTTPException, status
from app.schemas.list_member import ListMemberOut
from app.core.security import get_current_user
from app.crud import crud_list_members as crud

router = APIRouter(prefix="/lists/{list_id}/collaborators", tags=["collaboration"])

@router.get("", response_model=list[ListMemberOut])
async def get_members(list_id: int, current_user=Depends(get_current_user)):
    # TODO: verify current_user is member / owner
    return await crud.list_members(list_id)

@router.post("", response_model=ListMemberOut, status_code=status.HTTP_201_CREATED)
async def invite_member(
    list_id: int,
    user_id: int,                       # or email if you add invites
    role: str = "viewer",
    current_user=Depends(get_current_user),
):
    # TODO: verify owner
    return await crud.add_member(list_id, user_id, role)
