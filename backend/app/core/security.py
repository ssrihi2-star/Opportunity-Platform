"""Password hashing, JWT issue/verify, credential encryption."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters long.")
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return True


def create_token(
    subject: str,
    kind: Literal["access", "refresh"],
    role: str = "viewer",
    expires_delta: timedelta | None = None,
) -> tuple[str, str, datetime]:
    """Return (encoded_token, jti, expires_at)."""
    now = datetime.now(UTC)
    if expires_delta is None:
        expires_delta = (
            timedelta(minutes=settings.ACCESS_TOKEN_MINUTES)
            if kind == "access"
            else timedelta(days=settings.REFRESH_TOKEN_DAYS)
        )
    expires_at = now + expires_delta
    jti = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "type": kind,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": jti,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM), jti, expires_at


def decode_token(token: str, expected_kind: Literal["access", "refresh"]) -> dict[str, Any]:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    if payload.get("type") != expected_kind:
        raise jwt.InvalidTokenError(f"Expected a {expected_kind} token but received {payload.get('type')!r}.")
    return payload


def _fernet() -> Fernet:
    return Fernet(settings.SECRET_ENCRYPTION_KEY.encode())


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "Stored credential could not be decrypted. SECRET_ENCRYPTION_KEY has probably "
            "changed; re-enter the source credential."
        ) from exc


def mask_secret(plaintext: str) -> str:
    """Show only enough to recognise a key. Never return a full secret over the API."""
    if len(plaintext) <= 8:
        return "*" * len(plaintext)
    return f"{plaintext[:3]}...{plaintext[-3:]}"
