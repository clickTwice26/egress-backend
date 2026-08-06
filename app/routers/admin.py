"""Administration: who is here, and what the place looks like.

Every endpoint sits behind the same weight bar as role assignment. That is one
`require_capability` on the router rather than a check per handler, so a new
endpoint added here is gated by construction — the failure mode of per-handler
checks is the handler someone forgets.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select

from ..community import CursorError, clamp_limit, decode_cursor, encode_cursor
from ..deps import DbSession, require_capability
from ..models import Comment, Post, Reaction, Role, StudyPlan, User, aware_utc
from ..schemas import AdminStats, AdminUserOut, AdminUserPage, RoleTally

# The dependency is declared on the router, so it runs for every route below.
router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_capability("roles.assign"))],
)


@router.get("/stats", response_model=AdminStats)
async def get_stats(db: DbSession) -> AdminStats:
    """Totals for the overview.

    Six counts in six queries. Cheap at this size, and honest: nothing here is
    cached, so the numbers are what the database says right now.
    """
    members = await db.scalar(select(func.count()).select_from(User)) or 0
    posts = (
        await db.scalar(select(func.count()).select_from(Post).where(Post.deleted_at.is_(None)))
        or 0
    )
    comments = (
        await db.scalar(
            select(func.count()).select_from(Comment).where(Comment.deleted_at.is_(None))
        )
        or 0
    )
    reactions = await db.scalar(select(func.count()).select_from(Reaction)) or 0
    plans = await db.scalar(select(func.count()).select_from(StudyPlan)) or 0

    # One grouped query for the whole ladder, including roles nobody holds —
    # an outer join, so an empty rung still shows as zero rather than vanishing.
    tallies = await db.execute(
        select(Role.slug, Role.label, Role.weight, func.count(User.id))
        .select_from(Role)
        .outerjoin(User, User.role_slug == Role.slug)
        .group_by(Role.slug, Role.label, Role.weight)
        .order_by(Role.weight)
    )

    return AdminStats(
        members=members,
        roles=[
            RoleTally(slug=slug, label=label, weight=weight, member_count=count)
            for slug, label, weight, count in tallies
        ],
        posts=posts,
        comments=comments,
        reactions=reactions,
        study_plans=plans,
    )


@router.get("/users", response_model=AdminUserPage)
async def list_users(
    db: DbSession,
    cursor: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
    search: str | None = None,
    role: str | None = None,
) -> AdminUserPage:
    """Members, newest first, optionally filtered by name, email or role.

    Same keyset pagination as the feed, for the same reason: an administrator
    scrolling a member list while people sign up should not see rows shift
    under them.
    """
    size = clamp_limit(limit)
    stmt = select(User)

    if search:
        # Emails are stored lower-cased; names are not, so both sides are
        # folded for the comparison.
        needle = f"%{search.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(User.name).like(needle), User.email.like(needle)))
    if role:
        stmt = stmt.where(User.role_slug == role)

    if cursor:
        try:
            stamp, row_id = decode_cursor(cursor)
        except CursorError:
            # A bad cursor reads as "start again" rather than an error: there is
            # nothing an administrator can do about a malformed token.
            stamp, row_id = None, None
        if stamp is not None:
            stmt = stmt.where(
                or_(User.created_at < stamp, (User.created_at == stamp) & (User.id < row_id))
            )

    stmt = stmt.order_by(User.created_at.desc(), User.id.desc()).limit(size + 1)
    rows = list((await db.execute(stmt)).unique().scalars())

    has_more = len(rows) > size
    page = rows[:size]
    next_cursor = (
        encode_cursor(aware_utc(page[-1].created_at), page[-1].id) if has_more and page else None
    )

    return AdminUserPage(
        items=[AdminUserOut.model_validate(user) for user in page], next_cursor=next_cursor
    )
