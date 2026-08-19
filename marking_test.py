"""End-to-end test of marking and the attempt flow.

    .venv/bin/python marking_test.py

Runs the API in-process over an ASGI transport. Exits non-zero on any failure.
"""

import asyncio
import os
import sys
import tempfile

_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_path}"
os.environ["TURNSTILE_ENABLED"] = "false"
os.environ.setdefault("TURNSTILE_SECRET", "")

import httpx  # noqa: E402

from app.database import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.marking import answer_matches, mark_attempt, normalise, word_count  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name} — {detail}")
        print(f"  FAIL  {name}  {detail}")


def unit_tests() -> None:
    print("\n[1] Normalisation")
    check("case ignored", normalise("Library") == "library")
    check("whitespace collapsed", normalise("  the   main  library ") == "the main library")
    check("edge punctuation stripped", normalise("library.") == "library")
    check("inner hyphen kept", normalise("well-known") == "well-known")
    check("apostrophe kept", normalise("children's") == "children's")

    print("\n[2] Word counting")
    check("single word", word_count("library") == 1)
    check("article counts as a word", word_count("the library") == 2)
    check("hyphenated compound is one word", word_count("well-known") == 1)

    print("\n[3] Matching")
    check("exact match", answer_matches("library", ["library"], None))
    check("case-insensitive match", answer_matches("LIBRARY", ["library"], None))
    check("trailing period tolerated", answer_matches("library.", ["library"], None))
    check("authored spelling variant", answer_matches("color", ["colour", "color"], None))
    check("wrong answer rejected", not answer_matches("museum", ["library"], None))
    check("blank rejected", not answer_matches("   ", ["library"], None))

    print("\n[4] Word limit outranks the key")
    check(
        "'the library' rejected under a ONE WORD limit",
        not answer_matches("the library", ["the library"], 1),
        "an over-long answer must fail even when it matches",
    )
    check("same answer accepted under a TWO WORD limit", answer_matches("the library", ["the library"], 2))
    check(
        "articles are NOT silently stripped",
        not answer_matches("the library", ["library"], 1),
        "stripping 'the' would hide a real error and inflate the band",
    )


def marking_tests() -> None:
    print("\n[5] Marking a paper")
    groups = [
        {
            "type": "note_completion",
            "word_limit": 1,
            "questions": [
                {"id": "q1", "number": 1, "span": 1, "answers": ["library"], "explanation": ""},
                {"id": "q2", "number": 2, "span": 1, "answers": ["Tuesday"], "explanation": ""},
            ],
        },
        {
            "type": "multiple_choice_multi",
            "word_limit": None,
            "questions": [
                {"id": "q3", "number": 3, "span": 2, "answers": ["A", "C"], "explanation": ""},
            ],
        },
    ]

    marked = mark_attempt(
        kind="listening",
        module="both",
        groups=groups,
        responses={"q1": ["Library"], "q2": ["tuesday"], "q3": ["C", "A"]},
    )
    check("all correct scores full marks", marked.raw_score == 4, f"got {marked.raw_score}")
    check("max score counts spans", marked.max_score == 4, f"got {marked.max_score}")
    check("two-letter item accepted in any order", marked.results[2].correct is True)

    partial = mark_attempt(
        kind="listening", module="both", groups=groups,
        responses={"q1": ["the library"], "q2": ["Tuesday"], "q3": ["A", "B"]},
    )
    check("over-limit answer scores 0", partial.results[0].correct is False)
    check(
        "half-right multi-answer scores 0, not 1",
        partial.results[2].marks == 0,
        "'choose TWO' is all-or-nothing",
    )
    check("raw score reflects only the correct item", partial.raw_score == 1, f"got {partial.raw_score}")

    blank = mark_attempt(kind="listening", module="both", groups=groups, responses={})
    check("unanswered paper scores 0", blank.raw_score == 0)
    check("blank answers marked wrong, not skipped", all(r.correct is False for r in blank.results))

    print("\n[6] Rubric papers carry no raw band")
    essay = mark_attempt(
        kind="writing", module="academic",
        groups=[{
            "type": "essay", "word_limit": None,
            "questions": [{"id": "e1", "number": 1, "span": 1, "answers": [], "explanation": ""}],
        }],
        responses={"e1": ["My essay..."]},
    )
    check("no band from an essay", essay.band is None)
    check("flagged as needing an examiner", essay.needs_rubric is True)
    check("correctness is null, not false", essay.results[0].correct is None)


async def api_tests() -> None:
    await init_db()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # An author account, to create the paper.
        r = await c.post("/api/auth/register", json={
            "name": "Author", "email": "author@example.com",
            "password": "author-password-1", "cf-turnstile-response": "x"})
        author = r.json()["access_token"]
        ah = {"Authorization": f"Bearer {author}"}

        # Promote to a role that can manage tests.
        from sqlalchemy import update
        from app.database import SessionLocal
        from app.models import User
        async with SessionLocal() as db:
            await db.execute(update(User).where(User.email == "author@example.com")
                             .values(role_slug="admin"))
            await db.commit()

        print("\n[7] Authoring a sittable paper")
        r = await c.post("/api/content/tests", headers=ah, json={
            "kind": "listening", "title": "Mini Listening", "audio_url": "a.mp3",
            "duration_minutes": 10})
        if r.status_code not in (200, 201):
            check("create test", False, f"HTTP {r.status_code} {r.text[:200]}")
            return
        test_id = r.json()["id"]

        r = await c.post(f"/api/content/tests/{test_id}/groups", headers=ah,
                         json={"type": "note_completion", "body": "gap ___ (1)"})
        group = r.json()
        check("group pre-filled with the type's word limit", group["word_limit"] == 1,
              f"got {group['word_limit']}")

        r = await c.post(f"/api/content/groups/{group['id']}/questions", headers=ah, json={})
        qid = r.json()["id"]
        await c.patch(f"/api/content/questions/{qid}", headers=ah,
                      json={"number": 1, "answers": ["library"]})
        await c.patch(f"/api/content/tests/{test_id}", headers=ah, json={"status": "published"})

        # A separate candidate account.
        r = await c.post("/api/auth/register", json={
            "name": "Dilnoza R.", "email": "sitter@example.com",
            "password": "sitter-password-1", "cf-turnstile-response": "x"})
        sh = {"Authorization": f"Bearer {r.json()['access_token']}"}

        print("\n[8] Sitting the paper")
        r = await c.post(f"/api/attempts?test_id={test_id}", headers=sh)
        check("attempt starts", r.status_code == 201, f"HTTP {r.status_code} {r.text[:200]}")
        attempt = r.json()
        check("answer key withheld before submit",
              all(res.get("accepted") is None for res in attempt.get("results", [])))

        r2 = await c.post(f"/api/attempts?test_id={test_id}", headers=sh)
        check("re-starting resumes the open attempt", r2.json()["id"] == attempt["id"],
              "a dropped connection must not cost the candidate their answers")

        await c.patch(f"/api/attempts/{attempt['id']}/answers", headers=sh,
                      json={"answers": [{"question_id": qid, "response": ["Library"]}]})

        r = await c.post(f"/api/attempts/{attempt['id']}/submit", headers=sh)
        result = r.json()
        check("submit marks the paper", r.status_code == 200, f"HTTP {r.status_code}")
        check("case-insensitive answer accepted", result["raw_score"] == 1, f"got {result['raw_score']}")
        check("band produced", result["band"] is not None)
        check("answer key revealed after submit",
              result["results"][0]["accepted"] == ["library"])

        r = await c.post(f"/api/attempts/{attempt['id']}/submit", headers=sh)
        check("re-submitting is idempotent", r.json()["raw_score"] == 1)

        r = await c.patch(f"/api/attempts/{attempt['id']}/answers", headers=sh,
                          json={"answers": [{"question_id": qid, "response": ["museum"]}]})
        check("cannot edit a submitted attempt", r.status_code == 409, f"got {r.status_code}")

        print("\n[9] Attempts are private")
        r = await c.get(f"/api/attempts/{attempt['id']}", headers=ah)
        check("another account gets 404, not the attempt", r.status_code == 404, f"got {r.status_code}")

        print("\n[10] Band prediction")
        r = await c.get("/api/attempts/me/prediction", headers=sh)
        pred = r.json()
        listening = next(s for s in pred["skills"] if s["skill"] == "listening")
        check("listening band present", listening["band"] is not None)
        writing = next(s for s in pred["skills"] if s["skill"] == "writing")
        check("never-sat skill is null, not zero", writing["band"] is None,
              "averaging a missing skill as 0 would wreck the overall estimate")
        check("overall ignores unsat skills", pred["overall"] == listening["band"],
              f"got {pred['overall']}")
        check("window matches the dashboard's 'last 6 tests'", pred["window"] == 6)


async def main() -> None:
    unit_tests()
    marking_tests()
    await api_tests()

    print("\n" + "=" * 60)
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("\nFailures:")
        for f in FAILED:
            print(f"  - {f}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        try:
            os.unlink(_db_path)
        except OSError:
            pass
    sys.exit(1 if FAILED else 0)
