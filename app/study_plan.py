"""Generation of a week-by-week IELTS study schedule.

Pure functions only — no database, no clock. The caller supplies "today" so the
output is deterministic and testable.
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta

SKILLS = ("listening", "reading", "writing", "speaking")

# A single focused session. Short enough that most people finish one, long
# enough to cover a full reading passage or a Task 2 essay under time.
SESSION_MINUTES = 45

# A full paper is 2h45 of test time; the plan books three hours to include
# settling in and marking.
MOCK_MINUTES = 180

# A mock costs three of the week's hours. Below this commitment a weekly mock
# would leave too little room for the drills that actually move a band score,
# so mocks drop to fortnightly.
WEEKLY_MINUTES_FOR_WEEKLY_MOCK = 360

# The last stretch is consolidation, not new ground.
TAPER_DAYS = 3

MIN_PLAN_DAYS = 7
MAX_PLAN_DAYS = 365


@dataclass(frozen=True)
class TaskSpec:
    scheduled_on: date
    position: int
    skill: str
    kind: str
    title: str
    minutes: int


class PlanError(ValueError):
    """The intake cannot produce a sensible plan."""


# Rotating drill titles per skill. Cycling through them keeps a long plan from
# reading as the same line repeated forty times.
_DRILLS: dict[str, tuple[str, ...]] = {
    "listening": (
        "Section 3 and 4 practice — academic discussion and lecture",
        "Map, plan and diagram labelling",
        "Note completion under time",
        "Multiple choice with distractor spotting",
        "Accent practice — Australian and Scottish speakers",
    ),
    "reading": (
        "True / False / Not Given — full passage",
        "Matching headings under time",
        "Skimming and scanning drill",
        "Summary completion from a long passage",
        "Sentence completion and short answer",
    ),
    "writing": (
        "Task 1 — describe a chart or process",
        "Task 2 — opinion essay, timed",
        "Task 2 — discuss both views, timed",
        "Paragraph structure and cohesion drill",
        "Rewrite a past essay against the band descriptors",
    ),
    "speaking": (
        "Part 1 — familiar topics, recorded",
        "Part 2 — cue card, two minutes recorded",
        "Part 3 — abstract follow-up questions",
        "Fluency drill — no fillers, ninety seconds",
        "Pronunciation and word stress practice",
    ),
}

_REVIEW = {
    "listening": "Review listening mistakes log",
    "reading": "Review reading mistakes log",
    "writing": "Reread marked essays and the examiner notes",
    "speaking": "Replay recordings and note repeated errors",
}


def _study_weekdays(sessions_per_week: int) -> list[int]:
    """Pick which weekdays carry sessions, spread as evenly as seven days allow.

    Monday is index 0. Fewer than seven sessions are spaced out rather than
    stacked at the start of the week, so rest days fall between study days.
    """
    if sessions_per_week >= 7:
        return list(range(7))
    # Evenly sample `n` positions across the week.
    return sorted({round(i * 7 / sessions_per_week) % 7 for i in range(sessions_per_week)})


def _skill_weights(current: dict[str, float], target: float) -> dict[str, float]:
    """How much of each week each skill is owed.

    The weight is ``1 + gap`` rather than the gap alone. Weighting on the raw
    gap sends a lopsided share to one skill — a 1.5-band gap against three
    0.5-band gaps takes 43% of every week — and the overall band is the mean of
    all four papers, so starving three to rescue one lowers the result. The
    constant floor keeps the weakest skill first without abandoning the rest.
    """
    return {skill: 1.0 + max(0.0, target - current[skill]) for skill in SKILLS}


def _allocate(credit: dict[str, float], weights: dict[str, float], slots: int) -> dict[str, int]:
    """Hand out this week's ``slots``, carrying fractional shares between weeks.

    ``credit`` is mutated. Rounding each week in isolation starves any skill
    whose weekly share sits below 1.0: at three slots a week a 0.4 share floors
    to zero *every* week, so the skill is never scheduled at all. Carrying the
    remainder forward turns that into one session every third week, which is
    what a small share is supposed to mean.
    """
    total = sum(weights.values())
    for skill in SKILLS:
        credit[skill] += slots * weights[skill] / total

    counts = {skill: int(credit[skill]) for skill in SKILLS}
    for skill in SKILLS:
        credit[skill] -= counts[skill]

    # Floors can leave slots unfilled; give them to whoever is owed most.
    for skill in sorted(SKILLS, key=lambda s: credit[s], reverse=True):
        if sum(counts.values()) >= slots:
            break
        counts[skill] += 1
        credit[skill] -= 1
    return counts


def generate_plan(
    *,
    today: date,
    test_date: date,
    target_band: float,
    current_bands: dict[str, float],
    hours_per_week: int,
) -> list[TaskSpec]:
    """Build the full list of sessions between tomorrow and the test date.

    The schedule works in whole weeks. Each week's sessions are split across
    the four skills in proportion to the gap between the user's self-assessed
    band and their target, so the weakest skill gets the most time. A full mock
    lands at the end of a week, and the final few days taper into review.
    """
    start = today + timedelta(days=1)
    horizon = (test_date - start).days + 1

    if horizon < MIN_PLAN_DAYS:
        raise PlanError(f"Pick a test date at least {MIN_PLAN_DAYS} days away.")
    if horizon > MAX_PLAN_DAYS:
        raise PlanError("Pick a test date within the next year.")

    weekly_minutes = hours_per_week * 60
    # Three 45-minute sessions is a full evening; past that the plan would be
    # promising more than a working day allows.
    sessions_per_week = max(2, min(21, round(weekly_minutes / SESSION_MINUTES)))
    study_day_count = min(7, sessions_per_week)
    weekdays = _study_weekdays(study_day_count)
    per_day = math.ceil(sessions_per_week / study_day_count)

    weekly_mock = weekly_minutes >= WEEKLY_MINUTES_FOR_WEEKLY_MOCK

    taper_start = test_date - timedelta(days=TAPER_DAYS - 1)
    tasks: list[TaskSpec] = []
    drill_index = dict.fromkeys(SKILLS, 0)
    weights = _skill_weights(current_bands, target_band)
    credit = dict.fromkeys(SKILLS, 0.0)

    # Walk the horizon one week at a time so mocks and skill shares are
    # allocated per week rather than drifting.
    week_start = start
    week_number = 0
    while week_start <= test_date:
        week_end = min(week_start + timedelta(days=6), test_date)
        study_days = [
            week_start + timedelta(days=offset)
            for offset in range((week_end - week_start).days + 1)
            if (week_start + timedelta(days=offset)).weekday() in weekdays
        ]
        # Taper days are handled separately below.
        study_days = [d for d in study_days if d < taper_start]

        if study_days:
            wants_mock = weekly_mock or week_number % 2 == 1
            mock_day = study_days[-1] if wants_mock and len(study_days) > 1 else None

            drill_days = [d for d in study_days if d != mock_day]
            # The mock is spent from the same weekly budget as the drills, or
            # a mock week silently costs three hours more than was asked for.
            drill_budget = max(0, weekly_minutes - (MOCK_MINUTES if mock_day else 0))
            slots = min(round(drill_budget / SESSION_MINUTES), len(drill_days) * per_day)
            shares = _allocate(credit, weights, slots)

            # Flatten the per-skill counts into one ordered queue, interleaved
            # so consecutive sessions are not all the same skill.
            queue: list[str] = []
            remaining = dict(shares)
            while sum(remaining.values()) > 0:
                for skill in sorted(SKILLS, key=lambda s: remaining[s], reverse=True):
                    if remaining[skill] > 0:
                        queue.append(skill)
                        remaining[skill] -= 1
                        break

            cursor = 0
            for day in drill_days:
                for position in range(per_day):
                    if cursor >= len(queue):
                        break
                    skill = queue[cursor]
                    cursor += 1
                    options = _DRILLS[skill]
                    title = options[drill_index[skill] % len(options)]
                    drill_index[skill] += 1
                    tasks.append(
                        TaskSpec(day, position, skill, "drill", title, SESSION_MINUTES)
                    )

            if mock_day is not None:
                tasks.append(
                    TaskSpec(mock_day, 0, "all", "mock", "Full mock test — all four papers", MOCK_MINUTES)
                )

        week_start += timedelta(days=7)
        week_number += 1

    tasks.extend(_taper_tasks(taper_start, test_date, current_bands, target_band))
    return tasks


def _taper_tasks(
    taper_start: date, test_date: date, current_bands: dict[str, float], target_band: float
) -> list[TaskSpec]:
    """Light review in the last days, and nothing heavy the day before.

    Cramming a full paper the day before the test costs more in fatigue than it
    gains, so the final day is a short warm-up and admin check only.
    """
    tasks: list[TaskSpec] = []
    # Weakest first — the review time goes where it is most useful.
    weakest = sorted(SKILLS, key=lambda s: current_bands[s] - target_band)

    day = taper_start
    index = 0
    while day < test_date:
        skill = weakest[index % len(SKILLS)]
        tasks.append(TaskSpec(day, 0, skill, "review", _REVIEW[skill], 30))
        index += 1
        day += timedelta(days=1)

    tasks.append(
        TaskSpec(test_date, 0, "all", "exam", "Test day — bring ID, arrive early", 0)
    )
    return tasks
