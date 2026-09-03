"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import (
    BudgetExceededError,
    ImmutableRowError,
    OISError,
    RateLimitedError,
    RobotsDisallowedError,
    SourceConfigError,
    SSRFBlockedError,
    UngroundedReportError,
)
from app.core.logging import configure_logging, get_logger, request_id_var
from app.core.middleware import RateLimitMiddleware, RequestContextMiddleware

log = get_logger("app")

DESCRIPTION = """
Evidence-first opportunity research. **Not financial advice.**

Every derived number in this API comes from a deterministic calculation over stored
evidence. Language models are used only for narrative and classification, never for
arithmetic, and never without citations.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.LOG_LEVEL, json_output=settings.ENV != "dev")
    log.info("startup", env=settings.ENV, app=settings.APP_NAME)
    yield
    log.info("shutdown")


app = FastAPI(
    title=settings.APP_NAME,
    version="0.2.0",
    description=DESCRIPTION,
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(RequestContextMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


def _error(status_code: int, detail: str, code: str) -> JSONResponse:
    return JSONResponse(
        {"detail": detail, "code": code, "request_id": request_id_var.get()},
        status_code=status_code,
    )


@app.exception_handler(ImmutableRowError)
async def _immutable(_: Request, exc: ImmutableRowError) -> JSONResponse:
    return _error(409, str(exc), "immutable_row")


@app.exception_handler(SSRFBlockedError)
async def _ssrf(_: Request, exc: SSRFBlockedError) -> JSONResponse:
    return _error(400, str(exc), "ssrf_blocked")


@app.exception_handler(RobotsDisallowedError)
async def _robots(_: Request, exc: RobotsDisallowedError) -> JSONResponse:
    return _error(403, str(exc), "robots_disallowed")


@app.exception_handler(SourceConfigError)
async def _source_config(_: Request, exc: SourceConfigError) -> JSONResponse:
    return _error(422, str(exc), "source_misconfigured")


@app.exception_handler(RateLimitedError)
async def _rate_limited(_: Request, exc: RateLimitedError) -> JSONResponse:
    return _error(429, str(exc), "source_rate_limited")


@app.exception_handler(BudgetExceededError)
async def _budget(_: Request, exc: BudgetExceededError) -> JSONResponse:
    return _error(429, str(exc), "ai_budget_exceeded")


@app.exception_handler(UngroundedReportError)
async def _ungrounded(_: Request, exc: UngroundedReportError) -> JSONResponse:
    return _error(422, str(exc), "ungrounded_report")


@app.exception_handler(OISError)
async def _generic(_: Request, exc: OISError) -> JSONResponse:
    return _error(400, str(exc), "application_error")


@app.get("/", include_in_schema=False)
async def root() -> PlainTextResponse:
    return PlainTextResponse(
        f"{settings.APP_NAME} - see {settings.API_V1_PREFIX}/openapi.json or /docs.\n"
        "Research tool. Not financial advice.\n"
    )
