import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware_utc(dt: datetime | None) -> datetime | None:
    """Coerce a datetime read back from the database to UTC-aware.

    SQLite has no native timezone-aware type, so ``DateTime(timezone=True)``
    round-trips as naive. Values are always written as UTC, so attaching UTC on
    read is correct and keeps comparisons from raising
    "can't compare offset-naive and offset-aware datetimes".
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Stored lowercase so lookups are case-insensitive without a functional index.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Argon2id hash. Never stores or logs the plaintext password.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    module: Mapped[str] = mapped_column(String(20), default="academic", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AuthSession(Base):
    """One row per issued refresh token.

    Refresh tokens rotate on every use. Each rotation writes a new row sharing
    the original ``family_id`` and marks the previous row revoked. Presenting an
    already-rotated token means the token leaked and is being replayed, so the
    entire family is revoked (reuse detection).

    Tokens are stored as SHA-256 digests. They are 256-bit random values, so a
    fast hash is sufficient — there is nothing to brute-force — and a database
    leak alone does not yield usable session tokens.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Groups every rotation of one login. Revoked as a unit on reuse detection.
    family_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    access_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Ceiling for the whole family, carried across rotations so a session
    # cannot be extended indefinitely by refreshing.
    family_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set when this row is superseded by a rotation; distinguishes a normal
    # rotation from an explicit logout.
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")

    __table_args__ = (Index("ix_auth_sessions_family_revoked", "family_id", "revoked_at"),)

    def access_token_valid(self, now: datetime) -> bool:
        return self.revoked_at is None and aware_utc(self.access_expires_at) > now

    def refresh_token_valid(self, now: datetime) -> bool:
        return (
            self.revoked_at is None
            and self.rotated_at is None
            and aware_utc(self.refresh_expires_at) > now
            and aware_utc(self.family_expires_at) > now
        )
