from datetime import date, timezone

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import CurrentUser, DbSession
from ..models import StudyPlan, StudyTask, utcnow
from ..schemas import SkillBands, StudyPlanIntake, StudyPlanOut, StudyTaskOut, TaskCompletionRequest
from ..study_plan import PlanError, generate_plan

router = APIRouter(prefix="/api/study-plan", tags=["study-plan"])

_NO_PLAN = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No study plan yet.")


def _to_out(plan: StudyPlan) -> StudyPlanOut:
    return StudyPlanOut(
        id=plan.id,
        test_date=plan.test_date,
        target_band=plan.target_band,
        hours_per_week=plan.hours_per_week,
        current_bands=SkillBands(
            listening=plan.listening_band,
            reading=plan.reading_band,
            writing=plan.writing_band,
            speaking=plan.speaking_band,
        ),
        created_at=plan.created_at,
        tasks=[StudyTaskOut.model_validate(task) for task in plan.tasks],
    )


async def _load_plan(db: DbSession, user_id: str) -> StudyPlan | None:
    result = await db.execute(
        select(StudyPlan).where(StudyPlan.user_id == user_id).options(selectinload(StudyPlan.tasks))
    )
    return result.scalar_one_or_none()


@router.get("", response_model=StudyPlanOut | None)
async def get_plan(user: CurrentUser, db: DbSession) -> StudyPlanOut | None:
    """The caller's plan, or null when they have not made one.

    Null rather than 404: "no plan yet" is the expected first state, not an
    error the client should have to catch.
    """
    plan = await _load_plan(db, user.id)
    return _to_out(plan) if plan else None


@router.post("", response_model=StudyPlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(payload: StudyPlanIntake, user: CurrentUser, db: DbSession) -> StudyPlanOut:
    """Generate a plan from the intake, replacing any existing one."""
    bands = payload.current_bands.model_dump()

    try:
        specs = generate_plan(
            today=date.today(),
            test_date=payload.test_date,
            target_band=payload.target_band,
            current_bands=bands,
            hours_per_week=payload.hours_per_week,
        )
    except PlanError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    existing = await _load_plan(db, user.id)
    if existing is not None:
        # Cascade removes the old tasks; regenerating must not merge two plans.
        await db.delete(existing)
        await db.flush()

    plan = StudyPlan(
        user_id=user.id,
        test_date=payload.test_date,
        target_band=payload.target_band,
        hours_per_week=payload.hours_per_week,
        listening_band=bands["listening"],
        reading_band=bands["reading"],
        writing_band=bands["writing"],
        speaking_band=bands["speaking"],
        tasks=[
            StudyTask(
                scheduled_on=spec.scheduled_on,
                position=spec.position,
                skill=spec.skill,
                kind=spec.kind,
                title=spec.title,
                minutes=spec.minutes,
            )
            for spec in specs
        ],
    )
    db.add(plan)
    await db.commit()

    plan = await _load_plan(db, user.id)
    assert plan is not None
    return _to_out(plan)


@router.patch("/tasks/{task_id}", response_model=StudyTaskOut)
async def set_task_completion(
    task_id: str, payload: TaskCompletionRequest, user: CurrentUser, db: DbSession
) -> StudyTaskOut:
    result = await db.execute(
        select(StudyTask).join(StudyPlan).where(StudyTask.id == task_id, StudyPlan.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    if task is None:
        # Same response whether the task is missing or belongs to someone else,
        # so the endpoint cannot be used to probe for valid task ids.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")

    task.completed_at = utcnow().astimezone(timezone.utc) if payload.completed else None
    await db.commit()
    await db.refresh(task)
    return StudyTaskOut.model_validate(task)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(user: CurrentUser, db: DbSession) -> Response:
    plan = await _load_plan(db, user.id)
    if plan is None:
        raise _NO_PLAN
    await db.delete(plan)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
