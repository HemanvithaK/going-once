import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


class RegisterRequest(BaseModel):
    """What the client sends to create an account."""

    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=2, max_length=80)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    """What we hand back after a successful login or refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    """A user as the outside world is allowed to see one."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    display_name: str
    role: UserRole
    created_at: datetime
