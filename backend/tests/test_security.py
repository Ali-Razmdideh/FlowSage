import uuid

import jwt
import pytest

from flowsage_backend.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_password_does_not_return_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert hashed.startswith("$argon2")


def test_verify_password_accepts_correct_password() -> None:
    hashed = hash_password("hunter2")
    assert verify_password("hunter2", hashed) is True


def test_verify_password_rejects_wrong_password() -> None:
    hashed = hash_password("hunter2")
    assert verify_password("wrong", hashed) is False


def test_verify_password_rejects_malformed_hash() -> None:
    assert verify_password("hunter2", "not-a-real-hash") is False


def test_access_token_roundtrip() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(
        user_id, secret="s3cret-test-secret-32-bytes-long!!", algorithm="HS256", expires_minutes=5
    )
    decoded = decode_access_token(
        token, secret="s3cret-test-secret-32-bytes-long!!", algorithm="HS256"
    )
    assert decoded == user_id


def test_access_token_rejects_wrong_secret() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(
        user_id, secret="s3cret-test-secret-32-bytes-long!!", algorithm="HS256", expires_minutes=5
    )
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(
            token, secret="a-completely-different-32-byte-secret", algorithm="HS256"
        )


def test_access_token_rejects_expired_token() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(
        user_id, secret="s3cret-test-secret-32-bytes-long!!", algorithm="HS256", expires_minutes=-1
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token, secret="s3cret-test-secret-32-bytes-long!!", algorithm="HS256")
