"""Validate incoming Pub/Sub push requests via OIDC JWT or shared secret fallback."""
import logging
import time
from typing import Optional

import httpx
from jose import jwt, JWTError

from api.config import settings

logger = logging.getLogger(__name__)

_GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_CERT_CACHE_TTL = 3600  # 1 hour

_cached_certs: Optional[dict] = None
_cached_certs_at: float = 0.0


async def _get_google_certs() -> dict:
    """Fetch Google's public OIDC certs, cached for 1 hour.

    On network failure falls back to the last cached value if available,
    so a transient Google outage does not block legitimate recordings.
    """
    global _cached_certs, _cached_certs_at

    now = time.monotonic()
    if _cached_certs and (now - _cached_certs_at) < _CERT_CACHE_TTL:
        return _cached_certs

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(_GOOGLE_CERTS_URL, timeout=10)
            resp.raise_for_status()
            _cached_certs = resp.json()
            _cached_certs_at = now
            return _cached_certs
    except Exception as exc:
        if _cached_certs:
            logger.warning(
                "Failed to refresh Google OIDC certs (%s) — using cached certs from %.0fs ago",
                exc, now - _cached_certs_at,
            )
            return _cached_certs
        # No cache at all — re-raise so the caller can fall back to shared secret
        raise


async def validate_oidc_token(token: str, audience: str) -> bool:
    """Validate a Google-signed OIDC JWT from Pub/Sub."""
    try:
        certs = await _get_google_certs()
        claims = jwt.decode(
            token,
            certs,
            algorithms=["RS256"],
            audience=audience,
            options={"verify_at_hash": False},
        )
        iss = claims.get("iss", "")
        if iss not in ("https://accounts.google.com", "accounts.google.com"):
            logger.warning("OIDC token has unexpected issuer: %s", iss)
            return False
        email = claims.get("email", "")
        expected = settings.PUBSUB_SERVICE_ACCOUNT_EMAIL
        if expected and email != expected:
            logger.warning("OIDC token email mismatch: got %s, expected %s", email, expected)
            return False
        return True
    except JWTError as exc:
        logger.debug("OIDC JWT validation failed: %s", exc)
        return False
    except Exception as exc:
        # Cert fetch failed with no cache — log and return False so the
        # webhook handler can fall back to shared-secret validation.
        logger.warning("OIDC cert fetch failed, falling back to shared-secret auth: %s", exc)
        return False


def validate_shared_secret(secret_header: Optional[str]) -> bool:
    """Validate X-Webhook-Secret header against configured secret."""
    if not settings.WEBHOOK_SECRET:
        return False
    return secret_header == settings.WEBHOOK_SECRET
