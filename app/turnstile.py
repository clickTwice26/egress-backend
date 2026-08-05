import logging

import httpx

from .config import get_settings

logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

_TIMEOUT = httpx.Timeout(10.0)


async def verify_turnstile(token: str | None, client_ip: str | None) -> bool:
    """Canonical Cloudflare Turnstile server-side verification.

    POSTs the token to siteverify and returns True only when the parsed
    response has ``success is True``. Every other outcome — network error,
    non-2xx, non-JSON body, missing token — returns False (fail closed).

    The secret is read from the TURNSTILE_SECRET environment variable via
    settings and is never logged.
    """
    settings = get_settings()

    if not settings.turnstile_enabled:
        logger.warning("Turnstile verification is DISABLED via TURNSTILE_ENABLED=false")
        return True

    if not settings.turnstile_secret:
        # Misconfiguration must not silently allow traffic through.
        logger.error("TURNSTILE_SECRET is not set; rejecting request (fail closed)")
        return False

    if not token:
        return False

    payload = {
        "secret": settings.turnstile_secret,
        "response": token,
    }
    if client_ip:
        payload["remoteip"] = client_ip

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                SITEVERIFY_URL,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if response.status_code != httpx.codes.OK:
            logger.warning("siteverify returned HTTP %s", response.status_code)
            return False
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Network failure, timeout, or non-JSON body. Fail closed.
        logger.warning("siteverify call failed: %s", exc.__class__.__name__)
        return False

    if not isinstance(result, dict):
        return False

    if result.get("success") is True:
        return True

    logger.info("Turnstile rejected token: %s", result.get("error-codes"))
    return False


def client_ip_from_request(request) -> str | None:
    """Best-effort client IP for the siteverify ``remoteip`` field.

    Cloudflare sets CF-Connecting-IP. Behind another proxy, the left-most
    X-Forwarded-For entry is the original client. Only trust these when the
    app actually sits behind a proxy that overwrites them.
    """
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()

    return request.client.host if request.client else None
