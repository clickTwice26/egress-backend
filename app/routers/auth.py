import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from ..config import get_settings
from ..deps import CurrentUser, DbSession, get_bearer_token
from ..models import User
from ..schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    MessageOut,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
)
from ..security import dummy_verify, hash_password, needs_rehash, verify_password
from ..sessions import (
    IssuedTokens,
    create_session,
    revoke_by_access_token,
    rotate_session,
)
from ..turnstile import client_ip_from_request, verify_turnstile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_TURNSTILE_FAILED = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Verification failed. Please complete the challenge and try again.",
)

# Deliberately identical for unknown-email and wrong-password so the endpoint
# cannot be used to enumerate registered addresses.
_BAD_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _token_pair(issued: IssuedTokens, user: User) -> TokenPair:
    settings = get_settings()
    return TokenPair(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=settings.access_token_ttl_minutes * 60,
        refresh_expires_at=issued.session.refresh_expires_at,
        user=UserOut.model_validate(user),
    )


async def _require_turnstile(request: Request, token: str) -> None:
    """Gate the handler on a successful siteverify. Everything below runs only
    after Cloudflare returns success === true."""
    if not await verify_turnstile(token, client_ip_from_request(request)):
        raise _TURNSTILE_FAILED


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(request: Request, payload: RegisterRequest, db: DbSession) -> TokenPair:
    await _require_turnstile(request, payload.turnstile_token)

    email = payload.email.lower().strip()

    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        # Registration inherently reveals that an address is taken; there is no
        # way around it without an email-confirmation flow. Keep the message
        # neutral and let Turnstile absorb the enumeration pressure.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email is already registered.",
        )

    user = User(
        email=email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        module=payload.module,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    issued = await create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip_from_request(request),
    )
    return _token_pair(issued, user)


@router.post("/login", response_model=TokenPair)
async def login(request: Request, payload: LoginRequest, db: DbSession) -> TokenPair:
    await _require_turnstile(request, payload.turnstile_token)

    email = payload.email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None:
        # Burn comparable CPU so response time does not leak account existence.
        dummy_verify()
        raise _BAD_CREDENTIALS

    if not verify_password(user.password_hash, payload.password):
        raise _BAD_CREDENTIALS

    if not user.is_active:
        raise _BAD_CREDENTIALS

    # Opportunistically upgrade hashes when the Argon2 policy hardens.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        await db.commit()

    issued = await create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip_from_request(request),
    )
    return _token_pair(issued, user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(request: Request, payload: RefreshRequest, db: DbSession) -> TokenPair:
    # No Turnstile here: refresh is a background call with no human present,
    # and the refresh token itself is the credential.
    issued = await rotate_session(
        db,
        payload.refresh_token,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip_from_request(request),
    )
    if issued is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await db.get(User, issued.session.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please sign in again.",
        )
    return _token_pair(issued, user)


@router.post("/logout", response_model=MessageOut)
async def logout(db: DbSession, token: str = Depends(get_bearer_token)) -> MessageOut:
    await revoke_by_access_token(db, token)
    # Idempotent: an already-invalid token still reports success.
    return MessageOut(message="Signed out.")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/forgot-password", response_model=MessageOut)
async def forgot_password(
    request: Request, payload: ForgotPasswordRequest, db: DbSession
) -> MessageOut:
    await _require_turnstile(request, payload.turnstile_token)

    email = payload.email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is not None:
        # Delivery is out of scope for this integration. Wire your mailer here;
        # generate a single-use, expiring reset token at that point.
        logger.info("Password reset requested for a known account.")

    # Always the same response, so this endpoint cannot enumerate accounts.
    return MessageOut(
        message="If that address has an Egress account, a reset link is on its way."
    )
