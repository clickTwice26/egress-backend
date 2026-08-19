import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .community import DEFAULT_TOPIC_SLUG
from .roles import DEFAULT_ROLE_SLUG


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware_utc(dt: datetime | None) -> datetime | None:
    """Coerce a datetime read back from the database to UTC-aware.

    SQLite has no native timezone-aware type, so ``DateTime(timezone=True)``
    round-trips as naive. Values are always written as UTC, so attaching UTC on
    read is correct and keeps comparisons from raising
    "can't compare offset-naive and offset-aware datetimes".
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class Role(Base):
    """A named position on the privilege scale.

    The row that matters is ``weight``: every permission check compares numbers,
    so a role can be renamed, or a new one inserted between two existing ones,
    without touching a single check. See ``app/roles.py``.
    """

    __tablename__ = "roles"

    slug: Mapped[str] = mapped_column(String(30), primary_key=True)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Stored lowercase so lookups are case-insensitive without a functional index.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Argon2id hash. Never stores or logs the plaintext password.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    module: Mapped[str] = mapped_column(String(20), default="academic", nullable=False)
    role_slug: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("roles.slug", ondelete="RESTRICT"),
        default=DEFAULT_ROLE_SLUG,
        nullable=False,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Joined eagerly: every authenticated request asks for the weight, and a
    # lazy load on an async session raises rather than quietly issuing a query.
    role: Mapped["Role"] = relationship(back_populates="users", lazy="joined")

    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def role_weight(self) -> int:
        return self.role.weight


class AuthSession(Base):
    """One row per issued refresh token.

    Refresh tokens rotate on every use. Each rotation writes a new row sharing
    the original ``family_id`` and marks the previous row revoked. Presenting an
    already-rotated token means the token leaked and is being replayed, so the
    entire family is revoked (reuse detection).

    Tokens are stored as SHA-256 digests. They are 256-bit random values, so a
    fast hash is sufficient — there is nothing to brute-force — and a database
    leak alone does not yield usable session tokens.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Groups every rotation of one login. Revoked as a unit on reuse detection.
    family_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    access_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Ceiling for the whole family, carried across rotations so a session
    # cannot be extended indefinitely by refreshing.
    family_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set when this row is superseded by a rotation; distinguishes a normal
    # rotation from an explicit logout.
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")

    __table_args__ = (Index("ix_auth_sessions_family_revoked", "family_id", "revoked_at"),)

    def access_token_valid(self, now: datetime) -> bool:
        return self.revoked_at is None and aware_utc(self.access_expires_at) > now

    def refresh_token_valid(self, now: datetime) -> bool:
        return (
            self.revoked_at is None
            and self.rotated_at is None
            and aware_utc(self.refresh_expires_at) > now
            and aware_utc(self.family_expires_at) > now
        )


class StudyPlan(Base):
    """A generated week-by-week schedule leading up to a user's test date.

    One plan per user: regenerating replaces the previous plan and its tasks
    rather than accumulating them, which keeps "my plan" unambiguous.
    """

    __tablename__ = "study_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )

    test_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_band: Mapped[float] = mapped_column(Float, nullable=False)
    hours_per_week: Mapped[int] = mapped_column(Integer, nullable=False)

    # The self-assessed starting point, kept so the plan can be explained back
    # to the user and regenerated on the same basis.
    listening_band: Mapped[float] = mapped_column(Float, nullable=False)
    reading_band: Mapped[float] = mapped_column(Float, nullable=False)
    writing_band: Mapped[float] = mapped_column(Float, nullable=False)
    speaking_band: Mapped[float] = mapped_column(Float, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    tasks: Mapped[list["StudyTask"]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="StudyTask.scheduled_on, StudyTask.position",
    )


class StudyTask(Base):
    """One scheduled study session on one day."""

    __tablename__ = "study_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("study_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )

    scheduled_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Ordering within a day, so two sessions on the same date stay stable.
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    skill: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    plan: Mapped["StudyPlan"] = relationship(back_populates="tasks")

    __table_args__ = (Index("ix_study_tasks_plan_date", "plan_id", "scheduled_on"),)

    @property
    def completed(self) -> bool:
        return self.completed_at is not None


# ---- Community ----
#
# Three tables carry the feed: posts, comments and reactions. Two decisions are
# worth stating up front because everything else follows from them.
#
# 1. Counts are denormalised onto the row they describe. A feed page renders
#    "12 comments · 40 reactions" for twenty posts; recounting those from the
#    child tables would be forty aggregate queries per page. They are kept
#    honest by updating them in the same transaction as the write that moves
#    them, with `x = x + 1` so two concurrent writers cannot lose an increment.
#
# 2. Deletion is soft. A deleted post whose comments vanished would tear holes
#    in every thread quoting it, so the row survives with `deleted_at` set and
#    the API renders a tombstone. Purging is a separate, deliberate job.


class Post(Base):
    """One entry in the community feed.

    A share is a post like any other with ``shared_post_id`` pointing at the
    original — the same row shape, so the feed query does not special-case it,
    and a share can carry its own body ("this helped me").
    """

    __tablename__ = "community_posts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    author_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Which bucket the post is filed under. Required (with a default) rather
    # than nullable: "no topic" and "General" would be the same thing to a
    # reader, and two ways to say it means two branches in every query.
    topic_slug: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("community_topics.slug", ondelete="RESTRICT"),
        default=DEFAULT_TOPIC_SLUG,
        nullable=False,
        index=True,
    )

    # Self-reference: the post this one shares. Never chained — sharing a share
    # points at the original, so rendering is one level deep, always.
    shared_post_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("community_posts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    comment_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    share_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    author: Mapped["User"] = relationship(lazy="joined")
    shared_post: Mapped["Post | None"] = relationship(remote_side=[id], lazy="joined", join_depth=1)
    topic: Mapped["Topic"] = relationship(lazy="joined")
    tags: Mapped[list["PostTag"]] = relationship(
        back_populates="post", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        # The feed's keyset index: newest first, id breaking ties. Covers both
        # the ordering and the "not deleted" filter the feed always applies.
        Index("ix_community_posts_feed", "deleted_at", "created_at", "id"),
        # The same page, narrowed to one topic — the filter row's query.
        Index("ix_community_posts_topic_feed", "topic_slug", "deleted_at", "created_at", "id"),
    )

    @property
    def deleted(self) -> bool:
        return self.deleted_at is not None


class Comment(Base):
    """A comment on a post, or a reply to another comment.

    One table serves both: a reply is a comment whose ``parent_id`` is set.
    ``post_id`` is carried on every row, including deep replies, so a thread can
    be counted or purged without walking the chain.
    """

    __tablename__ = "community_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    post_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("community_comments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    author_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    body: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reply_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    author: Mapped["User"] = relationship(lazy="joined")

    __table_args__ = (
        # Root comments of a post, oldest first — the thread page's query.
        Index("ix_community_comments_thread", "post_id", "parent_id", "created_at", "id"),
        # Replies to one comment, same ordering.
        Index("ix_community_comments_replies", "parent_id", "created_at", "id"),
    )

    @property
    def deleted(self) -> bool:
        return self.deleted_at is not None


class Reaction(Base):
    """One account's reaction to one post or comment.

    The unique constraint is the whole concurrency story: an account holds at
    most one reaction per target, so reacting again *changes* the kind rather
    than stacking, and a double-tap cannot double-count.

    ``target_type`` plus ``target_id`` rather than two nullable foreign keys:
    reactions are read by target, never joined across, and one table means one
    index and one code path for posts and comments alike.
    """

    __tablename__ = "community_reactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_type: Mapped[str] = mapped_column(String(10), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "target_type", "target_id", name="uq_reaction_per_target"),
        # The breakdown query: every reaction on a page of targets, grouped.
        Index("ix_community_reactions_target", "target_type", "target_id", "kind"),
    )


class Topic(Base):
    """A curated bucket a post is filed under.

    A table rather than an enum so a topic can be added, renamed or reordered
    without a deployment, and so a post's foreign key keeps the set honest.
    """

    __tablename__ = "community_topics"

    slug: Mapped[str] = mapped_column(String(30), primary_key=True)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # Display order, spaced so a new topic can be slotted between two existing
    # ones — the same trick the role weights use.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)


class PostTag(Base):
    """One hashtag on one post.

    A row per (post, tag) rather than a delimited string on the post: this is
    what makes "everything tagged #band7" an index lookup instead of a table
    scan with a LIKE, and what lets the trending list be a GROUP BY.

    Tags are derived from the body on every write, so this table is a
    projection of the text — never edited on its own.
    """

    __tablename__ = "community_post_tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    post_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tag: Mapped[str] = mapped_column(String(50), nullable=False)

    post: Mapped["Post"] = relationship(back_populates="tags")

    __table_args__ = (
        UniqueConstraint("post_id", "tag", name="uq_tag_per_post"),
        # Posts carrying one tag, and the trending count, both start here.
        Index("ix_community_post_tags_tag", "tag", "post_id"),
    )


# ---- Test content ----
#
# Test -> QuestionGroup -> Question, matching how an IELTS paper is actually
# built. See app/content.py for why the middle layer exists.


class Test(Base):
    """One sitting: a mini test of roughly ten questions.

    `kind` carries listening, reading, writing or speaking rather than four
    near-identical tables. The papers differ in which *question types* they
    allow and which media they carry, not in their structure, and a shared
    table means the editor, the listing and the CRUD are written once.
    """

    __tablename__ = "tests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # Stable, human-readable, unique — the URL a test will eventually live at.
    slug: Mapped[str] = mapped_column(String(220), unique=True, nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    difficulty: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft", index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)

    # Which candidates the paper is for. Listening and Speaking are identical
    # across the two, so they stay "both"; Reading and Writing genuinely differ
    # and a paper that does not say which it is cannot be served correctly.
    module: Mapped[str] = mapped_column(String(10), nullable=False, default="both", index=True)

    # Listening carries audio; reading carries a passage; writing and speaking
    # carry neither. Nullable rather than four subclass tables.
    audio_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    passage: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped["User | None"] = relationship(lazy="joined")
    groups: Mapped[list["QuestionGroup"]] = relationship(
        back_populates="test",
        cascade="all, delete-orphan",
        order_by="QuestionGroup.position",
        lazy="selectin",
    )

    __table_args__ = (
        # The library listing: one kind, published first, newest first.
        Index("ix_tests_kind_status", "kind", "status", "created_at"),
    )


class QuestionGroup(Base):
    """One task: a run of questions sharing an instruction and a format."""

    __tablename__ = "test_question_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    test_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    type: Mapped[str] = mapped_column(String(40), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The form, table or notes the gaps sit in, for completion tasks.
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The shared box of choices for matching tasks, as a JSON array of strings.
    # JSON rather than another table: the options are meaningless apart from
    # their group, are always read whole, and are never queried across.
    options: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # A map or diagram the questions label.
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # The cap the candidate is given: "NO MORE THAN TWO WORDS" is 2. Marking
    # needs this as a number — an answer over the cap is wrong even when it
    # means the right thing, and that rule cannot be read out of prose.
    word_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Speaking only. Which of the three parts this task belongs to; Part 2 is
    # the cue card, with its own prep and talk timings.
    speaking_part: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Writing only. Task 1 or Task 2 — they differ in length, time and weight.
    writing_task: Mapped[int | None] = mapped_column(Integer, nullable=True)

    test: Mapped["Test"] = relationship(back_populates="groups")
    questions: Mapped[list["Question"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        order_by="Question.position",
        lazy="selectin",
    )

    __table_args__ = (Index("ix_question_groups_test_position", "test_id", "position"),)


class Question(Base):
    """One numbered item."""

    __tablename__ = "test_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("test_question_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The number printed on the paper. Not derived from position: a group can
    # be reordered without renumbering the test under the reader.
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    # How many numbers this item covers. "Questions 1-2: choose TWO letters" is
    # one item worth two marks, so it spans two — which is why a ten-question
    # paper does not always hold ten rows.
    span: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    options: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Every accepted spelling or wording: ["colour", "color"]. A list, because
    # marking a gap-fill against one exact string fails honest answers.
    answers: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")

    group: Mapped["QuestionGroup"] = relationship(back_populates="questions")

    __table_args__ = (Index("ix_questions_group_position", "group_id", "position"),)


class Attempt(Base):
    """One sitting of one test by one candidate.

    Kept separate from the test it is an attempt at, so a test can be edited or
    unpublished without rewriting anyone's history. `kind` and `module` are
    copied at submission rather than read back through the relationship for the
    same reason: the band a candidate was given must not change because someone
    later retagged the paper.
    """

    __tablename__ = "attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    test_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tests.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # in_progress -> submitted. A submitted attempt is never reopened; a retake
    # is a new row, which is what makes "last six tests" meaningful.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="in_progress", index=True)

    # Snapshotted from the test at submission so the score stays reproducible.
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    module: Mapped[str] = mapped_column(String(10), nullable=False, default="both")

    raw_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Null while in progress, and null for papers that only an examiner can score.
    band: Mapped[float | None] = mapped_column(Float, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    test: Mapped["Test"] = relationship(lazy="joined")
    answers: Mapped[list["AttemptAnswer"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan", lazy="selectin"
    )
    criterion_scores: Mapped[list["AttemptCriterionScore"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        # The candidate's own history, newest first.
        Index("ix_attempts_user_submitted", "user_id", "submitted_at"),
    )


class AttemptAnswer(Base):
    """What the candidate wrote for one numbered item, and how it scored."""

    __tablename__ = "attempt_answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    attempt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("test_questions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # A list because one item can take more than one answer ("choose TWO").
    response: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Null until marked, and null for rubric-marked papers.
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    marks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    attempt: Mapped["Attempt"] = relationship(back_populates="answers")

    __table_args__ = (
        # One answer per question per attempt; saving again overwrites.
        Index("ix_attempt_answers_unique", "attempt_id", "question_id", unique=True),
    )


class AttemptCriterionScore(Base):
    """One examiner score on one of the four Writing or Speaking criteria."""

    __tablename__ = "attempt_criterion_scores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    attempt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Matches a slug from WRITING_CRITERIA or SPEAKING_CRITERIA.
    criterion: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Half bands are legal, so this is a float rather than an int.
    score: Mapped[float] = mapped_column(Float, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")

    scored_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    attempt: Mapped["Attempt"] = relationship(back_populates="criterion_scores")

    __table_args__ = (
        Index("ix_criterion_scores_unique", "attempt_id", "criterion", unique=True),
    )
