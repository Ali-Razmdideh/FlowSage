"""Password hashing (Argon2id) and JWT access-token encode/decode."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerificationError, InvalidHash):
        return False


def create_access_token(
    user_id: uuid.UUID, *, secret: str, algorithm: str, expires_minutes: int
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(token: str, *, secret: str, algorithm: str) -> uuid.UUID:
    """Raises `jwt.PyJWTError` (or a subclass) if the token is invalid, expired, or malformed."""
    payload = jwt.decode(token, secret, algorithms=[algorithm])
    return uuid.UUID(payload["sub"])
