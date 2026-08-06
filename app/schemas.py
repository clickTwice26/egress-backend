from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .community import MAX_COMMENT_BODY, MAX_POST_BODY

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


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    label: str
    # The only field a privilege decision should ever read.
    weight: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    module: str
    role: RoleOut
    created_at: datetime


class AuthorOut(BaseModel):
    """The public face of an account. No email — the feed is not a directory."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    role_label: str
    # Carried so the client can badge staff by weight rather than by role name.
    role_weight: int


class PostOut(BaseModel):
    id: str
    author: AuthorOut
    body: str
    created_at: datetime
    edited_at: datetime | None
    # A removed post keeps its place in every thread that quotes it; the client
    # renders a tombstone instead of the body.
    deleted: bool
    comment_count: int
    reaction_count: int
    share_count: int
    # kind -> count, for the summary row. Absent kinds are simply not present.
    reactions: dict[str, int]
    # What the caller themselves reacted with, if anything.
    viewer_reaction: str | None
    # The post this one shares, resolved one level deep and never further.
    shared_post: "PostOut | None" = None


class CommentOut(BaseModel):
    id: str
    post_id: str
    parent_id: str | None
    author: AuthorOut
    body: str
    created_at: datetime
    edited_at: datetime | None
    deleted: bool
    reply_count: int
    reaction_count: int
    reactions: dict[str, int]
    viewer_reaction: str | None


class PostPage(BaseModel):
    items: list[PostOut]
    # Feed the cursor back to fetch the next page; null means the end.
    next_cursor: str | None


class CommentPage(BaseModel):
    items: list[CommentOut]
    next_cursor: str | None


class PostCreateRequest(BaseModel):
    body: str = Field(default="", max_length=MAX_POST_BODY)
    shared_post_id: str | None = None

    @field_validator("body")
    @classmethod
    def clean_body(cls, value: str) -> str:
        return value.strip()


class PostUpdateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_POST_BODY)

    @field_validator("body")
    @classmethod
    def clean_body(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A post cannot be empty.")
        return cleaned


class CommentCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_COMMENT_BODY)
    # Set to reply to a comment; absent for a comment on the post itself.
    parent_id: str | None = None

    @field_validator("body")
    @classmethod
    def clean_body(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A comment cannot be empty.")
        return cleaned


class CommentUpdateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_COMMENT_BODY)

    @field_validator("body")
    @classmethod
    def clean_body(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A comment cannot be empty.")
        return cleaned


class ReactionRequest(BaseModel):
    target_type: Literal["post", "comment"]
    target_id: str
    kind: Literal["like", "love", "insightful", "celebrate", "curious"]


class ReactionSummary(BaseModel):
    """Returned after reacting, so the client corrects its optimistic guess."""

    target_type: str
    target_id: str
    reaction_count: int
    reactions: dict[str, int]
    viewer_reaction: str | None


class ProfileUpdateRequest(BaseModel):
    """What an account may change about itself.

    Email is absent on purpose: it identifies the account and is the recovery
    channel, so changing it needs a confirmation flow that does not exist yet.
    Role is absent too — that moves only through the role endpoints, which are
    weight-gated, or anyone could promote themselves here.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    module: Literal["academic", "general"] | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank.")
        return cleaned


class RoleAssignRequest(BaseModel):
    role_slug: str = Field(min_length=1, max_length=30)


class RoleCreateRequest(BaseModel):
    """Adds a rung to the ladder. Weight is the whole point of the row."""

    slug: str = Field(min_length=1, max_length=30, pattern=r"^[a-z][a-z0-9-]*$")
    label: str = Field(min_length=1, max_length=60)
    weight: int = Field(ge=0, le=1000)


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


# A hand-made task must describe itself in the same vocabulary the generator
# uses, so the calendar dots and chips render it exactly like a generated one.
TaskSkill = Literal["listening", "reading", "writing", "speaking"]
TaskKind = Literal["drill", "mock", "review", "exam"]
TaskTitle = Annotated[str, Field(min_length=1, max_length=200)]
# A whole day is the ceiling; zero is allowed for an untimed reminder.
TaskMinutes = Annotated[int, Field(ge=0, le=1440)]


class StudyTaskCreate(BaseModel):
    """A session the user adds to their plan by hand."""

    scheduled_on: date
    skill: TaskSkill
    kind: TaskKind
    title: TaskTitle
    minutes: TaskMinutes = 45

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Give the session a title.")
        return stripped


class StudyTaskUpdate(BaseModel):
    """A partial edit. Every field is optional; omitted ones stay as they are."""

    scheduled_on: date | None = None
    skill: TaskSkill | None = None
    kind: TaskKind | None = None
    title: TaskTitle | None = None
    minutes: TaskMinutes | None = None
    completed: bool | None = None

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("Give the session a title.")
        return stripped
