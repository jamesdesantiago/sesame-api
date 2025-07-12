# app/schemas/collaboration.py
from typing import Literal
from pydantic import BaseModel, Field

class CollaboratorInvite(BaseModel):
    """
    Payload accepted by POST /lists/{id}/collaborators
    """
    user_id: int = Field(..., ge=1, description="ID of the user to invite")
    role: Literal["viewer", "editor"] = Field(
        "viewer", description="Permission this collaborator will receive"
    )
