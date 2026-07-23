import uuid

import jwt
import pytest

from flowsage_backend.security import (
    create_access_token,
    decode_access_token,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_password,
)


def test_generate_api_key_has_expected_prefix_and_is_random() -> None:
    key_a = generate_api_key()
    key_b = generate_api_key()
    assert key_a.startswith("fs_live_")
    assert key_a != key_b


def test_hash_api_key_is_deterministic_and_not_reversible() -> None:
    raw = generate_api_key()
    assert hash_api_key(raw) == hash_api_key(raw)
    assert hash_api_key(raw) != raw


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


def test_create_and_decode_access_token_round_trips() -> None:
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    token = create_access_token(
        user_id, workspace_id, secret="test-secret", algorithm="HS256", expires_minutes=5
    )
    decoded_user_id, decoded_workspace_id = decode_access_token(
        token, secret="test-secret", algorithm="HS256"
    )
    assert decoded_user_id == user_id
    assert decoded_workspace_id == workspace_id


def test_access_token_rejects_wrong_secret() -> None:
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    token = create_access_token(
        user_id,
        workspace_id,
        secret="s3cret-test-secret-32-bytes-long!!",
        algorithm="HS256",
        expires_minutes=5,
    )
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(
            token, secret="a-completely-different-32-byte-secret", algorithm="HS256"
        )


def test_access_token_rejects_expired_token() -> None:
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    token = create_access_token(
        user_id,
        workspace_id,
        secret="s3cret-test-secret-32-bytes-long!!",
        algorithm="HS256",
        expires_minutes=-1,
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token, secret="s3cret-test-secret-32-bytes-long!!", algorithm="HS256")
