from __future__ import annotations

import uuid
from datetime import UTC, datetime

import jwt
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, db_session, revoke
from app.core.config import settings
from app.core.ratelimit import get_limiter
from app.core.security import (
    create_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.models.models import SystemAuditLog, User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut
from app.schemas.common import Message

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "ois_refresh"  # noqa: S105 - a cookie name, not a secret


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=settings.REFRESH_TOKEN_DAYS * 86400,
        path="/api/v1/auth",
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(db_session),
) -> TokenResponse:
    client_ip = request.client.host if request.client else "unknown"
    limiter = get_limiter()
    if not await limiter.allow(f"rl:login:{client_ip}", settings.LOGIN_RATE_LIMIT_PER_MINUTE):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts. Wait a minute.")

    user = (
        await session.execute(sa.select(User).where(User.email == payload.email.lower()))
    ).scalar_one_or_none()

    # Constant-ish work whether or not the user exists, to avoid enumeration.
    stored_hash = user.password_hash if user else hash_password("placeholder-not-a-real-password")
    ok = verify_password(payload.password, stored_hash)

    if not user or not ok or not user.is_active:
        session.add(
            SystemAuditLog(actor_label=payload.email, action="auth.login_failed", ip_address=client_ip)
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    access, _jti, expires_at = create_token(str(user.id), "access", role=user.role)
    refresh, _rjti, _rexp = create_token(str(user.id), "refresh", role=user.role)
    _set_refresh_cookie(response, refresh)

    user.last_login_at = datetime.now(UTC)
    session.add(
        SystemAuditLog(
            actor_user_id=user.id, actor_label=user.email, action="auth.login", ip_address=client_ip
        )
    )
    return TokenResponse(access_token=access, expires_at=expires_at)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request, response: Response, session: AsyncSession = Depends(db_session)
) -> TokenResponse:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh cookie present.")
    try:
        payload = decode_token(token, "refresh")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid refresh token: {exc}") from None

    user = await session.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer active.")

    access, _jti, expires_at = create_token(str(user.id), "access", role=user.role)
    new_refresh, _rjti, _rexp = create_token(str(user.id), "refresh", role=user.role)
    _set_refresh_cookie(response, new_refresh)  # rotation
    return TokenResponse(access_token=access, expires_at=expires_at)


@router.post("/logout", response_model=Message)
async def logout(request: Request, response: Response, user: User = Depends(current_user)) -> Message:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            revoke(str(decode_token(auth.split(" ", 1)[1], "access").get("jti")))
        except jwt.InvalidTokenError:
            pass
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")
    return Message(detail="Signed out.")


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> User:
    return user
