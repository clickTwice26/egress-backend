from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# Turnstile posts the token under this exact key. Accepting the hyphenated
# form keeps the frontend payload identical to a native form submission.
TURNSTILE_FIELD = "cf-turnstile-response"

Password = Annotated[str, Field(min_length=8, max_length=200)]


class TurnstileProtected(BaseModel):
    """Base for any request that must carry a Turnstile token."""

    model_config = ConfigDict(populate_by_name=True)

    turnstile_token: str = Field(default="", alias=TURNSTILE_FIELD)


class RegisterRequest(TurnstileProtected):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: Password
    module: Literal["academic", "general"] = "academic"

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank.")
        return cleaned


class LoginRequest(TurnstileProtected):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class ForgotPasswordRequest(TurnstileProtected):
    email: EmailStr


class RefreshRequest(BaseModel):
    # The refresh token is a credential, so it travels in the body rather than
    # a URL where it could land in logs or browser history.
    refresh_token: str = Field(min_length=1)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    module: str
    created_at: datetime


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"
    # Seconds until the access token expires, for the client's refresh timer.
    expires_in: int
    # Absolute expiry of the refresh token, so the client knows the session length.
    refresh_expires_at: datetime
    user: UserOut


class MessageOut(BaseModel):
    message: str


# ---- Study plan ----

# IELTS reports in half bands from 4.0; below that the plan advice does not
# meaningfully differ.
Band = Annotated[float, Field(ge=4.0, le=9.0, multiple_of=0.5)]


class SkillBands(BaseModel):
    listening: Band
    reading: Band
    writing: Band
    speaking: Band


class StudyPlanIntake(BaseModel):
    """What the user tells us before a plan can be generated."""

    test_date: date
    target_band: Band
    current_bands: SkillBands
    hours_per_week: int = Field(ge=1, le=40)


class StudyTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scheduled_on: date
    position: int
    skill: str
    kind: str
    title: str
    minutes: int
    completed: bool


class StudyPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    test_date: date
    target_band: float
    hours_per_week: int
    current_bands: SkillBands
    created_at: datetime
    tasks: list[StudyTaskOut]


class TaskCompletionRequest(BaseModel):
    completed: bool
