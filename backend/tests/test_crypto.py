from __future__ import annotations

import pytest
from cryptography.fernet import InvalidToken

from flowsage_backend.crypto import decrypt, derive_fernet_key, encrypt


def test_encrypt_decrypt_round_trips() -> None:
    key = derive_fernet_key("a-passphrase-not-a-real-fernet-key")
    ciphertext = encrypt("super-secret-token", key)
    assert ciphertext != "super-secret-token"
    assert decrypt(ciphertext, key) == "super-secret-token"


def test_decrypt_rejects_tampered_ciphertext() -> None:
    key = derive_fernet_key("a-passphrase-not-a-real-fernet-key")
    ciphertext = encrypt("super-secret-token", key)
    tampered = ciphertext[:-4] + ("A" if ciphertext[-4] != "A" else "B") + ciphertext[-3:]
    with pytest.raises(InvalidToken):
        decrypt(tampered, key)


def test_decrypt_rejects_wrong_key() -> None:
    ciphertext = encrypt("super-secret-token", derive_fernet_key("key-one"))
    with pytest.raises(InvalidToken):
        decrypt(ciphertext, derive_fernet_key("key-two"))
