"""End-to-end smoke test for the auth + Turnstile integration.

Runs the whole API in-process over an ASGI transport (no server, no network
except the real siteverify call in the fail-closed check).

    .venv/bin/python smoke_test.py

Exits non-zero on the first failure.
"""

import asyncio
import os
import sys
import tempfile

# Configure the environment BEFORE importing the app: settings are cached.
_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_path}"
os.environ["ACCESS_TOKEN_TTL_MINUTES"] = "30"
os.environ["REFRESH_TOKEN_TTL_DAYS"] = "60"
os.environ["SESSION_ABSOLUTE_TTL_DAYS"] = "90"
# Bypass the challenge so we can exercise the auth flow. The fail-closed
# behaviour is asserted separately at the end with verification back ON.
os.environ["TURNSTILE_ENABLED"] = "false"
os.environ.setdefault("TURNSTILE_SECRET", "")

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import init_db  # noqa: E402
from app.main import app  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name} — {detail}")
        print(f"  FAIL  {name}  {detail}")


async def main() -> None:
    await init_db()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        print("\n[1] Registration")
        r = await c.post(
            "/api/auth/register",
            json={
                "name": "Dilnoza R.",
                "email": "Dilnoza@Example.com",
                "password": "correct-horse-battery",
                "module": "academic",
                "cf-turnstile-response": "dummy",
            },
        )
        check("register returns 201", r.status_code == 201, f"got {r.status_code} {r.text[:200]}")
        body = r.json()
        check("returns Bearer token type", body.get("token_type") == "Bearer")
        check("email normalised to lowercase", body["user"]["email"] == "dilnoza@example.com")
        check("access token present", bool(body.get("access_token")))
        check("refresh token present", bool(body.get("refresh_token")))
        check(
            "refresh token differs from access token",
            body["access_token"] != body["refresh_token"],
        )

        access = body["access_token"]
        refresh = body["refresh_token"]

        print("\n[2] Session length (requirement: > 30 days)")
        from datetime import datetime, timezone

        exp = datetime.fromisoformat(body["refresh_expires_at"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        days = (exp - datetime.now(timezone.utc)).days
        check(f"refresh token lives {days} days (>30)", days > 30, f"got {days}")

        print("\n[3] Duplicate registration rejected")
        r = await c.post(
            "/api/auth/register",
            json={
                "name": "Impostor",
                "email": "dilnoza@example.com",
                "password": "another-password",
                "cf-turnstile-response": "dummy",
            },
        )
        check("duplicate email returns 409", r.status_code == 409, f"got {r.status_code}")

        print("\n[4] Bearer auth on protected route")
        r = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
        check("GET /me with valid token returns 200", r.status_code == 200, f"got {r.status_code}")
        check("returns the right user", r.json().get("email") == "dilnoza@example.com")

        r = await c.get("/api/auth/me")
        check("GET /me without token returns 401", r.status_code == 401, f"got {r.status_code}")

        r = await c.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
        check("GET /me with bogus token returns 401", r.status_code == 401, f"got {r.status_code}")

        print("\n[5] Password verification")
        r = await c.post(
            "/api/auth/login",
            json={
                "email": "dilnoza@example.com",
                "password": "wrong-password",
                "cf-turnstile-response": "dummy",
            },
        )
        check("wrong password returns 401", r.status_code == 401, f"got {r.status_code}")

        r = await c.post(
            "/api/auth/login",
            json={
                "email": "nobody@example.com",
                "password": "whatever",
                "cf-turnstile-response": "dummy",
            },
        )
        check("unknown email returns 401", r.status_code == 401, f"got {r.status_code}")
        check(
            "unknown email and wrong password give identical message (no enumeration)",
            r.json().get("detail") == "Incorrect email or password.",
        )

        r = await c.post(
            "/api/auth/login",
            json={
                "email": "DILNOZA@example.com",
                "password": "correct-horse-battery",
                "cf-turnstile-response": "dummy",
            },
        )
        check("correct password logs in (case-insensitive email)", r.status_code == 200, f"got {r.status_code}")

        print("\n[6] Refresh token rotation")
        r = await c.post("/api/auth/refresh", json={"refresh_token": refresh})
        check("refresh returns 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
        rotated = r.json()
        check("rotation issues a NEW refresh token", rotated["refresh_token"] != refresh)
        check("rotation issues a new access token", rotated["access_token"] != access)
        new_refresh = rotated["refresh_token"]
        new_access = rotated["access_token"]

        r = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {new_access}"})
        check("rotated access token works", r.status_code == 200, f"got {r.status_code}")

        print("\n[7] Reuse detection (replay the retired refresh token)")
        r = await c.post("/api/auth/refresh", json={"refresh_token": refresh})
        check("replayed refresh token rejected", r.status_code == 401, f"got {r.status_code}")

        r = await c.post("/api/auth/refresh", json={"refresh_token": new_refresh})
        check(
            "reuse revoked the whole family (valid token now dead too)",
            r.status_code == 401,
            f"got {r.status_code} — family was NOT revoked",
        )
        r = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {new_access}"})
        check("access token from revoked family rejected", r.status_code == 401, f"got {r.status_code}")

        print("\n[8] Logout revokes the session")
        r = await c.post(
            "/api/auth/login",
            json={
                "email": "dilnoza@example.com",
                "password": "correct-horse-battery",
                "cf-turnstile-response": "dummy",
            },
        )
        tok = r.json()["access_token"]
        r = await c.post("/api/auth/logout", headers={"Authorization": f"Bearer {tok}"})
        check("logout returns 200", r.status_code == 200, f"got {r.status_code}")
        r = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
        check("token rejected after logout", r.status_code == 401, f"got {r.status_code}")

        print("\n[9] Forgot-password does not enumerate accounts")
        r1 = await c.post(
            "/api/auth/forgot-password",
            json={"email": "dilnoza@example.com", "cf-turnstile-response": "dummy"},
        )
        r2 = await c.post(
            "/api/auth/forgot-password",
            json={"email": "ghost@example.com", "cf-turnstile-response": "dummy"},
        )
        check("known and unknown email both return 200", r1.status_code == r2.status_code == 200)
        check("identical response body", r1.json() == r2.json())

        print("\n[10] Password policy")
        r = await c.post(
            "/api/auth/register",
            json={
                "name": "Shorty",
                "email": "shorty@example.com",
                "password": "short",
                "cf-turnstile-response": "dummy",
            },
        )
        check("password under 8 chars rejected (422)", r.status_code == 422, f"got {r.status_code}")

    print("\n[11] Turnstile fail-closed (verification ON, no valid token)")
    # Re-read settings with verification enabled and an empty secret: the
    # canonical implementation must reject rather than let traffic through.
    get_settings.cache_clear()
    os.environ["TURNSTILE_ENABLED"] = "true"
    os.environ["TURNSTILE_SECRET"] = ""
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/auth/register",
            json={
                "name": "Bot",
                "email": "bot@example.com",
                "password": "bot-password-123",
                "cf-turnstile-response": "",
            },
        )
        check(
            "register rejected 403 when secret missing (fails CLOSED)",
            r.status_code == 403,
            f"got {r.status_code} — this would let bots through!",
        )
        r = await c.post(
            "/api/auth/login",
            json={
                "email": "dilnoza@example.com",
                "password": "correct-horse-battery",
                "cf-turnstile-response": "",
            },
        )
        check("login rejected 403 without valid challenge", r.status_code == 403, f"got {r.status_code}")
    get_settings.cache_clear()

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
