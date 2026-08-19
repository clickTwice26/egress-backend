"""Generate a large library of practice tests.

    .venv/bin/python seed_tests.py --per-kind 500
    .venv/bin/python seed_tests.py --per-kind 500 --reset   # clear seeded first

Every generated paper is built to pass ``review_test``, so the library is
actually sittable rather than 2,000 rows that can never be published: word
limits are set on completion tasks, Reading and Writing declare a module,
Speaking runs all three parts, Writing carries both tasks, and every keyed
question has an answer.

Rows are written with Core bulk inserts rather than the ORM. Adding twelve
thousand questions one object at a time takes minutes; this takes seconds.

Seeded rows are tagged in ``summary`` with SEED_MARKER so ``--reset`` can find
and remove exactly what this script made, and nothing an author wrote by hand.
"""

import argparse
import asyncio
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, insert, select

from app.content import review_test
from app.database import SessionLocal, engine, init_db
from app.models import Question, QuestionGroup, Test, User

#: Written into every seeded test's summary so --reset can identify them.
SEED_MARKER = "[seeded]"

# Deterministic: the same command twice produces the same library, which makes
# a bug reproducible instead of a one-off.
RNG = random.Random(20260819)

TOPICS = [
    ("Urban beekeeping", "city ecology"),
    ("Tidal energy in the Bay of Fundy", "renewable power"),
    ("The Silk Road spice trade", "economic history"),
    ("Sleep and adolescent memory", "cognitive science"),
    ("Rewilding the Scottish Highlands", "conservation"),
    ("Cargo shipping and emissions", "transport"),
    ("The decline of cursive handwriting", "education"),
    ("Desalination in arid coastal cities", "water supply"),
    ("Museum lighting and pigment decay", "conservation science"),
    ("Remote work and city centres", "urban economics"),
    ("Antarctic ice core sampling", "climate research"),
    ("The economics of secondhand clothing", "consumer behaviour"),
    ("Birdsong dialects in urban sparrows", "behavioural ecology"),
    ("Cast iron and Victorian architecture", "engineering history"),
    ("Fermentation in traditional food", "food science"),
    ("Light pollution and moth navigation", "entomology"),
    ("Paper conservation in humid climates", "archival science"),
    ("Community allotments and diet", "public health"),
    ("The mathematics of traffic waves", "applied mathematics"),
    ("Volcanic soil and vineyard yield", "agronomy"),
    ("Underwater acoustics and whale routes", "marine biology"),
    ("Typeface legibility on road signs", "design research"),
    ("Cold-water swimming and immunity", "sports medicine"),
    ("The restoration of coral nurseries", "marine conservation"),
    ("Grain storage in ancient Anatolia", "archaeology"),
]

VENUES = ["Kelvinside", "Harborne", "Ravensbourne", "Fairhaven", "Northgate", "Ellesmere",
          "Bramhall", "Sandymount", "Carrowbeg", "Lindfield"]

NOUN_ANSWERS = ["library", "harbour", "workshop", "greenhouse", "archive", "quarry",
                "boathouse", "orchard", "foundry", "reservoir", "terrace", "pavilion"]
TWO_WORD_ANSWERS = ["field notes", "tide table", "roof garden", "study group",
                    "night shift", "loading bay", "seed bank", "map room"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
NUMBERS = ["14", "26", "38", "47", "52", "63", "71", "89"]

WRITING_TASK1_ACADEMIC = [
    "The chart below shows household water use in four cities between 2015 and 2024.",
    "The graph below shows rail passenger numbers on three routes over a ten-year period.",
    "The diagram below shows the process of recycling glass containers.",
    "The table below compares average commuting times in five European cities.",
]
WRITING_TASK1_GENERAL = [
    "You recently stayed at a guesthouse and left an item behind. Write a letter to the manager.",
    "A friend is moving to your town and has asked about finding work. Write a letter of advice.",
    "You were unhappy with a repair carried out on your home. Write a letter to the company.",
    "Your local library is reducing its opening hours. Write a letter to the council.",
]
WRITING_TASK2 = [
    "Some people believe museums should be free to enter. Others argue visitors should pay. Discuss both views and give your own opinion.",
    "Working from home is becoming more common. Do the advantages outweigh the disadvantages?",
    "Many cities are restricting private cars in their centres. To what extent do you agree with this policy?",
    "Some argue that children learn more outside the classroom than inside it. Discuss.",
    "Governments should spend more on public transport than on new roads. To what extent do you agree?",
]

SPEAKING_P1 = [
    ["Let's talk about where you live.", "Do you live in a house or a flat?",
     "What do you like about your neighbourhood?", "Would you like to move somewhere else?"],
    ["Let's talk about food.", "Do you prefer eating at home or eating out?",
     "Has your diet changed in recent years?", "Who does the cooking in your household?"],
    ["Let's talk about travel.", "How do you usually get to work or college?",
     "Do you enjoy long journeys?", "What is the furthest you have travelled?"],
]
SPEAKING_P2 = [
    ("Describe a place you go to relax.",
     ["where it is", "how often you go there", "what you do there",
      "and explain why it helps you relax"]),
    ("Describe something you learned to do as an adult.",
     ["what it was", "why you decided to learn it", "how you learned it",
      "and explain how useful it has been"]),
    ("Describe a piece of equipment you use often.",
     ["what it is", "how long you have had it", "what you use it for",
      "and explain why it matters to you"]),
]
SPEAKING_P3 = [
    ["How have places to relax changed in your country?",
     "Do people find it harder to switch off than in the past?",
     "Should employers provide quiet spaces?"],
    ["Is it harder to learn new skills as an adult?",
     "Should governments fund adult education?",
     "How has online learning changed who can study?"],
    ["Do people repair equipment less than they used to?",
     "Should manufacturers be required to make repairable products?",
     "How does advertising affect what people buy?"],
]


def _uid() -> str:
    return str(uuid.uuid4())


def _slug(title: str, index: int) -> str:
    import re
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:180]
    return f"{base}-{index}"


class Builder:
    """Accumulates rows for one bulk insert per table."""

    def __init__(self) -> None:
        self.tests: list[dict] = []
        self.groups: list[dict] = []
        self.questions: list[dict] = []

    def add_test(self, **fields) -> str:
        test_id = _uid()
        self.tests.append({"id": test_id, **fields})
        return test_id

    def add_group(self, test_id: str, position: int, **fields) -> str:
        group_id = _uid()
        self.groups.append({
            "id": group_id, "test_id": test_id, "position": position,
            "body": None, "options": [], "image_url": None,
            "word_limit": None, "speaking_part": None, "writing_task": None,
            **fields,
        })
        return group_id

    def add_question(self, group_id: str, position: int, number: int, **fields) -> None:
        self.questions.append({
            "id": _uid(), "group_id": group_id, "position": position, "number": number,
            "span": 1, "prompt": "", "options": [], "answers": [], "explanation": "",
            **fields,
        })


def build_listening(b: Builder, index: int, author_id: str | None, now: datetime) -> dict:
    topic, field = RNG.choice(TOPICS)
    venue = RNG.choice(VENUES)
    title = f"Listening {index}: {venue} {RNG.choice(['centre', 'project', 'society', 'trust'])}"
    published = RNG.random() < 0.85

    test_id = b.add_test(
        kind="listening", title=title, slug=_slug(title, index),
        summary=f"{SEED_MARKER} A short {field} conversation and a talk.",
        difficulty=RNG.choice(["easy", "medium", "medium", "hard"]),
        status="published" if published else "draft",
        module="both", duration_minutes=RNG.choice([10, 12, 15]),
        audio_url=f"/audio/seed/listening-{index}.mp3",
        transcript=f"Narrator: You will hear a conversation about {topic.lower()}.",
        passage=None, created_by_id=author_id,
        created_at=now, updated_at=now,
        published_at=now if published else None,
    )

    number = 1

    # Task 1 — form completion, one word each.
    g1 = b.add_group(
        test_id, 0, type="form_completion",
        instructions="Complete the form below. Write ONE WORD AND/OR A NUMBER for each answer.",
        body=f"{venue} Centre — enquiry form\nLocation: ___ (1)\nBest day: ___ (2)\nGroup size: ___ (3)",
        word_limit=1,
    )
    for position, answer in enumerate([RNG.choice(NOUN_ANSWERS), RNG.choice(DAYS), RNG.choice(NUMBERS)]):
        b.add_question(g1, position, number, answers=[answer],
                       explanation=f"The speaker says \"{answer}\" when confirming details.")
        number += 1

    # Task 2 — multiple choice.
    g2 = b.add_group(
        test_id, 1, type="multiple_choice",
        instructions="Choose the correct letter, A, B or C.",
    )
    for position in range(3):
        options = ["A", "B", "C"]
        b.add_question(
            g2, position, number,
            prompt=RNG.choice([
                "What is the main purpose of the visit?",
                "What does the speaker suggest bringing?",
                "Why was the session rescheduled?",
                "What will happen at the end of the tour?",
            ]),
            options=options, answers=[RNG.choice(options)],
            explanation="The other options are mentioned but rejected.",
        )
        number += 1

    # Task 3 — matching from a box.
    g3 = b.add_group(
        test_id, 2, type="matching",
        instructions="Choose FOUR answers from the box and write the correct letter, A-E.",
        options=["Weekday mornings", "Weekend afternoons", "Evenings only",
                 "By arrangement", "Closed to the public"],
    )
    for position in range(4):
        b.add_question(g3, position, number,
                       prompt=f"The {RNG.choice(['archive', 'workshop', 'garden', 'gallery', 'store'])}",
                       answers=[RNG.choice(["A", "B", "C", "D", "E"])])
        number += 1

    return {"id": test_id, "questions": number - 1}


def build_reading(b: Builder, index: int, author_id: str | None, now: datetime) -> dict:
    topic, field = RNG.choice(TOPICS)
    module = RNG.choice(["academic", "academic", "general"])
    title = f"Reading {index}: {topic}"
    published = RNG.random() < 0.85

    paragraphs = "\n\n".join(
        f"{chr(65 + i)}  {topic} has been studied since the 1970s, when researchers in "
        f"{field} first recorded systematic variation across sites. Later work narrowed "
        f"the question considerably."
        for i in range(5)
    )

    test_id = b.add_test(
        kind="reading", title=title, slug=_slug(title, index),
        summary=f"{SEED_MARKER} A {module} passage on {field}.",
        difficulty=RNG.choice(["easy", "medium", "medium", "hard"]),
        status="published" if published else "draft",
        module=module, duration_minutes=RNG.choice([15, 18, 20]),
        audio_url=None, transcript=None, passage=paragraphs,
        created_by_id=author_id, created_at=now, updated_at=now,
        published_at=now if published else None,
    )

    number = 1

    # Task 1 — True/False/Not Given, or Yes/No/Not Given.
    use_ynng = RNG.random() < 0.4
    g1 = b.add_group(
        test_id, 0,
        type="yes_no_notgiven" if use_ynng else "true_false_notgiven",
        instructions=(
            "Do the following statements agree with the claims of the writer? "
            "Write YES, NO or NOT GIVEN."
            if use_ynng else
            "Do the following statements agree with the information in the passage? "
            "Write TRUE, FALSE or NOT GIVEN."
        ),
    )
    pool = ["YES", "NO", "NOT GIVEN"] if use_ynng else ["TRUE", "FALSE", "NOT GIVEN"]
    for position in range(4):
        b.add_question(g1, position, number,
                       prompt=f"Research into {topic.lower()} began before 1970.",
                       answers=[RNG.choice(pool)],
                       explanation="See the opening paragraph.")
        number += 1

    # Task 2 — matching headings.
    g2 = b.add_group(
        test_id, 1, type="matching_headings",
        instructions="Choose the correct heading for each paragraph from the list below.",
        options=["An unexpected result", "Early measurements", "Funding difficulties",
                 "A change of method", "Applications elsewhere", "Remaining questions"],
    )
    for position in range(3):
        b.add_question(g2, position, number, prompt=f"Paragraph {chr(65 + position)}",
                       answers=[RNG.choice(["i", "ii", "iii", "iv", "v", "vi"][:6])])
        number += 1

    # Task 3 — summary completion, two words.
    g3 = b.add_group(
        test_id, 2, type="summary_completion",
        instructions=("Complete the summary below. Choose NO MORE THAN TWO WORDS "
                      "from the passage for each answer."),
        body=f"Work on {topic.lower()} relies on ___ (8) gathered at each site, "
             f"recorded during a ___ (9) and checked against a ___ (10).",
        word_limit=2,
    )
    for position in range(3):
        b.add_question(g3, position, number, answers=[RNG.choice(TWO_WORD_ANSWERS)],
                       explanation="The phrase appears verbatim in the passage.")
        number += 1

    return {"id": test_id, "questions": number - 1}


def build_writing(b: Builder, index: int, author_id: str | None, now: datetime) -> dict:
    module = RNG.choice(["academic", "academic", "general"])
    title = f"Writing {index}: {'Academic' if module == 'academic' else 'General Training'} paper"
    published = RNG.random() < 0.85

    test_id = b.add_test(
        kind="writing", title=title, slug=_slug(title, index),
        summary=f"{SEED_MARKER} Task 1 and Task 2 under exam timing.",
        difficulty=RNG.choice(["easy", "medium", "medium", "hard"]),
        status="published" if published else "draft",
        module=module, duration_minutes=60,
        audio_url=None, transcript=None, passage=None,
        created_by_id=author_id, created_at=now, updated_at=now,
        published_at=now if published else None,
    )

    task1 = (RNG.choice(WRITING_TASK1_ACADEMIC) if module == "academic"
             else RNG.choice(WRITING_TASK1_GENERAL))
    g1 = b.add_group(
        test_id, 0, type="essay",
        instructions="You should spend about 20 minutes on this task. Write at least 150 words.",
        body=task1, writing_task=1,
        # Academic Task 1 describes a figure, so the review expects an image.
        image_url=f"/figures/seed/task1-{index}.png" if module == "academic" else None,
    )
    b.add_question(g1, 0, 1, prompt=task1)

    g2 = b.add_group(
        test_id, 1, type="essay",
        instructions="You should spend about 40 minutes on this task. Write at least 250 words.",
        body=RNG.choice(WRITING_TASK2), writing_task=2,
    )
    b.add_question(g2, 0, 2, prompt=RNG.choice(WRITING_TASK2))

    return {"id": test_id, "questions": 2}


def build_speaking(b: Builder, index: int, author_id: str | None, now: datetime) -> dict:
    p1 = RNG.choice(SPEAKING_P1)
    cue_topic, cue_bullets = RNG.choice(SPEAKING_P2)
    p3 = RNG.choice(SPEAKING_P3)
    title = f"Speaking {index}: {cue_topic.rstrip('.')}"
    published = RNG.random() < 0.85

    test_id = b.add_test(
        kind="speaking", title=title, slug=_slug(title, index),
        summary=f"{SEED_MARKER} All three parts, examiner-marked.",
        difficulty=RNG.choice(["easy", "medium", "medium", "hard"]),
        status="published" if published else "draft",
        module="both", duration_minutes=14,
        audio_url=None, transcript=None, passage=None,
        created_by_id=author_id, created_at=now, updated_at=now,
        published_at=now if published else None,
    )

    number = 1

    g1 = b.add_group(test_id, 0, type="speaking_prompt",
                     instructions=p1[0], speaking_part=1)
    for position, question in enumerate(p1[1:]):
        b.add_question(g1, position, number, prompt=question)
        number += 1

    g2 = b.add_group(
        test_id, 1, type="speaking_prompt",
        instructions=("You will have one minute to prepare, then talk for one to two minutes."),
        speaking_part=2,
        body=f"{cue_topic}\n\nYou should say:\n" + "\n".join(f"- {b_}" for b_ in cue_bullets),
    )
    b.add_question(g2, 0, number, prompt=cue_topic)
    number += 1

    g3 = b.add_group(test_id, 2, type="speaking_prompt",
                     instructions="We've been talking about that topic. Let's consider it more generally.",
                     speaking_part=3)
    for position, question in enumerate(p3):
        b.add_question(g3, position, number, prompt=question)
        number += 1

    return {"id": test_id, "questions": number - 1}


BUILDERS = {
    "listening": build_listening,
    "reading": build_reading,
    "writing": build_writing,
    "speaking": build_speaking,
}


async def reset() -> int:
    """Delete previously seeded tests. Groups and questions cascade."""
    async with SessionLocal() as db:
        ids = (await db.scalars(select(Test.id).where(Test.summary.like(f"{SEED_MARKER}%")))).all()
        if ids:
            await db.execute(delete(Test).where(Test.id.in_(ids)))
            await db.commit()
        return len(ids)


async def seed(per_kind: int) -> None:
    async with SessionLocal() as db:
        # Attribute the library to the highest-privileged account if one exists.
        author_id = await db.scalar(select(User.id).order_by(User.created_at).limit(1))

    now = datetime.now(timezone.utc)
    builder = Builder()
    totals: dict[str, int] = {}

    for kind, build in BUILDERS.items():
        made = 0
        for index in range(1, per_kind + 1):
            # Spread created_at over the past year so listings sort sensibly.
            stamp = now - timedelta(minutes=RNG.randint(0, 525_600))
            build(builder, index, author_id, stamp)
            made += 1
        totals[kind] = made
        print(f"  built {made} {kind} tests")

    print(f"\n  inserting {len(builder.tests)} tests, "
          f"{len(builder.groups)} groups, {len(builder.questions)} questions...")

    started = time.perf_counter()
    async with engine.begin() as conn:
        # Chunked: SQLite has a hard cap on variables per statement, and one
        # 12,000-row INSERT would exceed it.
        for table, rows in (
            (Test.__table__, builder.tests),
            (QuestionGroup.__table__, builder.groups),
            (Question.__table__, builder.questions),
        ):
            for start in range(0, len(rows), 500):
                await conn.execute(insert(table), rows[start:start + 500])
    elapsed = time.perf_counter() - started
    print(f"  inserted in {elapsed:.1f}s")


async def verify(sample: int = 12) -> None:
    """Spot-check that seeded papers actually pass review."""
    from sqlalchemy.orm import selectinload

    async with SessionLocal() as db:
        tests = (await db.scalars(
            select(Test)
            .where(Test.summary.like(f"{SEED_MARKER}%"))
            .options(selectinload(Test.groups).selectinload(QuestionGroup.questions))
            .limit(sample)
        )).all()

    bad = 0
    for test in tests:
        issues = review_test(
            kind=test.kind, title=test.title, audio_url=test.audio_url,
            module=test.module, passage=test.passage,
            groups=[{
                "type": g.type, "options": g.options, "body": g.body,
                "image_url": g.image_url, "word_limit": g.word_limit,
                "speaking_part": g.speaking_part, "writing_task": g.writing_task,
                "questions": [{"number": q.number, "span": q.span,
                               "options": q.options, "answers": q.answers}
                              for q in g.questions],
            } for g in test.groups],
        )
        if issues:
            bad += 1
            print(f"  ISSUES in {test.kind} \"{test.title}\":")
            for issue in issues[:4]:
                print(f"      [{issue.where}] {issue.problem}")

    print(f"\n  reviewed {len(tests)} sampled papers — {len(tests) - bad} publishable, {bad} with issues")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed practice tests.")
    parser.add_argument("--per-kind", type=int, default=500,
                        help="How many tests to generate per paper (default 500).")
    parser.add_argument("--reset", action="store_true",
                        help="Delete previously seeded tests first.")
    args = parser.parse_args()

    await init_db()

    if args.reset:
        removed = await reset()
        print(f"  removed {removed} previously seeded tests\n")

    await seed(args.per_kind)
    await verify()


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
