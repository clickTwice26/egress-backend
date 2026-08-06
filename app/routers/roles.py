"""Reading the role ladder and moving accounts up and down it.

Every guard in here is a weight comparison. Nothing branches on a role's name,
so a deployment can add "tutor" at 40 or "support" at 70 and the rules below
apply to it unchanged.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from ..deps import CurrentUser, DbSession, require_capability
from ..models import Role, User
from ..roles import CAPABILITY_WEIGHTS
from ..schemas import RoleAssignRequest, RoleCreateRequest, RoleOut, UserOut

router = APIRouter(prefix="/api/roles", tags=["roles"])

# Managing roles is itself a capability, priced in weight like the rest.
ManagerUser = Annotated[User, Depends(require_capability("roles.assign"))]


@router.get("", response_model=list[RoleOut])
async def list_roles(user: CurrentUser, db: DbSession) -> list[Role]:
    """The ladder, lightest first. Any signed-in account may read it."""
    result = await db.execute(select(Role).order_by(Role.weight))
    return list(result.scalars())


@router.get("/capabilities", response_model=dict[str, int])
async def list_capabilities(user: CurrentUser) -> dict[str, int]:
    """What each capability costs in weight.

    Served rather than hard-coded in the client so a threshold moves in one
    place, and so the client gates on the same numbers the server enforces.
    """
    return CAPABILITY_WEIGHTS


@router.post("", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(payload: RoleCreateRequest, manager: ManagerUser, db: DbSession) -> Role:
    """Add a role at any point on the scale."""
    existing = await db.get(Role, payload.slug)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That role already exists.")

    # Nobody may mint a role at or above their own weight and then step into
    # it, which would be self-promotion in two calls.
    if payload.weight >= manager.role_weight:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot create a role at or above your own level.",
        )

    role = Role(slug=payload.slug, label=payload.label, weight=payload.weight)
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return role


@router.patch("/users/{user_id}", response_model=UserOut)
async def assign_role(
    user_id: str, payload: RoleAssignRequest, manager: ManagerUser, db: DbSession
) -> User:
    """Move an account to another role.

    Two weight rules, both numeric:

    * you cannot grant a role at or above your own weight, so nobody promotes
      themselves or a peer past themselves;
    * you cannot alter an account that already weighs as much as you do, so
      admins cannot demote each other.
    """
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    role = await db.get(Role, payload.role_slug)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found.")

    if target.role_weight >= manager.role_weight and target.id != manager.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot change an account at or above your own level.",
        )

    if role.weight >= manager.role_weight:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot grant a role at or above your own level.",
        )

    target.role_slug = role.slug
    await db.commit()
    await db.refresh(target)
    return target
