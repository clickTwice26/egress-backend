from datetime import date, timezone

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..deps import CurrentUser, DbSession
from ..models import StudyPlan, StudyTask, utcnow
from ..schemas import (
    SkillBands,
    StudyPlanIntake,
    StudyPlanOut,
    StudyTaskCreate,
    StudyTaskOut,
    StudyTaskUpdate,
)
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


async def _load_task(db: DbSession, task_id: str, user_id: str) -> StudyTask:
    result = await db.execute(
        select(StudyTask).join(StudyPlan).where(StudyTask.id == task_id, StudyPlan.user_id == user_id)
    )
    task = result.scalar_one_or_none()
    if task is None:
        # Same response whether the task is missing or belongs to someone else,
        # so the endpoint cannot be used to probe for valid task ids.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
    return task


@router.post("/tasks", response_model=StudyTaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(payload: StudyTaskCreate, user: CurrentUser, db: DbSession) -> StudyTaskOut:
    """Add a session to the plan by hand.

    A generated plan is a starting point, not a contract: sessions can be added
    on any day, including days the generator left empty and days beyond the
    test date.
    """
    plan = await _load_plan(db, user.id)
    if plan is None:
        raise _NO_PLAN

    # Land the new session after whatever that day already holds, so adding one
    # never reshuffles the sessions the user is part-way through.
    last = max(
        (task.position for task in plan.tasks if task.scheduled_on == payload.scheduled_on),
        default=-1,
    )

    task = StudyTask(
        plan_id=plan.id,
        scheduled_on=payload.scheduled_on,
        position=last + 1,
        skill=payload.skill,
        kind=payload.kind,
        title=payload.title,
        minutes=payload.minutes,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return StudyTaskOut.model_validate(task)


@router.patch("/tasks/{task_id}", response_model=StudyTaskOut)
async def update_task(
    task_id: str, payload: StudyTaskUpdate, user: CurrentUser, db: DbSession
) -> StudyTaskOut:
    """Edit one session. Ticking it off is the same call with only `completed`."""
    task = await _load_task(db, task_id, user.id)
    fields = payload.model_dump(exclude_unset=True)

    if "scheduled_on" in fields and fields["scheduled_on"] != task.scheduled_on:
        # Moving days means the old position is meaningless; append to the target.
        moved_to = fields["scheduled_on"]
        result = await db.execute(
            select(func.max(StudyTask.position)).where(
                StudyTask.plan_id == task.plan_id, StudyTask.scheduled_on == moved_to
            )
        )
        task.position = (result.scalar() or -1) + 1

    if "completed" in fields:
        task.completed_at = utcnow().astimezone(timezone.utc) if fields.pop("completed") else None

    for field, value in fields.items():
        setattr(task, field, value)

    await db.commit()
    await db.refresh(task)
    return StudyTaskOut.model_validate(task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str, user: CurrentUser, db: DbSession) -> Response:
    task = await _load_task(db, task_id, user.id)
    await db.delete(task)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(user: CurrentUser, db: DbSession) -> Response:
    plan = await _load_plan(db, user.id)
    if plan is None:
        raise _NO_PLAN
    await db.delete(plan)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
