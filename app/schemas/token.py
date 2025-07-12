# backend/app/schemas/token.py
from pydantic import BaseModel, EmailStr, Field
from typing import Optional

# Schema representing the relevant data extracted from a verified Firebase ID token
class FirebaseTokenData(BaseModel):
    uid: str = Field(..., description="Firebase User ID")
    email: Optional[EmailStr] = Field(None, description="User's email address (if available in token)")
    name: Optional[str] = Field(None, description="User's display name (if available in token)")
    picture: Optional[str] = Field(None, description="URL to user's profile picture (if available in token)")
    # Add other fields from the decoded token dictionary if needed by your application logic
    # For example:
    # iss: str
    # aud: str
    # auth_time: int
    # user_id: str # Usually same as uid
    # sub: str # Usually same as uid
    # iat: int
    # exp: int
    # email_verified: bool
    # firebase: dict # Contains provider info

    # Allow extra fields from the decoded token dict without causing validation errors
    model_config = {"extra": "ignore"}