"""Cryptographically signed tenant-context claims for PostgreSQL RLS prototypes.

This module is deliberately not wired into the current Flask application. It
creates a short-lived, canonical payload which PostgreSQL can verify using a
key held in a protected schema. Database custom GUC values are transport only;
they are not trusted until the database-side HMAC and membership checks pass.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any, Mapping

ISSUER = "erp-global-pro"
AUDIENCE = "erp-postgres-tenant-context"
MAX_TTL_SECONDS = 900
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _strict_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidTenantClaim(f"{name} must be an integer")
    return value


class InvalidTenantClaim(ValueError):
    """Raised when tenant-context claims cannot be safely issued or verified."""


def sign_tenant_claims(
    *,
    company_id: int,
    user_id: int,
    key_id: str,
    secret: bytes,
    ttl_seconds: int = 300,
    now: int | None = None,
) -> tuple[str, str]:
    """Return canonical JSON claims and an HMAC-SHA256 hex signature."""
    company_id = _strict_integer(company_id, "company_id")
    user_id = _strict_integer(user_id, "user_id")
    ttl_seconds = _strict_integer(ttl_seconds, "ttl_seconds")
    if company_id <= 0 or user_id <= 0:
        raise InvalidTenantClaim("company_id and user_id must be positive")
    if not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id):
        raise InvalidTenantClaim("key_id has an invalid format")
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise InvalidTenantClaim("HMAC secret must contain at least 32 bytes")
    if not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise InvalidTenantClaim(f"ttl_seconds must be between 1 and {MAX_TTL_SECONDS}")

    issued_at = int(time.time()) if now is None else _strict_integer(now, "now")
    claims = {
        "aud": AUDIENCE,
        "company_id": company_id,
        "exp": issued_at + ttl_seconds,
        "iat": issued_at,
        "iss": ISSUER,
        "jti": secrets.token_urlsafe(18),
        "kid": key_id,
        "sub": user_id,
    }
    payload = json.dumps(claims, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return payload, signature


def verify_tenant_claims(
    payload: str,
    signature: str,
    keys: Mapping[str, bytes],
    *,
    now: int | None = None,
) -> dict[str, Any]:
    """Verify signature, audience, identifiers and short expiry; does not verify membership.

    Membership and company status must also be checked by PostgreSQL against its
    authoritative company_memberships and companies tables.
    """
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > 4096:
        raise InvalidTenantClaim("Tenant claim payload is invalid or too large")
    if not isinstance(signature, str) or not _HEX_SHA256_RE.fullmatch(signature):
        raise InvalidTenantClaim("Tenant claim signature has an invalid format")
    try:
        claims = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise InvalidTenantClaim("Tenant claim payload is not valid JSON") from exc
    if not isinstance(claims, dict):
        raise InvalidTenantClaim("Tenant claim payload must be a JSON object")

    required = {"iss", "aud", "kid", "sub", "company_id", "iat", "exp", "jti"}
    if set(claims) != required:
        raise InvalidTenantClaim("Tenant claim fields do not match the required contract")
    if claims["iss"] != ISSUER or claims["aud"] != AUDIENCE:
        raise InvalidTenantClaim("Tenant claim issuer or audience is invalid")
    key_id = claims["kid"]
    if not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id):
        raise InvalidTenantClaim("Tenant claim key id is invalid")
    secret = keys.get(key_id)
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise InvalidTenantClaim("Tenant claim signing key is unavailable")
    expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise InvalidTenantClaim("Tenant claim signature does not match")

    for name in ("sub", "company_id", "iat", "exp"):
        if isinstance(claims[name], bool) or not isinstance(claims[name], int):
            raise InvalidTenantClaim(f"Tenant claim {name} must be an integer")
    if claims["sub"] <= 0 or claims["company_id"] <= 0:
        raise InvalidTenantClaim("Tenant claim identifiers must be positive")
    current = int(time.time()) if now is None else _strict_integer(now, "now")
    if claims["iat"] > current + 30:
        raise InvalidTenantClaim("Tenant claim was issued in the future")
    if claims["exp"] <= current or claims["exp"] <= claims["iat"]:
        raise InvalidTenantClaim("Tenant claim has expired")
    if claims["exp"] - claims["iat"] > MAX_TTL_SECONDS:
        raise InvalidTenantClaim("Tenant claim lifetime exceeds the maximum")
    if not isinstance(claims["jti"], str) or not 16 <= len(claims["jti"]) <= 128:
        raise InvalidTenantClaim("Tenant claim token identifier is invalid")
    return claims
