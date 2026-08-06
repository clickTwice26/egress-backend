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


async def _add_missing_columns(conn) -> None:
    """Bring an existing database up to the current schema.

    ``create_all`` adds tables but never alters one that already exists, so a
    database created before a column existed would keep failing on every query
    that selects it. There is no migration tool in this project yet; until there
    is, additive columns are applied here, guarded by a lookup so a fresh
    database and an old one converge on the same schema.
    """
    from sqlalchemy import text

    result = await conn.execute(text("PRAGMA table_info(users)"))
    columns = {row[1] for row in result}
    if "role_slug" not in columns:
        from .roles import DEFAULT_ROLE_SLUG

        await conn.execute(
            text(f"ALTER TABLE users ADD COLUMN role_slug VARCHAR(30) NOT NULL DEFAULT '{DEFAULT_ROLE_SLUG}'")
        )


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
