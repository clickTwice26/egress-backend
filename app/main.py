import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .database import SessionLocal, init_db
from .routers import auth, community, roles, study_plan
from .sessions import purge_expired

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as db:
        removed = await purge_expired(db)
    if removed:
        logger.info("Purged %s expired session rows", removed)

    if not settings.turnstile_secret and settings.turnstile_enabled:
        logger.error(
            "TURNSTILE_SECRET is not set. Turnstile-protected endpoints will "
            "reject every request until it is configured."
        )
    yield


app = FastAPI(
    title="Egress API",
    description="Auth, sessions, and Cloudflare Turnstile verification.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    # PUT is here for reactions: reacting is an upsert of one row keyed by
    # (user, target), so it is idempotent — PUT, not POST. A method missing
    # from this list fails at the preflight, which reads in the browser as the
    # request never happening rather than as a CORS error.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router)
app.include_router(community.router)
app.include_router(roles.router)
app.include_router(study_plan.router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "turnstile_configured": bool(settings.turnstile_secret),
        "turnstile_enabled": settings.turnstile_enabled,
    }
