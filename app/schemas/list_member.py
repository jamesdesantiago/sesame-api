from datetime import datetime
from pydantic import BaseModel, ConfigDict

class ListMemberBase(BaseModel):
    role: str

class ListMemberOut(ListMemberBase):
    id: int
    user_id: int
    invited_at: datetime
    accepted_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
