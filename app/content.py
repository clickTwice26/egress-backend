"""Test content: the kinds of test, and the kinds of question inside them.

Pure definitions. The shape here is deliberately the shape of a real IELTS
paper, because content that does not match the exam is content nobody can use:

    Test  ->  QuestionGroup  ->  Question

A **test** is one sitting: a mini test with around ten questions, not the full
four-section paper. A **question group** is what the exam calls a task — a run
of questions sharing one instruction ("Choose TWO letters, A-E"), one numbering
range and one answer format. A **question** is one numbered item.

That middle layer is the part worth insisting on. Ten flat questions cannot say
"7 to 10 all pick from this box of six options", and every IELTS task does
exactly that.
"""

from dataclasses import dataclass

TEST_KINDS: tuple[str, ...] = ("listening", "reading", "writing", "speaking")

TEST_KIND_LABELS: dict[str, str] = {
    "listening": "Listening",
    "reading": "Reading",
    "writing": "Writing",
    "speaking": "Speaking",
}

# Draft is the default: a half-written test must not be sittable.
TEST_STATUSES: tuple[str, ...] = ("draft", "published")

DIFFICULTIES: tuple[str, ...] = ("easy", "medium", "hard")

# Academic and General Training share Listening but diverge sharply in Reading
# and Writing, so a test declares who it is for. "both" is the honest default
# for Listening and for Speaking, which are identical across the two.
MODULES: tuple[str, ...] = ("both", "academic", "general")

MODULE_LABELS: dict[str, str] = {
    "both": "Academic and General",
    "academic": "Academic only",
    "general": "General Training only",
}

# Speaking is three fixed parts with different rules, not one pile of prompts.
SPEAKING_PARTS: tuple[int, ...] = (1, 2, 3)

SPEAKING_PART_LABELS: dict[int, str] = {
    1: "Part 1 — Introduction and interview",
    2: "Part 2 — Long turn (cue card)",
    3: "Part 3 — Two-way discussion",
}

# Part 2 is the only part with fixed timings the candidate is told about.
SPEAKING_PART_TWO_PREP_SECONDS = 60
SPEAKING_PART_TWO_TALK_SECONDS = 120

# Writing is two tasks with different weights: Task 2 is worth twice Task 1.
WRITING_TASKS: tuple[int, ...] = (1, 2)

WRITING_TASK_LABELS: dict[int, str] = {
    1: "Task 1",
    2: "Task 2",
}

WRITING_TASK_MIN_WORDS: dict[int, int] = {1: 150, 2: 250}
WRITING_TASK_MINUTES: dict[int, int] = {1: 20, 2: 40}
#: Task 2 carries twice the weight of Task 1 in the Writing band.
WRITING_TASK_WEIGHT: dict[int, int] = {1: 1, 2: 2}


@dataclass(frozen=True)
class QuestionTypeSpec:
    slug: str
    label: str
    #: Which papers this task appears in. A type is offered only for those.
    kinds: tuple[str, ...]
    #: The instruction wording the exam uses, offered as the default.
    default_instructions: str
    #: True when the group shares one bank of options (a "box" of A-F).
    group_options: bool = False
    #: True when each question carries its own options (A, B, C under a stem).
    question_options: bool = False
    #: How many answers one question takes. 2 means "choose TWO letters".
    answers_per_question: int = 1
    #: True when the group is written as a passage with numbered gaps in it.
    group_body: bool = False
    #: True when the task is scored against a word cap the candidate is given.
    #: Completion and short-answer tasks are; letter-choice tasks are not.
    takes_word_limit: bool = False
    #: The cap the exam most often prints for this task, pre-filled in the
    #: editor. None where the type has no cap.
    default_word_limit: int | None = None
    #: Fixed answer set the type always uses, so the editor can offer buttons
    #: instead of a free-text box and typos cannot enter the key.
    fixed_answers: tuple[str, ...] = ()
    #: True when the candidate's answer is a recording or an essay rather than
    #: a key — marked against descriptors, not compared to a string.
    rubric_marked: bool = False
    notes: str = ""


# The task types a mini test can be built from. Adding one here is enough for
# it to appear in the editor: nothing downstream switches on the slug.
QUESTION_TYPES: tuple[QuestionTypeSpec, ...] = (
    QuestionTypeSpec(
        slug="multiple_choice",
        label="Multiple choice — one answer",
        kinds=("listening", "reading"),
        default_instructions="Choose the correct letter, A, B or C.",
        question_options=True,
    ),
    QuestionTypeSpec(
        slug="multiple_choice_multi",
        label="Multiple choice — two answers",
        kinds=("listening", "reading"),
        default_instructions="Choose TWO letters, A-E.",
        question_options=True,
        answers_per_question=2,
        notes="One question, two letters. Both must be right to score.",
    ),
    QuestionTypeSpec(
        slug="matching",
        label="Matching from a box",
        kinds=("listening", "reading"),
        default_instructions="Choose FOUR answers from the box and write the correct letter, A-F.",
        group_options=True,
    ),
    QuestionTypeSpec(
        slug="matching_information",
        label="Matching information to paragraphs",
        kinds=("reading",),
        default_instructions=(
            "Which paragraph contains the following information? "
            "Write the correct letter, A-G."
        ),
        group_options=True,
        notes="The box is the paragraph letters. A letter may be used more than once.",
    ),
    QuestionTypeSpec(
        slug="matching_features",
        label="Matching features",
        kinds=("reading", "listening"),
        default_instructions="Match each statement with the correct person, A-E.",
        group_options=True,
        notes="The box is the people, theories or places being matched to.",
    ),
    QuestionTypeSpec(
        slug="matching_sentence_endings",
        label="Matching sentence endings",
        kinds=("reading",),
        default_instructions=(
            "Complete each sentence with the correct ending, A-F, below."
        ),
        group_options=True,
        notes="The stem is the sentence opening; the box holds the endings.",
    ),
    QuestionTypeSpec(
        slug="classification",
        label="Classification",
        kinds=("reading", "listening"),
        default_instructions="Classify the following as belonging to A, B or C.",
        group_options=True,
        notes="A small fixed set of categories, each usable more than once.",
    ),
    QuestionTypeSpec(
        slug="form_completion",
        label="Form completion",
        kinds=("listening",),
        default_instructions="Write ONE WORD AND/OR A NUMBER for each answer.",
        group_body=True,
        takes_word_limit=True,
        default_word_limit=1,
    ),
    QuestionTypeSpec(
        slug="note_completion",
        label="Note completion",
        kinds=("listening", "reading"),
        default_instructions="Write ONE WORD ONLY for each answer.",
        group_body=True,
        takes_word_limit=True,
        default_word_limit=1,
    ),
    QuestionTypeSpec(
        slug="table_completion",
        label="Table completion",
        kinds=("listening", "reading"),
        default_instructions="Write NO MORE THAN TWO WORDS for each answer.",
        group_body=True,
        takes_word_limit=True,
        default_word_limit=2,
    ),
    QuestionTypeSpec(
        slug="flow_chart_completion",
        label="Flow-chart completion",
        kinds=("listening", "reading"),
        default_instructions="Complete the flow-chart. Write NO MORE THAN TWO WORDS for each answer.",
        group_body=True,
        takes_word_limit=True,
        default_word_limit=2,
    ),
    QuestionTypeSpec(
        slug="summary_completion",
        label="Summary completion — from the passage",
        kinds=("reading", "listening"),
        default_instructions=(
            "Complete the summary below. "
            "Choose NO MORE THAN TWO WORDS from the passage for each answer."
        ),
        group_body=True,
        takes_word_limit=True,
        default_word_limit=2,
        notes="Words must be taken from the passage exactly as they appear.",
    ),
    QuestionTypeSpec(
        slug="summary_completion_bank",
        label="Summary completion — from a word bank",
        kinds=("reading",),
        default_instructions=(
            "Complete the summary using the list of words, A-I, below."
        ),
        group_body=True,
        group_options=True,
        notes="The answer is a letter from the bank, so there is no word cap.",
    ),
    QuestionTypeSpec(
        slug="sentence_completion",
        label="Sentence completion",
        kinds=("listening", "reading"),
        default_instructions="Complete the sentences below. Write NO MORE THAN TWO WORDS.",
        takes_word_limit=True,
        default_word_limit=2,
    ),
    QuestionTypeSpec(
        slug="short_answer",
        label="Short answer",
        kinds=("listening", "reading"),
        default_instructions="Answer the questions below. Write NO MORE THAN THREE WORDS.",
        takes_word_limit=True,
        default_word_limit=3,
    ),
    QuestionTypeSpec(
        slug="map_labelling",
        label="Map or plan labelling",
        kinds=("listening",),
        default_instructions="Label the map below. Write the correct letter, A-H.",
        group_options=True,
    ),
    QuestionTypeSpec(
        slug="diagram_labelling",
        label="Diagram labelling",
        kinds=("listening", "reading"),
        default_instructions="Label the diagram below. Write ONE WORD for each answer.",
        group_body=True,
        takes_word_limit=True,
        default_word_limit=1,
    ),
    QuestionTypeSpec(
        slug="true_false_notgiven",
        label="True / False / Not Given",
        kinds=("reading",),
        default_instructions=(
            "Do the following statements agree with the information in the passage? "
            "Write TRUE, FALSE or NOT GIVEN."
        ),
        fixed_answers=("TRUE", "FALSE", "NOT GIVEN"),
        notes="Tests facts stated in the passage.",
    ),
    QuestionTypeSpec(
        slug="yes_no_notgiven",
        label="Yes / No / Not Given",
        kinds=("reading",),
        default_instructions=(
            "Do the following statements agree with the claims of the writer? "
            "Write YES, NO or NOT GIVEN."
        ),
        fixed_answers=("YES", "NO", "NOT GIVEN"),
        notes=(
            "Tests the writer's opinions and claims, not facts. Using this where "
            "True/False/Not Given belongs is the most common authoring mistake."
        ),
    ),
    QuestionTypeSpec(
        slug="matching_headings",
        label="Matching headings",
        kinds=("reading",),
        default_instructions="Choose the correct heading for each paragraph from the list below.",
        group_options=True,
        notes="The box holds more headings than paragraphs; some are never used.",
    ),
    QuestionTypeSpec(
        slug="essay",
        label="Essay task",
        kinds=("writing",),
        default_instructions="Write at least 250 words.",
        rubric_marked=True,
        notes="Marked against the four Writing criteria; there is no answer key.",
    ),
    QuestionTypeSpec(
        slug="speaking_prompt",
        label="Speaking prompt",
        kinds=("speaking",),
        default_instructions="Answer the following questions.",
        rubric_marked=True,
        notes="Marked against the four Speaking criteria; the recording is the answer.",
    ),
)

QUESTION_TYPE_SLUGS: tuple[str, ...] = tuple(spec.slug for spec in QUESTION_TYPES)

_BY_SLUG = {spec.slug: spec for spec in QUESTION_TYPES}


def question_type(slug: str) -> QuestionTypeSpec | None:
    return _BY_SLUG.get(slug)


def types_for_kind(kind: str) -> list[QuestionTypeSpec]:
    return [spec for spec in QUESTION_TYPES if kind in spec.kinds]


# ---------------------------------------------------------------- rubrics ---

@dataclass(frozen=True)
class Criterion:
    """One of the four axes an essay or a recording is scored on."""

    slug: str
    label: str
    #: What the examiner is judging, in one line.
    summary: str


WRITING_CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        "task_achievement",
        "Task Achievement / Response",
        "Whether the question was answered fully, with a clear position and enough support.",
    ),
    Criterion(
        "coherence_cohesion",
        "Coherence and Cohesion",
        "Whether the argument is ordered logically and the links between ideas are signposted.",
    ),
    Criterion(
        "lexical_resource",
        "Lexical Resource",
        "The range and precision of vocabulary, including collocation and spelling.",
    ),
    Criterion(
        "grammatical_range",
        "Grammatical Range and Accuracy",
        "The variety of sentence structures and how error-free they are.",
    ),
)

SPEAKING_CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        "fluency_coherence",
        "Fluency and Coherence",
        "Whether speech flows without undue hesitation and holds together as an answer.",
    ),
    Criterion(
        "lexical_resource",
        "Lexical Resource",
        "The range and precision of vocabulary, and how well gaps are paraphrased around.",
    ),
    Criterion(
        "grammatical_range",
        "Grammatical Range and Accuracy",
        "The variety of structures attempted and how error-free they are.",
    ),
    Criterion(
        "pronunciation",
        "Pronunciation",
        "How intelligible the speech is across sounds, stress and intonation.",
    ),
)


def criteria_for_kind(kind: str) -> tuple[Criterion, ...]:
    if kind == "writing":
        return WRITING_CRITERIA
    if kind == "speaking":
        return SPEAKING_CRITERIA
    return ()


def overall_from_criteria(scores: dict[str, float]) -> float | None:
    """Average the four criteria, then round to the nearest half band.

    IELTS rounds a criterion average of .25 up to the next half band and .75 up
    to the next whole band, which is what `round(x * 2) / 2` produces at the
    boundaries that matter.
    """
    values = [value for value in scores.values() if value is not None]
    if not values:
        return None
    return round((sum(values) / len(values)) * 2) / 2


# ------------------------------------------------------------ band tables ---

# Raw-score-out-of-40 to band. Published conversion varies slightly between
# test versions, so these are the widely used indicative tables rather than a
# guarantee — which is why the platform calls the result an estimate.
#
# Each entry is (minimum raw score, band), highest first.
_LISTENING_BANDS: tuple[tuple[int, float], ...] = (
    (39, 9.0), (37, 8.5), (35, 8.0), (32, 7.5), (30, 7.0), (26, 6.5),
    (23, 6.0), (18, 5.5), (16, 5.0), (13, 4.5), (10, 4.0), (8, 3.5), (6, 3.0),
)

_ACADEMIC_READING_BANDS: tuple[tuple[int, float], ...] = (
    (39, 9.0), (37, 8.5), (35, 8.0), (33, 7.5), (30, 7.0), (27, 6.5),
    (23, 6.0), (19, 5.5), (15, 5.0), (13, 4.5), (10, 4.0), (8, 3.5), (6, 3.0),
)

# General Training Reading is marked harder: the texts are easier, so more
# right answers are needed for the same band.
_GENERAL_READING_BANDS: tuple[tuple[int, float], ...] = (
    (40, 9.0), (39, 8.5), (37, 8.0), (36, 7.5), (34, 7.0), (32, 6.5),
    (30, 6.0), (27, 5.5), (23, 5.0), (19, 4.5), (15, 4.0), (12, 3.5), (9, 3.0),
)

#: Full-paper length. Mini tests are scaled onto this before conversion.
FULL_PAPER_QUESTIONS = 40


def _table_for(kind: str, module: str) -> tuple[tuple[int, float], ...] | None:
    if kind == "listening":
        return _LISTENING_BANDS
    if kind == "reading":
        return _GENERAL_READING_BANDS if module == "general" else _ACADEMIC_READING_BANDS
    # Writing and Speaking have no raw score; they are scored on descriptors.
    return None


def band_for_raw(*, kind: str, module: str, correct: int, total: int) -> float | None:
    """Estimate a band from a raw score.

    The published tables are defined for a 40-question paper. A mini test is
    shorter, so the score is scaled onto 40 first. That scaling is the reason
    this is an estimate and not a result: a ten-question sample carries far more
    variance than the real paper, and one lucky guess moves the band further
    than it ever could over forty questions.

    Returns None for papers that have no answer key.
    """
    table = _table_for(kind, module)
    if table is None or total <= 0:
        return None

    correct = max(0, min(correct, total))
    scaled = round(correct / total * FULL_PAPER_QUESTIONS)

    for minimum, band in table:
        if scaled >= minimum:
            return band
    return 2.5 if scaled > 0 else 0.0


def overall_band(section_bands: dict[str, float]) -> float | None:
    """Mean of the four section bands, rounded to the nearest half band."""
    values = [value for value in section_bands.values() if value is not None]
    if not values:
        return None
    return round((sum(values) / len(values)) * 2) / 2


# ------------------------------------------------------------- validation ---

@dataclass
class ValidationIssue:
    """One thing wrong with a test, in the reviewer's words."""

    where: str
    problem: str


def review_test(
    *,
    kind: str,
    title: str,
    audio_url: str | None,
    groups: list[dict],
    module: str = "both",
    passage: str | None = None,
) -> list[ValidationIssue]:
    """Everything that would make a test unusable if it were published.

    Advisory, not enforced on save: a half-written test is a normal state, and
    an editor that refuses to save until the whole thing is right is an editor
    people work around. Publishing is where it counts, and the editor shows
    this list the whole time so nothing is a surprise at the end.
    """
    issues: list[ValidationIssue] = []

    if not title.strip():
        issues.append(ValidationIssue("Test", "It needs a title."))
    if kind == "listening" and not (audio_url or "").strip():
        issues.append(ValidationIssue("Test", "A listening test needs its audio."))
    if kind == "reading" and not (passage or "").strip():
        issues.append(ValidationIssue("Test", "A reading test needs its passage."))
    if not groups:
        issues.append(ValidationIssue("Test", "Add at least one question group."))

    # Reading and Writing differ between the two modules, so "both" is a
    # promise the paper almost certainly cannot keep.
    if kind in ("reading", "writing") and module == "both":
        issues.append(
            ValidationIssue(
                "Test",
                "Reading and Writing differ between Academic and General Training. "
                "Pick which module this paper is for.",
            )
        )

    numbers: dict[int, int] = {}
    seen_speaking_parts: set[int] = set()

    for group in groups:
        spec = question_type(group["type"])
        where = f"{spec.label if spec else group['type']}"

        if spec is None:
            issues.append(ValidationIssue(where, "Unknown question type."))
            continue
        if not group["questions"]:
            issues.append(ValidationIssue(where, "The group has no questions."))
        if spec.group_options and not [o for o in (group.get("options") or []) if str(o).strip()]:
            issues.append(ValidationIssue(where, "This type needs a box of options to choose from."))
        if spec.group_body and not (group.get("body") or "").strip():
            issues.append(
                ValidationIssue(where, "This type needs the notes, form or table the gaps sit in.")
            )

        # A completion task without a cap cannot be marked: an answer that is
        # correct but too long has to be rejected, and nothing records the cap.
        if spec.takes_word_limit and not group.get("word_limit"):
            issues.append(
                ValidationIssue(
                    where,
                    "Set the word limit. Without it an over-long answer cannot be marked wrong.",
                )
            )

        if kind == "speaking":
            part = group.get("speaking_part")
            if not part:
                issues.append(ValidationIssue(where, "Say which speaking part this is."))
            else:
                seen_speaking_parts.add(int(part))
                if int(part) == 2 and not (group.get("body") or "").strip():
                    issues.append(
                        ValidationIssue(
                            where, "Part 2 needs its cue card — the topic and its bullet points."
                        )
                    )

        if kind == "writing":
            task = group.get("writing_task")
            if not task:
                issues.append(ValidationIssue(where, "Say whether this is Task 1 or Task 2."))
            elif int(task) == 1 and module == "academic" and not (group.get("image_url") or "").strip():
                issues.append(
                    ValidationIssue(
                        where,
                        "Academic Task 1 describes a chart, graph or diagram — attach the image.",
                    )
                )

        for question in group["questions"]:
            number = question["number"]
            span = question.get("span", 1)
            # Every number the item covers is claimed, so an overlap between a
            # two-mark item and the item after it is caught.
            for covered in range(number, number + span):
                numbers[covered] = numbers.get(covered, 0) + 1
            label = f"{where}, question {number}"

            if spec.question_options and len(question.get("options") or []) < 2:
                issues.append(ValidationIssue(label, "Give it at least two options."))

            # Writing and speaking have no key; everything else must have one.
            expects_answer = not spec.rubric_marked
            answers = [a for a in (question.get("answers") or []) if str(a).strip()]
            if expects_answer and not answers:
                issues.append(ValidationIssue(label, "No answer recorded."))
            elif expects_answer and len(answers) < spec.answers_per_question:
                issues.append(
                    ValidationIssue(
                        label, f"This type takes {spec.answers_per_question} answers."
                    )
                )

            # A fixed-answer type can only hold its own vocabulary.
            if spec.fixed_answers:
                allowed = {value.upper() for value in spec.fixed_answers}
                for answer in answers:
                    if str(answer).strip().upper() not in allowed:
                        issues.append(
                            ValidationIssue(
                                label,
                                f"\"{answer}\" is not one of {', '.join(spec.fixed_answers)}.",
                            )
                        )

            # An answer longer than the cap the candidate was given is one the
            # marker would have to reject, so it cannot be the key.
            limit = group.get("word_limit")
            if spec.takes_word_limit and limit:
                for answer in answers:
                    if len(str(answer).split()) > int(limit):
                        issues.append(
                            ValidationIssue(
                                label,
                                f"\"{answer}\" is longer than the {limit}-word limit.",
                            )
                        )

    if kind == "speaking" and seen_speaking_parts and seen_speaking_parts != set(SPEAKING_PARTS):
        missing = sorted(set(SPEAKING_PARTS) - seen_speaking_parts)
        issues.append(
            ValidationIssue(
                "Test",
                "A speaking test runs all three parts. Missing: "
                + ", ".join(f"Part {part}" for part in missing)
                + ".",
            )
        )

    for number, count in sorted(numbers.items()):
        if count > 1:
            issues.append(ValidationIssue("Numbering", f"Question {number} appears {count} times."))

    return issues
