# Egress API

FastAPI backend: registration, login, bearer-token sessions, and Cloudflare
Turnstile verification.

## Setup

```bash
cd backend
uv venv
uv pip install -r pyproject.toml
cp .env.example .env
```

Then put the Turnstile secret in `.env`:

```
TURNSTILE_SECRET=<paste from the Turnstile dashboard>
```

The secret is read from the environment only. It is never committed, logged, or
written into source. The **site key** is public and lives in the frontend.

## Run

```bash
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
```

Interactive API docs: http://localhost:8000/docs
Health (shows whether the secret is configured): http://localhost:8000/health

## Test

```bash
cd backend && .venv/bin/python smoke_test.py
```

30 checks covering registration, login, bearer auth, rotation, reuse detection,
logout, account-enumeration resistance, and Turnstile fail-closed behaviour.

## Endpoints

| Method | Path                        | Turnstile | Auth   |
| ------ | --------------------------- | --------- | ------ |
| POST   | `/api/auth/register`        | required  | —      |
| POST   | `/api/auth/login`           | required  | —      |
| POST   | `/api/auth/forgot-password` | required  | —      |
| POST   | `/api/auth/refresh`         | —         | —      |
| POST   | `/api/auth/logout`          | —         | Bearer |
| GET    | `/api/auth/me`              | —         | Bearer |

## Session model

Two opaque tokens, both stored in the database as SHA-256 digests.

| Token   | Default TTL | Purpose                                          |
| ------- | ----------- | ------------------------------------------------ |
| Access  | 30 minutes  | `Authorization: Bearer <token>` on each request  |
| Refresh | 60 days     | Exchanges for a new pair; carries the session    |

A session family also has a hard ceiling (`SESSION_ABSOLUTE_TTL_DAYS`, 90 days)
that rotation cannot extend.

**Why opaque instead of JWT.** A signed JWT cannot be revoked before it
expires. For a 60-day session that means a stolen token stays valid for two
months. These tokens are looked up per request, so logout and reuse detection
take effect immediately.

**Rotation and reuse detection.** Every `/refresh` issues a new pair and retires
the old refresh token. Presenting a retired token means it leaked, so the entire
family is revoked — attacker and legitimate user are both signed out, which
surfaces the compromise instead of hiding it.

**Password hashing.** Argon2id via `argon2-cffi`, the current OWASP
recommendation. Hashes are transparently upgraded on login when parameters
change. Login burns comparable CPU on the unknown-email path so response timing
does not reveal which addresses are registered.

## Turnstile verification

`app/turnstile.py` posts to
`https://challenges.cloudflare.com/turnstile/v0/siteverify` with
`secret` / `response` / `remoteip` and allows the request through **only** when
the parsed body has `success is True`.

Every other outcome fails closed: network error, timeout, non-2xx, non-JSON
body, missing token, and — importantly — a missing `TURNSTILE_SECRET`. A
misconfigured deployment rejects traffic rather than silently disabling bot
protection.

`TURNSTILE_ENABLED=false` bypasses verification for offline local development.
Never set it in production.

## Not included

- Email delivery for password reset. `/forgot-password` verifies the challenge
  and returns a constant response; wire a mailer where `routers/auth.py` marks
  the spot, and issue a single-use expiring reset token there.
- Rate limiting. Turnstile blocks bots, not a determined human hammering login
  from one browser. Add per-IP and per-account throttling before production.
- The SQLite default is for development. Point `DATABASE_URL` at Postgres for
  production (`postgresql+asyncpg://…`).
