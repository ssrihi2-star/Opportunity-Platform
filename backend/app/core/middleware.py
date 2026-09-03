"""Request id, security headers, rate limiting and audit logging."""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.core.logging import get_logger, request_id_var
from app.core.ratelimit import get_limiter

log = get_logger("http")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
        "connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'"
    ),
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_var.set(request_id)
        start = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        duration_ms = int((time.monotonic() - start) * 1000)
        response.headers["X-Request-ID"] = request_id
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if settings.COOKIE_SECURE:
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Coarse per-IP limit. Endpoint-specific limits live in the routers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith("/api/"):
            client_ip = request.client.host if request.client else "unknown"
            limiter = get_limiter()
            if not await limiter.allow(f"rl:ip:{client_ip}", settings.RATE_LIMIT_PER_MINUTE):
                return JSONResponse(
                    {
                        "detail": "Rate limit exceeded. Slow down and retry in a minute.",
                        "code": "rate_limited",
                    },
                    status_code=429,
                )
        return await call_next(request)
