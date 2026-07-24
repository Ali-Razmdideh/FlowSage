"""Encryption at rest for secret columns (`JiraIntegration.api_token`,
`Webhook.secret`). `Settings.secret_encryption_key` is an arbitrary string (not
required to already be a valid Fernet key) -- `derive_fernet_key` stretches it
into one via SHA-256, so operators can set a plain passphrase env var the same
way `JWT_SECRET` works today, without needing to run a key-generation command
first."""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Callable

from cryptography.fernet import Fernet
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator


def derive_fernet_key(passphrase: str) -> bytes:
    digest = hashlib.sha256(passphrase.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt(plaintext: str, key: bytes) -> str:
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str, key: bytes) -> str:
    return Fernet(key).decrypt(ciphertext.encode("ascii")).decode("utf-8")


class EncryptedString(TypeDecorator[str]):
    """Transparently encrypts on write / decrypts on read. `key_provider` is
    called lazily on each bind/result (not once at class-definition time) so
    tests can swap in a per-`Settings`-instance key without module-level
    global state leaking between tests."""

    impl = String
    cache_ok = False

    def __init__(self, key_provider: Callable[[], str], *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._key_provider = key_provider

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        return encrypt(value, derive_fernet_key(self._key_provider()))

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        return decrypt(value, derive_fernet_key(self._key_provider()))
