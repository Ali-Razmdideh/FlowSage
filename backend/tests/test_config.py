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


def test_placeholder_secrets_rejected_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(ValueError, match="JWT_SECRET, EVENTS_API_KEY"):
        Settings()


def test_one_placeholder_secret_rejected_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "a-real-32-byte-secret-for-production!!")
    with pytest.raises(ValueError, match="EVENTS_API_KEY"):
        Settings()


def test_placeholder_secrets_allowed_in_dev() -> None:
    Settings(environment="development")  # must not raise


def test_custom_secrets_allowed_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "a-real-32-byte-secret-for-production!!")
    monkeypatch.setenv("EVENTS_API_KEY", "a-real-32-byte-events-api-key-here!!")
    Settings()  # must not raise


def test_slack_jira_settings_default_to_unconfigured() -> None:
    settings = Settings()
    assert settings.slack_webhook_url is None
    assert settings.jira_base_url is None
    assert settings.jira_email is None
    assert settings.jira_api_token is None
    assert settings.jira_project_key is None


def test_slack_jira_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/x/y/z")
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token123")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "FLOW")
    settings = Settings()
    assert settings.slack_webhook_url == "https://hooks.slack.com/services/x/y/z"
    assert settings.jira_base_url == "https://example.atlassian.net"
    assert settings.jira_project_key == "FLOW"
