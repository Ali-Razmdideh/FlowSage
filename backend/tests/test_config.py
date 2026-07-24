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
