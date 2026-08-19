from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    # Import models so they are registered on Base.metadata before create_all.
    from . import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _add_missing_columns(conn)

    await _seed_roles()
    await _seed_topics()


async def _add_missing_columns(conn) -> None:
    """Bring an existing database up to the current schema.

    ``create_all`` adds tables but never alters one that already exists, so a
    database created before a column existed would keep failing on every query
    that selects it. There is no migration tool in this project yet; until there
    is, additive columns are applied here, guarded by a lookup so a fresh
    database and an old one converge on the same schema.
    """
    from sqlalchemy import text

    from .community import DEFAULT_TOPIC_SLUG
    from .roles import DEFAULT_ROLE_SLUG

    # (table, column, DDL for the column) — additive only. Anything that needs
    # a table rewritten or data moved belongs in a real migration, not here.
    additions = (
        ("users", "role_slug", f"VARCHAR(30) NOT NULL DEFAULT '{DEFAULT_ROLE_SLUG}'"),
        (
            "community_posts",
            "topic_slug",
            f"VARCHAR(30) NOT NULL DEFAULT '{DEFAULT_TOPIC_SLUG}'",
        ),
        # How many numbers a question covers. Existing rows are single-mark
        # items, which is exactly what the default says.
        ("test_questions", "span", "INTEGER NOT NULL DEFAULT 1"),
        # Academic vs General Training. Existing papers predate the split and
        # claim neither, which is what "both" means.
        ("tests", "module", "VARCHAR(10) NOT NULL DEFAULT 'both'"),
        # Nullable, because only completion and short-answer tasks carry a cap
        # and existing rows have never had one recorded.
        ("test_question_groups", "word_limit", "INTEGER"),
        ("test_question_groups", "speaking_part", "INTEGER"),
        ("test_question_groups", "writing_task", "INTEGER"),
    )

    for table, column, ddl in additions:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        columns = {row[1] for row in result}
        # An empty result means the table does not exist yet, in which case
        # create_all has just built it with every column already present.
        if columns and column not in columns:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


async def _seed_roles() -> None:
    """Insert any default role that is missing.

    Existing rows are left alone: a deployment that has retuned a weight, or
    added a role of its own, must survive a restart unchanged.
    """
    from sqlalchemy import select

    from .models import Role
    from .roles import DEFAULT_ROLES

    async with SessionLocal() as session:
        existing = set((await session.execute(select(Role.slug))).scalars())
        missing = [spec for spec in DEFAULT_ROLES if spec.slug not in existing]
        if not missing:
            return
        session.add_all(
            Role(slug=spec.slug, label=spec.label, weight=spec.weight) for spec in missing
        )
        await session.commit()


async def _seed_topics() -> None:
    """Insert any default topic that is missing, leaving existing ones alone."""
    from sqlalchemy import select

    from .community import DEFAULT_TOPICS
    from .models import Topic

    async with SessionLocal() as session:
        existing = set((await session.execute(select(Topic.slug))).scalars())
        missing = [spec for spec in DEFAULT_TOPICS if spec.slug not in existing]
        if not missing:
            return
        session.add_all(
            Topic(
                slug=spec.slug,
                label=spec.label,
                description=spec.description,
                position=spec.position,
            )
            for spec in missing
        )
        await session.commit()
