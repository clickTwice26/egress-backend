"""Authoring practice tests: create, edit, arrange, publish, delete.

Writes are gated on `tests.manage`; reading a *published* test is open to any
signed-in account, because that is the library everyone practises from. Drafts
are visible only to the people who can edit them — a half-written test showing
up in someone's practice list is worse than no test at all.

Structure mirrors the exam: a test owns question groups, a group owns questions.
Deleting cascades down, so removing a group takes its questions with it rather
than leaving them parented to nothing.
"""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select

from ..community import CursorError, clamp_limit, decode_cursor, encode_cursor
from ..content import (
    QUESTION_TYPES,
    TEST_KIND_LABELS,
    MODULE_LABELS,
    SPEAKING_PART_LABELS,
    SPEAKING_PART_TWO_PREP_SECONDS,
    SPEAKING_PART_TWO_TALK_SECONDS,
    SPEAKING_CRITERIA,
    SPEAKING_PARTS,
    WRITING_CRITERIA,
    WRITING_TASKS,
    WRITING_TASK_LABELS,
    WRITING_TASK_MIN_WORDS,
    WRITING_TASK_MINUTES,
    WRITING_TASK_WEIGHT,
    question_type,
    review_test,
)
from ..deps import CurrentUser, DbSession, require_capability
from ..models import Question, QuestionGroup, Test, User, aware_utc, utcnow
from ..schemas import (
    KindSummary,
    QuestionCreateRequest,
    QuestionGroupCreateRequest,
    QuestionGroupOut,
    QuestionGroupUpdateRequest,
    QuestionOut,
    ContentReferenceOut,
    CriterionOut,
    QuestionTypeOut,
    SpeakingPartOut,
    WritingTaskOut,
    QuestionUpdateRequest,
    ReviewIssue,
    TestCreateRequest,
    TestOut,
    TestPage,
    TestReviewOut,
    TestSummaryOut,
    TestUpdateRequest,
)

router = APIRouter(prefix="/api/content", tags=["content"])

# Authors and above. Declared once and applied per write route, so a read route
# added later is not silently gated shut.
Author = Annotated[User, Depends(require_capability("tests.manage"))]

_TEST_GONE = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test not found.")
_GROUP_GONE = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question group not found.")
_QUESTION_GONE = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found.")


def _slugify(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return base[:200] or "test"


async def _unique_slug(db: DbSession, title: str, exclude_id: str | None = None) -> str:
    """A slug nobody else holds, with a counter appended if the base is taken."""
    base = _slugify(title)
    candidate = base
    suffix = 2

    while True:
        stmt = select(Test.id).where(Test.slug == candidate)
        if exclude_id:
            stmt = stmt.where(Test.id != exclude_id)
        if await db.scalar(stmt) is None:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1


async def _load_test(db: DbSession, test_id: str) -> Test:
    test = await db.get(Test, test_id)
    if test is None:
        raise _TEST_GONE
    return test


def _summary(test: Test, question_count: int, group_count: int) -> TestSummaryOut:
    return TestSummaryOut(
        id=test.id,
        kind=test.kind,
        title=test.title,
        slug=test.slug,
        summary=test.summary,
        difficulty=test.difficulty,
        status=test.status,
        module=test.module,
        duration_minutes=test.duration_minutes,
        question_count=question_count,
        group_count=group_count,
        created_at=test.created_at,
        updated_at=test.updated_at,
        author_name=test.created_by.name if test.created_by else None,
    )


def _review(test: Test) -> TestReviewOut:
    issues = review_test(
        kind=test.kind,
        title=test.title,
        audio_url=test.audio_url,
        module=test.module,
        passage=test.passage,
        groups=[
            {
                "type": group.type,
                "options": group.options,
                "body": group.body,
                "image_url": group.image_url,
                "word_limit": group.word_limit,
                "speaking_part": group.speaking_part,
                "writing_task": group.writing_task,
                "questions": [
                    {
                        "number": q.number,
                        "span": q.span,
                        "options": q.options,
                        "answers": q.answers,
                    }
                    for q in group.questions
                ],
            }
            for group in test.groups
        ],
    )
    return TestReviewOut(
        publishable=not issues,
        issues=[ReviewIssue(where=issue.where, problem=issue.problem) for issue in issues],
    )


# ---- Reference data --------------------------------------------------------


@router.get("/question-types", response_model=list[QuestionTypeOut])
async def list_question_types(user: CurrentUser) -> list[QuestionTypeOut]:
    """The task types the editor can offer, and how each one behaves.

    Served rather than hard-coded in the client so adding a type is a change in
    one file: the editor renders whatever this returns.
    """
    return [
        QuestionTypeOut(
            slug=spec.slug,
            label=spec.label,
            kinds=list(spec.kinds),
            default_instructions=spec.default_instructions,
            group_options=spec.group_options,
            question_options=spec.question_options,
            answers_per_question=spec.answers_per_question,
            group_body=spec.group_body,
            takes_word_limit=spec.takes_word_limit,
            default_word_limit=spec.default_word_limit,
            fixed_answers=list(spec.fixed_answers),
            rubric_marked=spec.rubric_marked,
            notes=spec.notes,
        )
        for spec in QUESTION_TYPES
    ]


@router.get("/kinds", response_model=dict[str, str])
async def list_kinds(user: CurrentUser) -> dict[str, str]:
    return TEST_KIND_LABELS


@router.get("/modules", response_model=dict[str, str])
async def list_modules(user: CurrentUser) -> dict[str, str]:
    """Academic / General Training, for the paper's audience selector."""
    return MODULE_LABELS


@router.get("/reference", response_model=ContentReferenceOut)
async def content_reference(user: CurrentUser) -> ContentReferenceOut:
    """Everything the editor needs that is a fact about IELTS, not about data.

    Served rather than duplicated in the client so the exam's own rules —
    part labels, task word counts, marking criteria — have one home.
    """
    return ContentReferenceOut(
        kinds=TEST_KIND_LABELS,
        modules=MODULE_LABELS,
        speaking_parts=[
            SpeakingPartOut(
                part=part,
                label=SPEAKING_PART_LABELS[part],
                prep_seconds=SPEAKING_PART_TWO_PREP_SECONDS if part == 2 else None,
                talk_seconds=SPEAKING_PART_TWO_TALK_SECONDS if part == 2 else None,
            )
            for part in SPEAKING_PARTS
        ],
        writing_tasks=[
            WritingTaskOut(
                task=task,
                label=WRITING_TASK_LABELS[task],
                min_words=WRITING_TASK_MIN_WORDS[task],
                minutes=WRITING_TASK_MINUTES[task],
                weight=WRITING_TASK_WEIGHT[task],
            )
            for task in WRITING_TASKS
        ],
        writing_criteria=[
            CriterionOut(slug=c.slug, label=c.label, summary=c.summary) for c in WRITING_CRITERIA
        ],
        speaking_criteria=[
            CriterionOut(slug=c.slug, label=c.label, summary=c.summary) for c in SPEAKING_CRITERIA
        ],
    )


@router.get("/summary", response_model=list[KindSummary])
async def summarise_kinds(user: CurrentUser, db: DbSession) -> list[KindSummary]:
    """Counts per paper, for the four cards the content area opens on.

    One grouped query rather than four list calls, and every paper is returned
    even at zero — a missing card would read as a missing feature.
    """
    rows = await db.execute(
        select(Test.kind, Test.status, func.count()).group_by(Test.kind, Test.status)
    )
    tally: dict[tuple[str, str], int] = {(kind, status_): count for kind, status_, count in rows}

    return [
        KindSummary(
            kind=kind,
            label=label,
            published=tally.get((kind, "published"), 0),
            draft=tally.get((kind, "draft"), 0),
        )
        for kind, label in TEST_KIND_LABELS.items()
    ]


# ---- Tests -----------------------------------------------------------------


@router.get("/tests", response_model=TestPage)
async def list_tests(
    user: CurrentUser,
    db: DbSession,
    cursor: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
    kind: str | None = None,
    test_status: Annotated[str | None, Query(alias="status")] = None,
    search: str | None = None,
) -> TestPage:
    """The library, newest first.

    Drafts are included only for accounts that could edit them; everyone else
    sees the published shelf.
    """
    from ..roles import weight_for

    size = clamp_limit(limit)
    can_author = user.role_weight >= weight_for("tests.manage")

    stmt = select(Test)
    if not can_author:
        stmt = stmt.where(Test.status == "published")
    elif test_status:
        stmt = stmt.where(Test.status == test_status)

    if kind:
        stmt = stmt.where(Test.kind == kind)
    if search:
        stmt = stmt.where(func.lower(Test.title).like(f"%{search.strip().lower()}%"))

    if cursor:
        try:
            stamp, row_id = decode_cursor(cursor)
            stmt = stmt.where(
                or_(Test.created_at < stamp, (Test.created_at == stamp) & (Test.id < row_id))
            )
        except CursorError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor."
            ) from exc

    stmt = stmt.order_by(Test.created_at.desc(), Test.id.desc()).limit(size + 1)
    rows = list((await db.execute(stmt)).unique().scalars())

    has_more = len(rows) > size
    page = rows[:size]
    next_cursor = (
        encode_cursor(aware_utc(page[-1].created_at), page[-1].id) if has_more and page else None
    )

    # Counts for the page in two grouped queries rather than one per row.
    ids = [test.id for test in page]
    counts: dict[str, tuple[int, int]] = {test_id: (0, 0) for test_id in ids}
    if ids:
        grouped = await db.execute(
            select(
                QuestionGroup.test_id,
                func.count(func.distinct(QuestionGroup.id)),
                func.coalesce(func.sum(Question.span), 0),
            )
            .outerjoin(Question, Question.group_id == QuestionGroup.id)
            .where(QuestionGroup.test_id.in_(ids))
            .group_by(QuestionGroup.test_id)
        )
        for test_id, group_count, question_count in grouped:
            counts[test_id] = (question_count, group_count)

    return TestPage(
        items=[_summary(test, *counts.get(test.id, (0, 0))) for test in page],
        next_cursor=next_cursor,
    )


@router.post("/tests", response_model=TestOut, status_code=status.HTTP_201_CREATED)
async def create_test(payload: TestCreateRequest, user: Author, db: DbSession) -> Test:
    """Starts a draft. Questions are added afterwards, group by group."""
    test = Test(
        kind=payload.kind,
        title=payload.title,
        slug=await _unique_slug(db, payload.title),
        summary=payload.summary,
        difficulty=payload.difficulty,
        duration_minutes=payload.duration_minutes,
        audio_url=payload.audio_url,
        transcript=payload.transcript,
        passage=payload.passage,
        created_by_id=user.id,
        status="draft",
    )
    db.add(test)
    await db.commit()
    await db.refresh(test)
    return test


@router.get("/tests/{test_id}", response_model=TestOut)
async def get_test(test_id: str, user: CurrentUser, db: DbSession) -> Test:
    from ..roles import weight_for

    test = await _load_test(db, test_id)
    if test.status != "published" and user.role_weight < weight_for("tests.manage"):
        # 404 rather than 403: an unpublished test is not something a reader
        # should learn exists.
        raise _TEST_GONE
    return test


@router.get("/tests/{test_id}/review", response_model=TestReviewOut)
async def review(test_id: str, user: Author, db: DbSession) -> TestReviewOut:
    """What is still missing before this can be published."""
    return _review(await _load_test(db, test_id))


@router.patch("/tests/{test_id}", response_model=TestOut)
async def update_test(
    test_id: str, payload: TestUpdateRequest, user: Author, db: DbSession
) -> Test:
    test = await _load_test(db, test_id)
    changes = payload.model_dump(exclude_unset=True)

    if "status" in changes and changes["status"] == "published":
        blocking = _review(test)
        if not blocking.publishable:
            # Publishing is the one gate that refuses: everything else saves
            # freely, so the check lands where it actually matters.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Not ready to publish: {blocking.issues[0].where} — {blocking.issues[0].problem}",
            )
        test.published_at = utcnow()

    if "title" in changes and changes["title"] and changes["title"] != test.title:
        test.slug = await _unique_slug(db, changes["title"], exclude_id=test.id)

    for field, value in changes.items():
        setattr(test, field, value)

    await db.commit()
    await db.refresh(test)
    return test


@router.delete("/tests/{test_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_test(test_id: str, user: Author, db: DbSession) -> Response:
    """Hard delete, with its groups and questions.

    Unlike a community post, a test has no conversation hanging off it and no
    reader relying on the link, so there is nothing for a tombstone to hold
    together.
    """
    test = await _load_test(db, test_id)
    await db.delete(test)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- Question groups -------------------------------------------------------


async def _load_group(db: DbSession, group_id: str) -> QuestionGroup:
    group = await db.get(QuestionGroup, group_id)
    if group is None:
        raise _GROUP_GONE
    return group


@router.post(
    "/tests/{test_id}/groups", response_model=QuestionGroupOut, status_code=status.HTTP_201_CREATED
)
async def create_group(
    test_id: str, payload: QuestionGroupCreateRequest, user: Author, db: DbSession
) -> QuestionGroup:
    test = await _load_test(db, test_id)

    spec = question_type(payload.type)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown question type."
        )
    if test.kind not in spec.kinds:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{spec.label} is not a {test.kind} task.",
        )

    group = QuestionGroup(
        test_id=test.id,
        position=len(test.groups),
        type=payload.type,
        # Falling back to the exam's own wording means a group is usable the
        # moment it is added.
        instructions=payload.instructions or spec.default_instructions,
        body=payload.body,
        options=payload.options,
        image_url=payload.image_url,
        # Pre-filling the cap the exam usually prints for this task means the
        # common case needs no thought, and the review still catches the rest.
        word_limit=payload.word_limit if payload.word_limit is not None else spec.default_word_limit,
        # Speaking and writing tasks default to their first part/task so a new
        # group is never in an invalid state the author did not choose.
        speaking_part=payload.speaking_part or (1 if test.kind == "speaking" else None),
        writing_task=payload.writing_task or (1 if test.kind == "writing" else None),
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return group


@router.patch("/groups/{group_id}", response_model=QuestionGroupOut)
async def update_group(
    group_id: str, payload: QuestionGroupUpdateRequest, user: Author, db: DbSession
) -> QuestionGroup:
    group = await _load_group(db, group_id)
    changes = payload.model_dump(exclude_unset=True)

    if "type" in changes and changes["type"] is not None:
        spec = question_type(changes["type"])
        test = await _load_test(db, group.test_id)
        if spec is None or test.kind not in spec.kinds:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="That question type does not belong in this test.",
            )

    if "position" in changes and changes["position"] is not None:
        await _reorder(db, group, changes.pop("position"))

    for field, value in changes.items():
        setattr(group, field, value)

    await db.commit()
    await db.refresh(group)
    return group


async def _reorder(db: DbSession, group: QuestionGroup, target: int) -> None:
    """Moves a group and closes the gap it leaves.

    Positions are rewritten for the whole test rather than nudged, which keeps
    them contiguous — a sequence with holes in it eventually sorts wrong.
    """
    siblings = list(
        (
            await db.execute(
                select(QuestionGroup)
                .where(QuestionGroup.test_id == group.test_id)
                .order_by(QuestionGroup.position)
            )
        ).scalars()
    )
    siblings = [g for g in siblings if g.id != group.id]
    target = max(0, min(target, len(siblings)))
    siblings.insert(target, group)
    for index, sibling in enumerate(siblings):
        sibling.position = index


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group_id: str, user: Author, db: DbSession) -> Response:
    group = await _load_group(db, group_id)
    await db.delete(group)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- Questions -------------------------------------------------------------


async def _load_question(db: DbSession, question_id: str) -> Question:
    question = await db.get(Question, question_id)
    if question is None:
        raise _QUESTION_GONE
    return question


@router.post(
    "/groups/{group_id}/questions", response_model=QuestionOut, status_code=status.HTTP_201_CREATED
)
async def create_question(
    group_id: str, payload: QuestionCreateRequest, user: Author, db: DbSession
) -> Question:
    group = await _load_group(db, group_id)

    spec = question_type(group.type)
    # An item covers as many numbers as it takes answers: "choose TWO letters"
    # is one item spanning two numbers on the paper.
    span = payload.span or (spec.answers_per_question if spec else 1)

    number = payload.number
    if number is None:
        # Continue the test's numbering: one past the last number *covered*
        # anywhere in it, so an item that spans two does not collide with the
        # next one added.
        highest = await db.scalar(
            select(func.max(Question.number + Question.span - 1))
            .join(QuestionGroup, QuestionGroup.id == Question.group_id)
            .where(QuestionGroup.test_id == group.test_id)
        )
        number = (highest or 0) + 1

    question = Question(
        group_id=group.id,
        position=len(group.questions),
        number=number,
        span=span,
        prompt=payload.prompt,
        options=payload.options,
        answers=payload.answers,
        explanation=payload.explanation,
    )
    db.add(question)
    await db.commit()
    await db.refresh(question)
    return question


@router.patch("/questions/{question_id}", response_model=QuestionOut)
async def update_question(
    question_id: str, payload: QuestionUpdateRequest, user: Author, db: DbSession
) -> Question:
    question = await _load_question(db, question_id)
    changes = payload.model_dump(exclude_unset=True)

    if "position" in changes and changes["position"] is not None:
        target = changes.pop("position")
        siblings = list(
            (
                await db.execute(
                    select(Question)
                    .where(Question.group_id == question.group_id)
                    .order_by(Question.position)
                )
            ).scalars()
        )
        siblings = [q for q in siblings if q.id != question.id]
        target = max(0, min(target, len(siblings)))
        siblings.insert(target, question)
        for index, sibling in enumerate(siblings):
            sibling.position = index

    for field, value in changes.items():
        setattr(question, field, value)

    await db.commit()
    await db.refresh(question)
    return question


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(question_id: str, user: Author, db: DbSession) -> Response:
    question = await _load_question(db, question_id)
    await db.delete(question)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
