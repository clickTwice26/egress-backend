from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import User
from .roles import weight_for
from .sessions import resolve_access_token

# auto_error=False so a missing header produces our own 401 with a
# WWW-Authenticate challenge rather than FastAPI's default 403.
bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    if credentials is None or not credentials.credentials:
        raise _UNAUTHORIZED
    if credentials.scheme.lower() != "bearer":
        raise _UNAUTHORIZED
    return credentials.credentials


async def get_current_user(
    token: Annotated[str, Depends(get_bearer_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    user = await resolve_access_token(db, token)
    if user is None:
        raise _UNAUTHORIZED
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


def require_weight(minimum: int) -> Callable[[User], Awaitable[User]]:
    """Dependency that admits any account weighing at least ``minimum``.

    The comparison is purely numeric. No role name appears here, so adding a
    role — anywhere on the scale — needs no change to this guard or to anything
    that uses it.
    """

    async def guard(user: CurrentUser) -> User:
        if user.role_weight < minimum:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                # Deliberately vague: the caller learns they may not, not what
                # the threshold is or who does clear it.
                detail="Your account does not have access to this.",
            )
        return user

    return guard


def require_capability(capability: str) -> Callable[[User], Awaitable[User]]:
    """``require_weight`` with the threshold looked up by capability name.

    The name is a label for a number, resolved once at import time — the check
    itself is still ``weight >= n``.
    """
    return require_weight(weight_for(capability))
