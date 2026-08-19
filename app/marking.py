"""Marking a sitting against its answer key.

Pure functions over plain data, so the rules can be read and tested without a
database in the way.

The hard part of marking IELTS is not comparing strings — it is deciding which
differences matter. The decisions taken here, and why:

* **Case and spacing never matter.** "Library", "library" and " library " are
  the same answer written by three people under time pressure.
* **Edge punctuation never matters.** A trailing full stop is a habit, not a
  wrong answer.
* **Articles matter.** "the library" is not silently reduced to "library",
  because under a ONE WORD limit the article is exactly what makes it wrong.
  Stripping it would hide a real error and inflate the band.
* **Spelling and number variants are authored, not guessed.** Anything else
  acceptable — "colour"/"color", "20"/"twenty" — belongs in the question's
  answer list. Inferring them here would silently accept answers the exam
  would reject, and the author is the one who knows which apply.
* **The word limit outranks the key.** An answer that matches but runs over
  the cap is wrong, because the real exam rejects it.
"""

import re
from dataclasses import dataclass, field

from .content import band_for_raw, question_type

# Punctuation people add at the edges of a written answer. Anything inside the
# answer — hyphens, apostrophes — is part of the word and stays.
_EDGE_PUNCTUATION = ".,;:!?\"'()[]{}"


def normalise(text: str) -> str:
    """The comparable form of a written answer."""
    cleaned = str(text).strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(_EDGE_PUNCTUATION).strip()


def word_count(text: str) -> int:
    """Words as the exam counts them.

    A hyphenated compound is one word, and a number is one word, which is what
    splitting on whitespace already gives.
    """
    return len(normalise(text).split())


def within_limit(response: str, word_limit: int | None) -> bool:
    if not word_limit:
        return True
    return word_count(response) <= word_limit


def answer_matches(response: str, accepted: list[str], word_limit: int | None) -> bool:
    """True when one written answer earns its mark."""
    if not str(response).strip():
        return False
    # Checked before the key: over-long answers fail even when they match.
    if not within_limit(response, word_limit):
        return False
    target = normalise(response)
    return any(target == normalise(candidate) for candidate in accepted if str(candidate).strip())


@dataclass
class QuestionResult:
    question_id: str
    number: int
    span: int
    #: What the candidate wrote. Empty list means they left it blank.
    response: list[str]
    accepted: list[str]
    #: None where the paper is marked against descriptors rather than a key.
    correct: bool | None
    #: Marks earned. A two-letter item is worth its span, all-or-nothing.
    marks: int
    max_marks: int
    explanation: str
    #: Set when the answer was right but broke the word cap, so the result can
    #: say why rather than just showing a cross.
    over_word_limit: bool = False


@dataclass
class MarkedAttempt:
    results: list[QuestionResult] = field(default_factory=list)
    raw_score: int = 0
    max_score: int = 0
    #: None for writing and speaking, which have no raw score.
    band: float | None = None
    #: True when the paper is scored by descriptors and needs an examiner.
    needs_rubric: bool = False


def mark_question(
    *,
    question: dict,
    spec_slug: str,
    word_limit: int | None,
    response: list[str],
) -> QuestionResult:
    """Mark one numbered item."""
    spec = question_type(spec_slug)
    accepted = [a for a in (question.get("answers") or []) if str(a).strip()]
    span = int(question.get("span", 1) or 1)
    given = [r for r in (response or []) if str(r).strip()]

    base = QuestionResult(
        question_id=question["id"],
        number=question["number"],
        span=span,
        response=list(response or []),
        accepted=accepted,
        correct=None,
        marks=0,
        max_marks=span,
        explanation=question.get("explanation", "") or "",
    )

    # Essays and recordings have no key; an examiner scores them separately.
    if spec is not None and spec.rubric_marked:
        return base

    required = spec.answers_per_question if spec else 1

    if len(given) < required:
        base.correct = False
        return base

    # "Choose TWO letters" is one item worth two marks and is all-or-nothing:
    # both letters must be right, and the order they were written in is not
    # part of the answer.
    remaining = list(accepted)
    matched = 0
    for written in given[:required]:
        if not within_limit(written, word_limit):
            base.over_word_limit = True
            continue
        for index, candidate in enumerate(remaining):
            if normalise(written) == normalise(candidate):
                remaining.pop(index)
                matched += 1
                break

    base.correct = matched >= required
    base.marks = span if base.correct else 0
    return base


def mark_attempt(
    *,
    kind: str,
    module: str,
    groups: list[dict],
    responses: dict[str, list[str]],
) -> MarkedAttempt:
    """Mark a whole sitting.

    `responses` maps question id to what the candidate wrote — a list because a
    single item can take more than one answer.
    """
    attempt = MarkedAttempt()

    for group in groups:
        word_limit = group.get("word_limit")
        for question in group.get("questions", []):
            result = mark_question(
                question=question,
                spec_slug=group["type"],
                word_limit=word_limit,
                response=responses.get(question["id"], []),
            )
            attempt.results.append(result)
            attempt.max_score += result.max_marks
            attempt.raw_score += result.marks
            if result.correct is None:
                attempt.needs_rubric = True

    # A paper with no key at all is entirely examiner-marked; a mixed paper
    # still gets its objective band, with the rubric part scored separately.
    if attempt.max_score > 0 and not all(r.correct is None for r in attempt.results):
        attempt.band = band_for_raw(
            kind=kind, module=module, correct=attempt.raw_score, total=attempt.max_score
        )

    return attempt
