"""Validate incoming Pub/Sub push requests via OIDC JWT or shared secret fallback."""
import logging
from typing import Optional

import httpx
from jose import jwt, JWTError

from api.config import settings

logger = logging.getLogger(__name__)

_GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_cached_certs: Optional[dict] = None


async def _get_google_certs() -> dict:
    global _cached_certs
    if _cached_certs:
        return _cached_certs
    async with httpx.AsyncClient() as client:
        resp = await client.get(_GOOGLE_CERTS_URL, timeout=10)
        resp.raise_for_status()
        _cached_certs = resp.json()
    return _cached_certs


async def validate_oidc_token(token: str, audience: str) -> bool:
    """Validate a Google-signed OIDC JWT from Pub/Sub."""
    try:
        certs = await _get_google_certs()
        # jose expects keys list
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
        logger.error("Unexpected error validating OIDC token: %s", exc)
        return False


def validate_shared_secret(secret_header: Optional[str]) -> bool:
    """Validate X-Webhook-Secret header against configured secret."""
    if not settings.WEBHOOK_SECRET:
        return False
    return secret_header == settings.WEBHOOK_SECRET
