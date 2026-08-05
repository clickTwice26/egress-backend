"""Bearer-token session lifecycle.

Design notes:

* Two tokens. A short-lived **access token** is sent as
  ``Authorization: Bearer <token>`` on every request. A long-lived
  **refresh token** exchanges for a new pair and is the only thing that
  survives 30+ days.
* Opaque, not JWT. Tokens are 256-bit random strings looked up in the
  database, so revocation is immediate. A stateless JWT cannot be revoked
  before it expires, which is unacceptable for a 60-day session.
* Stored hashed. Only SHA-256 digests are persisted, so a database leak
  does not yield usable tokens.
* Rotation with reuse detection. Every refresh issues a new token and
  retires the old one. Replaying a retired token means it leaked, so the
  whole family is revoked and the attacker and user are both logged out.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import AuthSession, User, aware_utc, utcnow
from .security import generate_token, hash_token


class IssuedTokens:
    """Plaintext tokens, returned to the client exactly once."""

    def __init__(self, access_token: str, refresh_token: str, session: AuthSession):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.session = session


async def create_session(
    db: AsyncSession,
    user: User,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedTokens:
    """Start a new session family for a fresh login or registration."""
    settings = get_settings()
    now = utcnow()

    access_token = generate_token()
    refresh_token = generate_token()

    session = AuthSession(
        user_id=user.id,
        family_id=generate_token()[:36],
        refresh_token_hash=hash_token(refresh_token),
        access_token_hash=hash_token(access_token),
        issued_at=now,
        access_expires_at=now + timedelta(minutes=settings.access_token_ttl_minutes),
        refresh_expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
        family_expires_at=now + timedelta(days=settings.session_absolute_ttl_days),
        user_agent=(user_agent or "")[:400] or None,
        ip_address=(ip_address or "")[:45] or None,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    return IssuedTokens(access_token, refresh_token, session)


async def revoke_family(db: AsyncSession, family_id: str) -> None:
    """Revoke every session in a family (logout-all, or reuse detection)."""
    await db.execute(
        update(AuthSession)
        .where(AuthSession.family_id == family_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    await db.commit()


async def rotate_session(
    db: AsyncSession,
    refresh_token: str,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedTokens | None:
    """Exchange a refresh token for a fresh pair.

    Returns None when the token is unknown, expired, or revoked. If the token
    is recognised but already rotated, the presentation is treated as a replay
    and the entire family is revoked before returning None.
    """
    settings = get_settings()
    now = utcnow()

    result = await db.execute(
        select(AuthSession).where(AuthSession.refresh_token_hash == hash_token(refresh_token))
    )
    session = result.scalar_one_or_none()

    if session is None:
        return None

    # Reuse detection: a token that was already rotated is being replayed.
    if session.rotated_at is not None:
        await revoke_family(db, session.family_id)
        return None

    if not session.refresh_token_valid(now):
        return None

    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None

    access_token = generate_token()
    new_refresh_token = generate_token()

    # Retire the presented token, then chain a new row in the same family.
    session.rotated_at = now
    session.revoked_at = now

    rotated = AuthSession(
        user_id=user.id,
        family_id=session.family_id,
        refresh_token_hash=hash_token(new_refresh_token),
        access_token_hash=hash_token(access_token),
        issued_at=now,
        access_expires_at=now + timedelta(minutes=settings.access_token_ttl_minutes),
        # Sliding refresh window, but never past the family ceiling.
        refresh_expires_at=min(
            now + timedelta(days=settings.refresh_token_ttl_days),
            aware_utc(session.family_expires_at),
        ),
        family_expires_at=aware_utc(session.family_expires_at),
        user_agent=(user_agent or "")[:400] or None,
        ip_address=(ip_address or "")[:45] or None,
    )
    db.add(rotated)
    await db.commit()
    await db.refresh(rotated)

    return IssuedTokens(access_token, new_refresh_token, rotated)


async def resolve_access_token(db: AsyncSession, access_token: str) -> User | None:
    """Look up the user behind a bearer access token, or None if unusable."""
    now = utcnow()

    result = await db.execute(
        select(AuthSession).where(AuthSession.access_token_hash == hash_token(access_token))
    )
    session = result.scalar_one_or_none()

    if session is None or not session.access_token_valid(now):
        return None
    if aware_utc(session.family_expires_at) <= now:
        return None

    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None
    return user


async def revoke_by_access_token(db: AsyncSession, access_token: str) -> bool:
    """Log out the session that owns this access token."""
    result = await db.execute(
        select(AuthSession).where(AuthSession.access_token_hash == hash_token(access_token))
    )
    session = result.scalar_one_or_none()
    if session is None:
        return False
    await revoke_family(db, session.family_id)
    return True


async def purge_expired(db: AsyncSession) -> int:
    """Delete session rows whose family ceiling has passed. Safe to run on boot."""
    from sqlalchemy import delete

    cutoff = datetime.now(timezone.utc)
    result = await db.execute(delete(AuthSession).where(AuthSession.family_expires_at < cutoff))
    await db.commit()
    return result.rowcount or 0
