"""The community feed: posts, threaded comments, reactions and shares.

Three things shape this module.

**Reads are paginated by keyset.** Every list endpoint takes an opaque cursor
naming the last row the client saw and returns rows strictly after it. Deep
pages cost the same as shallow ones, and posting mid-scroll never makes a reader
skip or repeat an item the way `OFFSET` does.

**Hydration is batched.** Turning rows into API objects needs three extra facts
per row — the author, the reaction breakdown, and what the viewer reacted with.
Fetching those per row would be 3N queries for an N-row page; here it is two
queries for the whole page, whatever N is (authors ride along on the row itself
via a joined load).

**Counts are maintained, not computed.** Comment, reaction and share totals live
on the parent row and move by `x = x + 1` inside the same transaction as the
write, so two people commenting at once cannot lose a count.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Select, func, or_, select, update

from ..community import (
    CursorError,
    can_delete,
    can_edit,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from ..deps import CurrentUser, DbSession, require_capability
from ..models import Comment, Post, Reaction, User, aware_utc, utcnow
from ..roles import weight_for
from ..schemas import (
    AuthorOut,
    CommentCreateRequest,
    CommentOut,
    CommentPage,
    CommentUpdateRequest,
    PostCreateRequest,
    PostOut,
    PostPage,
    PostUpdateRequest,
    ReactionRequest,
    ReactionSummary,
)

router = APIRouter(prefix="/api/community", tags=["community"])

# Posting at all is a capability, so a future "read-only" role — one that weighs
# less than the bar — needs no new check here.
Participant = Annotated[User, Depends(require_capability("plan.manage"))]

MODERATE_WEIGHT = weight_for("content.moderate")

_POST_GONE = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")
_COMMENT_GONE = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found.")
_NOT_YOURS = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN, detail="That is not yours to change."
)

# What a removed post or comment says in place of its body.
TOMBSTONE = "This has been removed."


# ---- Hydration -------------------------------------------------------------


def _author_out(user: User) -> AuthorOut:
    return AuthorOut(
        id=user.id, name=user.name, role_label=user.role.label, role_weight=user.role.weight
    )


async def _reaction_context(
    db: DbSession, target_type: str, ids: list[str], viewer_id: str
) -> tuple[dict[str, dict[str, int]], dict[str, str]]:
    """Reaction breakdown and the viewer's own pick, for a whole page at once.

    Two queries regardless of page size. The alternative — asking per row — is
    the classic N+1 that only shows up once a feed has real traffic on it.
    """
    if not ids:
        return {}, {}

    grouped = await db.execute(
        select(Reaction.target_id, Reaction.kind, func.count())
        .where(Reaction.target_type == target_type, Reaction.target_id.in_(ids))
        .group_by(Reaction.target_id, Reaction.kind)
    )
    breakdown: dict[str, dict[str, int]] = {}
    for target_id, kind, count in grouped:
        breakdown.setdefault(target_id, {})[kind] = count

    mine = await db.execute(
        select(Reaction.target_id, Reaction.kind).where(
            Reaction.target_type == target_type,
            Reaction.target_id.in_(ids),
            Reaction.user_id == viewer_id,
        )
    )
    viewer = {target_id: kind for target_id, kind in mine}
    return breakdown, viewer


def _post_out(
    post: Post,
    breakdown: dict[str, dict[str, int]],
    viewer: dict[str, str],
    *,
    include_shared: bool = True,
) -> PostOut:
    deleted = post.deleted
    shared: PostOut | None = None
    if include_shared and post.shared_post is not None:
        # One level only: sharing a share resolves to the original, so this
        # never recurses and a page can never fan out into a chain of fetches.
        shared = _post_out(post.shared_post, breakdown, viewer, include_shared=False)

    return PostOut(
        id=post.id,
        author=_author_out(post.author),
        body=TOMBSTONE if deleted else post.body,
        created_at=post.created_at,
        edited_at=post.edited_at,
        deleted=deleted,
        comment_count=post.comment_count,
        reaction_count=post.reaction_count,
        share_count=post.share_count,
        reactions={} if deleted else breakdown.get(post.id, {}),
        viewer_reaction=viewer.get(post.id),
        shared_post=shared,
    )


def _comment_out(
    comment: Comment, breakdown: dict[str, dict[str, int]], viewer: dict[str, str]
) -> CommentOut:
    deleted = comment.deleted
    return CommentOut(
        id=comment.id,
        post_id=comment.post_id,
        parent_id=comment.parent_id,
        author=_author_out(comment.author),
        body=TOMBSTONE if deleted else comment.body,
        created_at=comment.created_at,
        edited_at=comment.edited_at,
        deleted=deleted,
        reply_count=comment.reply_count,
        reaction_count=comment.reaction_count,
        reactions={} if deleted else breakdown.get(comment.id, {}),
        viewer_reaction=viewer.get(comment.id),
    )


async def _hydrate_posts(db: DbSession, posts: list[Post], viewer_id: str) -> list[PostOut]:
    # Shared originals need their own reaction context, so they are counted in.
    ids = [post.id for post in posts]
    ids += [post.shared_post_id for post in posts if post.shared_post_id]
    breakdown, viewer = await _reaction_context(db, "post", list(set(ids)), viewer_id)
    return [_post_out(post, breakdown, viewer) for post in posts]


async def _hydrate_comments(
    db: DbSession, comments: list[Comment], viewer_id: str
) -> list[CommentOut]:
    breakdown, viewer = await _reaction_context(
        db, "comment", [c.id for c in comments], viewer_id
    )
    return [_comment_out(c, breakdown, viewer) for c in comments]


# ---- Keyset pagination -----------------------------------------------------


def _paginate(stmt: Select, model, cursor: str | None, limit: int, *, newest_first: bool) -> Select:
    """Applies the cursor predicate and ordering for one page.

    The predicate is written as `ts < c OR (ts = c AND id < cid)` rather than a
    row-value comparison so it plans identically everywhere and uses the
    (created_at, id) index either way. Ties on the timestamp are broken by id,
    which is why the cursor carries both — without the tiebreak, two rows
    written in the same millisecond would make a page boundary lose one.
    """
    if cursor:
        try:
            stamp, row_id = decode_cursor(cursor)
        except CursorError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor."
            ) from exc

        if newest_first:
            stmt = stmt.where(
                or_(
                    model.created_at < stamp,
                    (model.created_at == stamp) & (model.id < row_id),
                )
            )
        else:
            stmt = stmt.where(
                or_(
                    model.created_at > stamp,
                    (model.created_at == stamp) & (model.id > row_id),
                )
            )

    order = (
        (model.created_at.desc(), model.id.desc())
        if newest_first
        else (model.created_at.asc(), model.id.asc())
    )
    # One extra row is fetched, never returned: its existence is how we know
    # whether to hand back a cursor, without a second COUNT query.
    return stmt.order_by(*order).limit(limit + 1)


def _split_page(rows: list, limit: int) -> tuple[list, str | None]:
    has_more = len(rows) > limit
    page = rows[:limit]
    if not has_more or not page:
        return page, None
    last = page[-1]
    return page, encode_cursor(aware_utc(last.created_at), last.id)


# ---- Posts -----------------------------------------------------------------


@router.get("/posts", response_model=PostPage)
async def list_feed(
    user: CurrentUser,
    db: DbSession,
    cursor: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
) -> PostPage:
    """The feed, newest first."""
    size = clamp_limit(limit)
    stmt = _paginate(
        select(Post).where(Post.deleted_at.is_(None)), Post, cursor, size, newest_first=True
    )
    rows = list((await db.execute(stmt)).unique().scalars())
    page, next_cursor = _split_page(rows, size)
    return PostPage(items=await _hydrate_posts(db, page, user.id), next_cursor=next_cursor)


@router.post("/posts", response_model=PostOut, status_code=status.HTTP_201_CREATED)
async def create_post(payload: PostCreateRequest, user: Participant, db: DbSession) -> PostOut:
    """Write a post, or share an existing one with an optional comment."""
    shared_id: str | None = None

    if payload.shared_post_id:
        original = await db.get(Post, payload.shared_post_id)
        if original is None or original.deleted:
            raise _POST_GONE
        # Sharing a share credits the original, so the chain stays one deep and
        # the count lands where the content actually is.
        shared_id = original.shared_post_id or original.id
        await db.execute(
            update(Post).where(Post.id == shared_id).values(share_count=Post.share_count + 1)
        )
    elif not payload.body:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Write something first."
        )

    post = Post(author_id=user.id, body=payload.body, shared_post_id=shared_id)
    db.add(post)
    await db.commit()

    created = await _load_post(db, post.id)
    return (await _hydrate_posts(db, [created], user.id))[0]


async def _load_post(db: DbSession, post_id: str) -> Post:
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.unique().scalar_one_or_none()
    if post is None:
        raise _POST_GONE
    return post


@router.get("/posts/{post_id}", response_model=PostOut)
async def get_post(post_id: str, user: CurrentUser, db: DbSession) -> PostOut:
    """One post, for the thread page. A removed post still resolves, as a
    tombstone, so a shared link does not 404 on someone mid-conversation."""
    post = await _load_post(db, post_id)
    return (await _hydrate_posts(db, [post], user.id))[0]


@router.patch("/posts/{post_id}", response_model=PostOut)
async def update_post(
    post_id: str, payload: PostUpdateRequest, user: Participant, db: DbSession
) -> PostOut:
    post = await _load_post(db, post_id)
    if post.deleted:
        raise _POST_GONE
    if not can_edit(post.author_id, user.id):
        raise _NOT_YOURS

    post.body = payload.body
    post.edited_at = utcnow()
    await db.commit()

    refreshed = await _load_post(db, post_id)
    return (await _hydrate_posts(db, [refreshed], user.id))[0]


@router.delete("/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(post_id: str, user: Participant, db: DbSession) -> Response:
    """Soft delete: authors remove their own, moderators remove any."""
    post = await _load_post(db, post_id)
    if not can_delete(post.author_id, user.id, user.role_weight, MODERATE_WEIGHT):
        raise _NOT_YOURS

    if not post.deleted:
        post.deleted_at = utcnow()
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- Comments and replies --------------------------------------------------


@router.get("/posts/{post_id}/comments", response_model=CommentPage)
async def list_comments(
    post_id: str,
    user: CurrentUser,
    db: DbSession,
    cursor: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
) -> CommentPage:
    """Top-level comments on a post, oldest first.

    Oldest first, unlike the feed: a conversation reads top to bottom, and a
    reply loses its meaning when it arrives above the line it answers.
    """
    await _load_post(db, post_id)
    size = clamp_limit(limit)
    stmt = _paginate(
        select(Comment).where(Comment.post_id == post_id, Comment.parent_id.is_(None)),
        Comment,
        cursor,
        size,
        newest_first=False,
    )
    rows = list((await db.execute(stmt)).unique().scalars())
    page, next_cursor = _split_page(rows, size)
    return CommentPage(items=await _hydrate_comments(db, page, user.id), next_cursor=next_cursor)


@router.get("/comments/{comment_id}", response_model=CommentOut)
async def get_comment(comment_id: str, user: CurrentUser, db: DbSession) -> CommentOut:
    comment = await _load_comment(db, comment_id)
    return (await _hydrate_comments(db, [comment], user.id))[0]


@router.get("/comments/{comment_id}/replies", response_model=CommentPage)
async def list_replies(
    comment_id: str,
    user: CurrentUser,
    db: DbSession,
    cursor: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
) -> CommentPage:
    """Replies to one comment — the same shape as comments on a post, so a
    reply thread and a post thread are the same page with a different root."""
    await _load_comment(db, comment_id)
    size = clamp_limit(limit)
    stmt = _paginate(
        select(Comment).where(Comment.parent_id == comment_id),
        Comment,
        cursor,
        size,
        newest_first=False,
    )
    rows = list((await db.execute(stmt)).unique().scalars())
    page, next_cursor = _split_page(rows, size)
    return CommentPage(items=await _hydrate_comments(db, page, user.id), next_cursor=next_cursor)


async def _load_comment(db: DbSession, comment_id: str) -> Comment:
    result = await db.execute(select(Comment).where(Comment.id == comment_id))
    comment = result.unique().scalar_one_or_none()
    if comment is None:
        raise _COMMENT_GONE
    return comment


@router.post(
    "/posts/{post_id}/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED
)
async def create_comment(
    post_id: str, payload: CommentCreateRequest, user: Participant, db: DbSession
) -> CommentOut:
    """Comment on a post, or reply to a comment by naming its id as parent."""
    post = await _load_post(db, post_id)
    if post.deleted:
        raise _POST_GONE

    parent: Comment | None = None
    if payload.parent_id:
        parent = await _load_comment(db, payload.parent_id)
        if parent.post_id != post_id:
            # Refusing rather than re-homing: a reply that jumps threads would
            # read as an answer to something its author never saw.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That comment belongs to a different post.",
            )
        if parent.deleted:
            raise _COMMENT_GONE

    comment = Comment(
        post_id=post_id,
        parent_id=parent.id if parent else None,
        author_id=user.id,
        body=payload.body,
    )
    db.add(comment)

    # Every comment counts towards the post, replies included: the number on the
    # post means "how much conversation is in here", not "how many top lines".
    await db.execute(
        update(Post).where(Post.id == post_id).values(comment_count=Post.comment_count + 1)
    )
    if parent is not None:
        await db.execute(
            update(Comment)
            .where(Comment.id == parent.id)
            .values(reply_count=Comment.reply_count + 1)
        )

    await db.commit()
    created = await _load_comment(db, comment.id)
    return (await _hydrate_comments(db, [created], user.id))[0]


@router.patch("/comments/{comment_id}", response_model=CommentOut)
async def update_comment(
    comment_id: str, payload: CommentUpdateRequest, user: Participant, db: DbSession
) -> CommentOut:
    comment = await _load_comment(db, comment_id)
    if comment.deleted:
        raise _COMMENT_GONE
    if not can_edit(comment.author_id, user.id):
        raise _NOT_YOURS

    comment.body = payload.body
    comment.edited_at = utcnow()
    await db.commit()

    refreshed = await _load_comment(db, comment_id)
    return (await _hydrate_comments(db, [refreshed], user.id))[0]


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(comment_id: str, user: Participant, db: DbSession) -> Response:
    """Soft delete, so replies underneath keep their parent and their meaning."""
    comment = await _load_comment(db, comment_id)
    if not can_delete(comment.author_id, user.id, user.role_weight, MODERATE_WEIGHT):
        raise _NOT_YOURS

    if not comment.deleted:
        comment.deleted_at = utcnow()
        # The comment is gone from the conversation, so it stops counting
        # towards it — the replies it still carries do not.
        await db.execute(
            update(Post)
            .where(Post.id == comment.post_id, Post.comment_count > 0)
            .values(comment_count=Post.comment_count - 1)
        )
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- Reactions -------------------------------------------------------------


async def _reaction_target(db: DbSession, target_type: str, target_id: str) -> Post | Comment:
    target = (
        await _load_post(db, target_id)
        if target_type == "post"
        else await _load_comment(db, target_id)
    )
    if target.deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That has been removed."
        )
    return target


async def _summarise(
    db: DbSession, target_type: str, target_id: str, viewer_id: str
) -> ReactionSummary:
    model = Post if target_type == "post" else Comment
    total = await db.scalar(select(model.reaction_count).where(model.id == target_id))
    breakdown, viewer = await _reaction_context(db, target_type, [target_id], viewer_id)
    return ReactionSummary(
        target_type=target_type,
        target_id=target_id,
        reaction_count=total or 0,
        reactions=breakdown.get(target_id, {}),
        viewer_reaction=viewer.get(target_id),
    )


@router.put("/reactions", response_model=ReactionSummary)
async def react(payload: ReactionRequest, user: Participant, db: DbSession) -> ReactionSummary:
    """React, or change an existing reaction.

    Idempotent by design: the unique constraint on (user, target) means a second
    tap swaps the kind instead of stacking a second reaction, so the total only
    moves when a reaction is genuinely added.
    """
    await _reaction_target(db, payload.target_type, payload.target_id)
    model = Post if payload.target_type == "post" else Comment

    existing = await db.scalar(
        select(Reaction).where(
            Reaction.user_id == user.id,
            Reaction.target_type == payload.target_type,
            Reaction.target_id == payload.target_id,
        )
    )

    if existing is None:
        db.add(
            Reaction(
                user_id=user.id,
                target_type=payload.target_type,
                target_id=payload.target_id,
                kind=payload.kind,
            )
        )
        await db.execute(
            update(model)
            .where(model.id == payload.target_id)
            .values(reaction_count=model.reaction_count + 1)
        )
    else:
        # Changing kind leaves the total alone — same person, same one reaction.
        existing.kind = payload.kind

    await db.commit()
    return await _summarise(db, payload.target_type, payload.target_id, user.id)


@router.delete("/reactions", response_model=ReactionSummary)
async def unreact(
    user: Participant,
    db: DbSession,
    target_type: Literal["post", "comment"] = Query(...),
    target_id: str = Query(...),
) -> ReactionSummary:
    """Take a reaction back. Removing one that is not there is not an error."""
    model = Post if target_type == "post" else Comment

    existing = await db.scalar(
        select(Reaction).where(
            Reaction.user_id == user.id,
            Reaction.target_type == target_type,
            Reaction.target_id == target_id,
        )
    )
    if existing is not None:
        await db.delete(existing)
        # Floored at zero: a counter that has drifted must not go negative and
        # start rendering "-1 reactions".
        await db.execute(
            update(model)
            .where(model.id == target_id, model.reaction_count > 0)
            .values(reaction_count=model.reaction_count - 1)
        )
        await db.commit()

    return await _summarise(db, target_type, target_id, user.id)
