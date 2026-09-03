"""Shared FastAPI dependencies: session, current user, RBAC."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.session import get_session
from app.models.enums import Role
from app.models.models import User

bearer = HTTPBearer(auto_error=False)

ROLE_ORDER = {Role.VIEWER: 0, Role.ANALYST: 1, Role.ADMIN: 2}

# Revoked access-token ids. Redis-backed in production; the in-process set is a
# correct-but-single-worker fallback so logout still means something in dev.
_REVOKED: set[str] = set()


def revoke(jti: str) -> None:
    _REVOKED.add(jti)


def is_revoked(jti: str) -> bool:
    return jti in _REVOKED


async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session


async def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(db_session),
) -> User:
    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing bearer token. Call POST /api/v1/auth/login first.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(credentials.credentials, "access")
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Access token expired. Use POST /api/v1/auth/refresh."
        ) from None
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}") from None

    if is_revoked(str(payload.get("jti"))):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This token was revoked by logout.")

    user = await session.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer active.")
    request.state.user_id = str(user.id)
    return user


def require_role(minimum: Role) -> Callable:
    async def _dep(user: User = Depends(current_user)) -> User:
        if ROLE_ORDER.get(Role(user.role), 0) < ROLE_ORDER[minimum]:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"This action requires the '{minimum}' role; your role is '{user.role}'.",
            )
        return user

    return _dep


require_admin = require_role(Role.ADMIN)
require_analyst = require_role(Role.ANALYST)
