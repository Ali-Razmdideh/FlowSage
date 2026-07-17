"""Password hashing (Argon2id) and JWT access-token encode/decode."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache

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


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    """A fixed Argon2 hash to verify against when no user was found.

    Callers should still run `verify_password(password, dummy_password_hash())` on the
    "unknown email" path so it costs about the same time as a real failed login --
    otherwise the login endpoint's response time leaks which emails have accounts.
    """
    return _hasher.hash("not-a-real-password-timing-safety-only")


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
