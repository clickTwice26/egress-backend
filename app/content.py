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

from dataclasses import dataclass, field

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
        slug="form_completion",
        label="Form completion",
        kinds=("listening",),
        default_instructions="Write ONE WORD AND/OR A NUMBER for each answer.",
        group_body=True,
    ),
    QuestionTypeSpec(
        slug="note_completion",
        label="Note completion",
        kinds=("listening", "reading"),
        default_instructions="Write ONE WORD ONLY for each answer.",
        group_body=True,
    ),
    QuestionTypeSpec(
        slug="table_completion",
        label="Table completion",
        kinds=("listening", "reading"),
        default_instructions="Write NO MORE THAN TWO WORDS for each answer.",
        group_body=True,
    ),
    QuestionTypeSpec(
        slug="sentence_completion",
        label="Sentence completion",
        kinds=("listening", "reading"),
        default_instructions="Complete the sentences below. Write NO MORE THAN TWO WORDS.",
    ),
    QuestionTypeSpec(
        slug="short_answer",
        label="Short answer",
        kinds=("listening", "reading"),
        default_instructions="Answer the questions below. Write NO MORE THAN THREE WORDS.",
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
    ),
    QuestionTypeSpec(
        slug="true_false_notgiven",
        label="True / False / Not Given",
        kinds=("reading",),
        default_instructions="Do the following statements agree with the information in the passage?",
    ),
    QuestionTypeSpec(
        slug="matching_headings",
        label="Matching headings",
        kinds=("reading",),
        default_instructions="Choose the correct heading for each paragraph from the list below.",
        group_options=True,
    ),
    QuestionTypeSpec(
        slug="essay",
        label="Essay task",
        kinds=("writing",),
        default_instructions="Write at least 250 words.",
        notes="Marked by a human or a model; there is no answer key.",
    ),
    QuestionTypeSpec(
        slug="speaking_prompt",
        label="Speaking prompt",
        kinds=("speaking",),
        default_instructions="Answer the following questions.",
        notes="No answer key; the recording is the answer.",
    ),
)

QUESTION_TYPE_SLUGS: tuple[str, ...] = tuple(spec.slug for spec in QUESTION_TYPES)

_BY_SLUG = {spec.slug: spec for spec in QUESTION_TYPES}


def question_type(slug: str) -> QuestionTypeSpec | None:
    return _BY_SLUG.get(slug)


def types_for_kind(kind: str) -> list[QuestionTypeSpec]:
    return [spec for spec in QUESTION_TYPES if kind in spec.kinds]


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
    if not groups:
        issues.append(ValidationIssue("Test", "Add at least one question group."))

    numbers: dict[int, int] = {}

    for group in groups:
        spec = question_type(group["type"])
        where = f"{spec.label if spec else group['type']}"

        if spec is None:
            issues.append(ValidationIssue(where, "Unknown question type."))
            continue
        if not group["questions"]:
            issues.append(ValidationIssue(where, "The group has no questions."))
        if spec.group_options and not group.get("options"):
            issues.append(ValidationIssue(where, "This type needs a box of options to choose from."))

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
            expects_answer = kind not in ("writing", "speaking")
            answers = question.get("answers") or []
            if expects_answer and not answers:
                issues.append(ValidationIssue(label, "No answer recorded."))
            elif expects_answer and len(answers) < spec.answers_per_question:
                issues.append(
                    ValidationIssue(
                        label, f"This type takes {spec.answers_per_question} answers."
                    )
                )

    for number, count in sorted(numbers.items()):
        if count > 1:
            issues.append(ValidationIssue("Numbering", f"Question {number} appears {count} times."))

    return issues
