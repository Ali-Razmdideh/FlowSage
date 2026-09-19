import pytest

from flowsage_backend.config import Settings, get_settings


def test_settings_have_sane_defaults() -> None:
    settings = Settings()
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.environment == "development"


def test_settings_read_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://a:b@example.com/db")
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://a:b@example.com/db"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_placeholder_secret_rejected_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings()


def test_placeholder_secrets_allowed_in_dev() -> None:
    Settings(environment="development")  # must not raise


def test_custom_secret_allowed_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "a-real-32-byte-secret-for-production!!")
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", "a-real-encryption-passphrase-for-production")
    Settings()  # must not raise


def test_placeholder_encryption_key_rejected_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "a-real-32-byte-secret-for-production!!")
    with pytest.raises(ValueError, match="SECRET_ENCRYPTION_KEY"):
        Settings()


@pytest.mark.parametrize("field", ["jwt_secret", "secret_encryption_key"])
def test_any_development_secret_is_rejected_outside_dev(field: str) -> None:
    from flowsage_backend.config import _PLACEHOLDER_JWT_SECRET

    values = {"jwt_secret": "j" * 32, "secret_encryption_key": "e" * 32}
    values[field] = _PLACEHOLDER_JWT_SECRET
    with pytest.raises(ValueError, match=field.upper()):
        Settings(environment="production", **values)


def test_empty_string_env_var_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """docker-compose's `${STRIPE_SECRET_KEY:-}` interpolation passes an actual
    empty string, not an unset variable, whenever the host has no override. If
    that empty string were kept as-is it would defeat the `str | None = None`
    "unconfigured" defaults (see config.py's Stripe fields) and turn billing's
    clean 400 "not configured" response into a 500 from the Stripe SDK -- caught
    against a real docker-compose backend in Task 12 verification."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    settings = Settings()
    assert settings.stripe_secret_key is None


@pytest.mark.parametrize("field", ["jwt_secret", "secret_encryption_key"])
@pytest.mark.parametrize("value", ["", "short", " " * 32])
def test_production_rejects_weak_secrets(field: str, value: str) -> None:
    values = {"jwt_secret": "j" * 32, "secret_encryption_key": "e" * 32}
    values[field] = value
    with pytest.raises(ValueError, match=field.upper()):
        Settings(environment="production", **values)


def test_secret_minimum_is_measured_in_utf8_bytes() -> None:
    Settings(environment="production", jwt_secret="é" * 16, secret_encryption_key="é" * 16)
