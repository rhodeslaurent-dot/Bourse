"""Cloudflare Access JWT verification (docs/14 §14.1) + application token for n8n/webhooks.

Every request must carry a valid ``Cf-Access-Jwt-Assertion`` (audience, issuer, expiry) when
``CF_ACCESS_TEAM_DOMAIN`` and ``CF_ACCESS_AUD`` are set. Exempt: ``/health`` (minimal) and
``/api/webhooks/*`` (own HMAC secret, phase 6). ``X-App-Token`` (``APP_API_TOKEN``) is accepted
for the JSON API (n8n). Without CF variables the check is *disabled* and /sante says so.
"""

from __future__ import annotations

import hmac
import os
import time
from typing import Any

import httpx
import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

EXEMPT_PREFIXES = ("/health", "/api/webhooks/", "/static/")


def cf_settings() -> tuple[str, str] | None:
    team = os.environ.get("CF_ACCESS_TEAM_DOMAIN")
    aud = os.environ.get("CF_ACCESS_AUD")
    if team and aud:
        return team.rstrip("/"), aud
    return None


class _Jwks:
    def __init__(self) -> None:
        self._keys: dict[str, Any] | None = None
        self._fetched = 0.0

    def get(self, team: str) -> dict[str, Any]:
        if self._keys is None or time.time() - self._fetched > 3600:
            url = f"https://{team}/cdn-cgi/access/certs"
            self._keys = httpx.get(url, timeout=10).json()
            self._fetched = time.time()
        return self._keys

    def set_for_tests(self, keys: dict[str, Any]) -> None:
        self._keys = keys
        self._fetched = time.time()


JWKS = _Jwks()


def verify_cf_token(token: str, team: str, aud: str) -> dict[str, Any]:
    keys = JWKS.get(team)
    header = jwt.get_unverified_header(token)
    jwk = next((k for k in keys.get("keys", []) if k.get("kid") == header.get("kid")), None)
    if jwk is None:
        raise jwt.InvalidTokenError("unknown kid")
    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
    return jwt.decode(token, public_key, algorithms=["RS256"], audience=aud, issuer=f"https://{team}")


def app_token_ok(header_value: str | None) -> bool:
    expected = os.environ.get("APP_API_TOKEN")
    return bool(expected) and header_value is not None and hmac.compare_digest(header_value, expected)


class AccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        path = request.url.path
        if path.startswith(EXEMPT_PREFIXES):
            return await call_next(request)
        settings = cf_settings()
        if settings is None:
            if app_token_ok(request.headers.get("X-App-Token")):
                request.state.auth = "app_token"
                return await call_next(request)
            if request.method != "GET" and os.environ.get("ALLOW_UNAUTHENTICATED_WRITES") != "1":
                return JSONResponse(
                    {
                        "detail": "accès non protégé : définir CF_ACCESS_TEAM_DOMAIN/CF_ACCESS_AUD (ou ALLOW_UNAUTHENTICATED_WRITES=1 en développement local) avant toute écriture"  # noqa: E501
                    },
                    status_code=503,
                )
            request.state.auth = "disabled"
            return await call_next(request)
        if app_token_ok(request.headers.get("X-App-Token")):
            request.state.auth = "app_token"
            return await call_next(request)
        token = request.headers.get("Cf-Access-Jwt-Assertion") or request.cookies.get("CF_Authorization")
        if not token:
            return JSONResponse({"detail": "Cloudflare Access token required"}, status_code=401)
        try:
            claims = verify_cf_token(token, *settings)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"detail": f"invalid access token: {exc.__class__.__name__}"}, status_code=401)
        allowed = os.environ.get("CF_ACCESS_EMAIL")
        if allowed and claims.get("email", "").lower() != allowed.lower():
            return JSONResponse({"detail": "email not allowed"}, status_code=403)
        request.state.auth = "cloudflare"
        return await call_next(request)
