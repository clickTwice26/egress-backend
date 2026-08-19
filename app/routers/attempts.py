"""Sitting a test: start, save answers, submit, and read the result back.

An attempt belongs to the candidate who started it and to nobody else, so every
route here loads by (id, current user) rather than by id alone. Reading someone
else's attempt is a 404 rather than a 403 — a stranger's attempt should not even
be confirmed to exist.

The answer key is withheld until the attempt is submitted. Serving it with the
paper would let anyone read the answers out of the network tab before writing a
single one.
"""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import desc, select

from ..content import (
    SPEAKING_CRITERIA,
    TEST_KIND_LABELS,
    WRITING_CRITERIA,
    criteria_for_kind,
    overall_band,
    overall_from_criteria,
)
from ..deps import CurrentUser, DbSession
from ..marking import mark_attempt
from ..models import Attempt, AttemptAnswer, AttemptCriterionScore, Test, utcnow
from ..schemas import (
    AnswerResultOut,
    AnswerSaveRequest,
    AttemptOut,
    AttemptSummaryOut,
    BandPredictionOut,
    CriterionScoreOut,
    CriterionScoreRequest,
    MessageOut,
    SkillBandOut,
)

router = APIRouter(prefix="/api/attempts", tags=["attempts"])

_GONE = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attempt not found.")

#: How many recent attempts per skill the prediction averages. Matches the
#: "Last 6 tests" the dashboard card promises.
PREDICTION_WINDOW = 6


async def _load_own(db: DbSession, attempt_id: str, user_id: str) -> Attempt:
    attempt = await db.scalar(
        select(Attempt).where(Attempt.id == attempt_id, Attempt.user_id == user_id)
    )
    if attempt is None:
        raise _GONE
    return attempt


def _criterion_label(kind: str, slug: str) -> str:
    for criterion in criteria_for_kind(kind):
        if criterion.slug == slug:
            return criterion.label
    return slug


def _to_out(attempt: Attempt, *, include_key: bool) -> AttemptOut:
    """Shape an attempt for the client.

    `include_key` is false while the attempt is still open: the accepted
    answers stay on the server until there is nothing left to gain from them.
    """
    by_question = {answer.question_id: answer for answer in attempt.answers}
    results: list[AnswerResultOut] = []

    if attempt.status == "submitted":
        for group in attempt.test.groups:
            for question in group.questions:
                answer = by_question.get(question.id)
                results.append(
                    AnswerResultOut(
                        question_id=question.id,
                        number=question.number,
                        span=question.span,
                        response=answer.response if answer else [],
                        accepted=question.answers if include_key else None,
                        correct=answer.correct if answer else None,
                        marks=answer.marks if answer else 0,
                        max_marks=question.span,
                        explanation=question.explanation,
                        # Recomputed for display rather than stored: it is a
                        # reason for a score, not a fact about the attempt.
                        over_word_limit=bool(
                            answer
                            and answer.correct is False
                            and group.word_limit
                            and any(
                                len(str(r).split()) > group.word_limit
                                for r in answer.response
                                if str(r).strip()
                            )
                        ),
                    )
                )
        results.sort(key=lambda r: r.number)

    scores = [
        CriterionScoreOut(
            criterion=score.criterion,
            label=_criterion_label(attempt.kind or attempt.test.kind, score.criterion),
            score=score.score,
            comment=score.comment,
        )
        for score in attempt.criterion_scores
    ]

    kind = attempt.kind or attempt.test.kind
    expected = {c.slug for c in criteria_for_kind(kind)}
    awaiting = (
        attempt.status == "submitted"
        and bool(expected)
        and {s.criterion for s in attempt.criterion_scores} != expected
    )

    return AttemptOut(
        id=attempt.id,
        test_id=attempt.test_id,
        test_title=attempt.test.title,
        kind=kind,
        module=attempt.module or attempt.test.module,
        status=attempt.status,
        raw_score=attempt.raw_score,
        max_score=attempt.max_score,
        band=attempt.band,
        started_at=attempt.started_at,
        submitted_at=attempt.submitted_at,
        results=results,
        criterion_scores=scores,
        awaiting_examiner=awaiting,
    )


@router.post("", response_model=AttemptOut, status_code=status.HTTP_201_CREATED)
async def start_attempt(test_id: str, user: CurrentUser, db: DbSession) -> AttemptOut:
    """Begin a sitting, or hand back the one already open on this test."""
    test = await db.get(Test, test_id)
    if test is None or test.status != "published":
        # An unpublished test is not sittable, and saying so would leak that
        # a draft exists at that id.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test not found.")

    existing = await db.scalar(
        select(Attempt).where(
            Attempt.user_id == user.id,
            Attempt.test_id == test_id,
            Attempt.status == "in_progress",
        )
    )
    if existing is not None:
        # Resuming rather than starting again: a dropped connection mid-paper
        # must not cost the candidate their answers.
        return _to_out(existing, include_key=False)

    attempt = Attempt(
        user_id=user.id,
        test_id=test.id,
        kind=test.kind,
        module=test.module,
        status="in_progress",
    )
    db.add(attempt)
    await db.commit()
    await db.refresh(attempt)
    return _to_out(attempt, include_key=False)


@router.patch("/{attempt_id}/answers", response_model=MessageOut)
async def save_answers(
    attempt_id: str, payload: AnswerSaveRequest, user: CurrentUser, db: DbSession
) -> MessageOut:
    """Autosave. Overwrites whatever was stored for each question sent."""
    attempt = await _load_own(db, attempt_id, user.id)
    if attempt.status != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This attempt has been submitted and cannot be changed.",
        )

    valid_ids = {q.id for group in attempt.test.groups for q in group.questions}
    existing = {answer.question_id: answer for answer in attempt.answers}

    for item in payload.answers:
        if item.question_id not in valid_ids:
            continue  # A question from another paper is simply ignored.
        if item.question_id in existing:
            existing[item.question_id].response = item.response
        else:
            db.add(
                AttemptAnswer(
                    attempt_id=attempt.id,
                    question_id=item.question_id,
                    response=item.response,
                )
            )

    await db.commit()
    return MessageOut(message="Saved.")


@router.post("/{attempt_id}/submit", response_model=AttemptOut)
async def submit_attempt(attempt_id: str, user: CurrentUser, db: DbSession) -> AttemptOut:
    """Close the sitting and mark it."""
    attempt = await _load_own(db, attempt_id, user.id)
    if attempt.status == "submitted":
        # Idempotent: a double-tap on Submit returns the same result rather
        # than re-marking and risking a different one.
        return _to_out(attempt, include_key=True)

    responses = {answer.question_id: answer.response for answer in attempt.answers}
    marked = mark_attempt(
        kind=attempt.test.kind,
        module=attempt.test.module,
        groups=[
            {
                "type": group.type,
                "word_limit": group.word_limit,
                "questions": [
                    {
                        "id": q.id,
                        "number": q.number,
                        "span": q.span,
                        "answers": q.answers,
                        "explanation": q.explanation,
                    }
                    for q in group.questions
                ],
            }
            for group in attempt.test.groups
        ],
        responses=responses,
    )

    by_question = {answer.question_id: answer for answer in attempt.answers}
    for result in marked.results:
        answer = by_question.get(result.question_id)
        if answer is None:
            # The candidate never touched this question; record the blank so
            # the result page can show it as unanswered rather than missing.
            answer = AttemptAnswer(
                attempt_id=attempt.id, question_id=result.question_id, response=[]
            )
            db.add(answer)
        answer.correct = result.correct
        answer.marks = result.marks

    attempt.status = "submitted"
    attempt.submitted_at = utcnow()
    attempt.kind = attempt.test.kind
    attempt.module = attempt.test.module
    attempt.raw_score = marked.raw_score
    attempt.max_score = marked.max_score
    attempt.band = marked.band

    await db.commit()
    await db.refresh(attempt)
    return _to_out(attempt, include_key=True)


@router.get("/{attempt_id}", response_model=AttemptOut)
async def get_attempt(attempt_id: str, user: CurrentUser, db: DbSession) -> AttemptOut:
    attempt = await _load_own(db, attempt_id, user.id)
    return _to_out(attempt, include_key=attempt.status == "submitted")


@router.get("", response_model=list[AttemptSummaryOut])
async def list_attempts(
    user: CurrentUser,
    db: DbSession,
    kind: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[AttemptSummaryOut]:
    """The candidate's own history, newest first."""
    stmt = select(Attempt).where(Attempt.user_id == user.id)
    if kind:
        stmt = stmt.where(Attempt.kind == kind)
    stmt = stmt.order_by(desc(Attempt.started_at)).limit(limit)

    attempts = (await db.scalars(stmt)).all()
    return [
        AttemptSummaryOut(
            id=a.id,
            test_id=a.test_id,
            test_title=a.test.title,
            kind=a.kind or a.test.kind,
            status=a.status,
            raw_score=a.raw_score,
            max_score=a.max_score,
            band=a.band,
            submitted_at=a.submitted_at,
        )
        for a in attempts
    ]


@router.put("/{attempt_id}/criteria", response_model=AttemptOut)
async def score_criteria(
    attempt_id: str,
    payload: list[CriterionScoreRequest],
    user: CurrentUser,
    db: DbSession,
) -> AttemptOut:
    """Record examiner scores for a writing or speaking attempt.

    Once all four criteria are in, their average becomes the attempt's band —
    which is the only way a rubric-marked paper ever gets one.
    """
    attempt = await _load_own(db, attempt_id, user.id)
    kind = attempt.kind or attempt.test.kind

    allowed = {c.slug for c in criteria_for_kind(kind)}
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{TEST_KIND_LABELS.get(kind, kind)} is marked from an answer key, not criteria.",
        )

    unknown = {item.criterion for item in payload} - allowed
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Not a {kind} criterion: {', '.join(sorted(unknown))}.",
        )

    existing = {score.criterion: score for score in attempt.criterion_scores}
    for item in payload:
        if item.criterion in existing:
            existing[item.criterion].score = item.score
            existing[item.criterion].comment = item.comment
            existing[item.criterion].scored_by_id = user.id
            existing[item.criterion].scored_at = utcnow()
        else:
            db.add(
                AttemptCriterionScore(
                    attempt_id=attempt.id,
                    criterion=item.criterion,
                    score=item.score,
                    comment=item.comment,
                    scored_by_id=user.id,
                )
            )

    await db.commit()
    await db.refresh(attempt)

    # A partial marking is not a band. Only a complete set produces one.
    scores = {s.criterion: s.score for s in attempt.criterion_scores}
    if set(scores) == allowed:
        attempt.band = overall_from_criteria(scores)
        await db.commit()
        await db.refresh(attempt)

    return _to_out(attempt, include_key=True)


@router.get("/me/prediction", response_model=BandPredictionOut)
async def band_prediction(user: CurrentUser, db: DbSession) -> BandPredictionOut:
    """The candidate's estimated band, per skill and overall.

    Averages the most recent marked attempts in each skill. Skills with no
    marked attempt are reported as null rather than as zero — never sat is not
    the same as scored nothing, and averaging a missing skill in as 0 would
    drag the overall estimate into nonsense.
    """
    skills: list[SkillBandOut] = []
    per_skill: dict[str, float] = {}

    for kind, label in TEST_KIND_LABELS.items():
        recent = (
            await db.scalars(
                select(Attempt)
                .where(
                    Attempt.user_id == user.id,
                    Attempt.kind == kind,
                    Attempt.status == "submitted",
                    Attempt.band.is_not(None),
                )
                .order_by(desc(Attempt.submitted_at))
                .limit(PREDICTION_WINDOW)
            )
        ).all()

        bands = [a.band for a in recent if a.band is not None]
        if bands:
            # Rounded to the nearest half band, as IELTS reports.
            average = round((sum(bands) / len(bands)) * 2) / 2
            per_skill[kind] = average
            skills.append(SkillBandOut(skill=kind, label=label, band=average, attempts=len(bands)))
        else:
            skills.append(SkillBandOut(skill=kind, label=label, band=None, attempts=0))

    return BandPredictionOut(
        overall=overall_band(per_skill) if per_skill else None,
        skills=skills,
        window=PREDICTION_WINDOW,
    )
