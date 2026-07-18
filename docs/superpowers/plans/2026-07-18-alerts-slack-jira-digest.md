# Phase 2 Chunk 3: Trend Alerts + Slack/Jira Export + Weekly Digest — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fixed-threshold trend alerts (reusing calibration/churn thresholds), Slack/Jira export buttons on predicted friction issues and observational friction nodes, and a weekly Slack digest job — all backed by env-var-only Slack/Jira config, with mocked-HTTP test coverage (no live external creds available).

**Architecture:** New compute-on-demand `alerts.py` (same pattern as `calibration.py`/`churn.py` — no new DB tables) reuses the existing calibration delta threshold and churn risk threshold as alert triggers. New `integrations/slack.py` and `integrations/jira.py` are thin `httpx` clients with a `transport` parameter for test injection (mirrors the `ASGITransport` idiom this test suite already uses). Three new/extended routers expose: `GET /alerts` + `POST /alerts/digest/run`, `POST /friction-issues/{id}/export/{slack,jira}`, and `POST /graph/nodes/{screen}/export/{slack,jira}`. An arq `cron_job` in `worker.py` runs the same digest-building code weekly.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic v2, httpx (moved from dev to main deps), arq cron, React 19 + TypeScript strict, Vitest.

## Global Constraints

- No new Alembic migration — nothing in this chunk is persisted (per the approved spec, `docs/superpowers/specs/2026-07-18-alerts-slack-jira-design.md`).
- Slack/Jira credentials: optional `Settings` fields, no placeholder-secret startup guard (unlike `JWT_SECRET`/`EVENTS_API_KEY` — this feature must work unconfigured, returning a clean 400 rather than failing startup).
- No live Slack/Jira network calls anywhere in this plan's test suite — all client tests use `httpx.MockTransport`.
- mypy `--strict` on all new/modified Python; TypeScript `strict: true` on all new/modified frontend code.
- Run `autoflake8` then `black` on touched Python files before the final commit.
- One commit at the end of this plan (after the full review + `docker-compose` verification pass), pushed to `main` — matching this repo's established per-chunk convention (see `c553837`, `f03eaa3`), not a commit-per-task. Individual tasks below end with a local test-passing checkpoint, not a commit.

---

## File Structure

```
backend/src/flowsage_backend/
  config.py                  MODIFY: add optional slack/jira Settings fields
  alerts.py                  CREATE: fixed-threshold alert checks + digest content builders
  integrations/
    __init__.py               CREATE: empty
    slack.py                  CREATE: post_slack_message (httpx, MockTransport-testable)
    jira.py                   CREATE: create_jira_issue (httpx, MockTransport-testable)
  api/
    alerts.py                  CREATE: GET /alerts, POST /alerts/digest/run
    exports.py                 CREATE: POST /friction-issues/{id}/export/{slack,jira}
    events.py                  MODIFY: add POST /graph/nodes/{screen}/export/{slack,jira}
  main.py                     MODIFY: register alerts_router, exports_router
  worker.py                   MODIFY: add run_weekly_digest_job + arq cron_job

backend/tests/
  test_config.py               MODIFY: cover new optional fields' defaults
  test_integrations_slack.py   CREATE
  test_integrations_jira.py    CREATE
  test_alerts.py               CREATE (pure-function unit tests)
  test_alerts_api.py           CREATE
  test_exports_api.py          CREATE
  test_node_export_api.py      CREATE
  test_worker.py                CREATE (digest job, mocked Slack)

backend/pyproject.toml        MODIFY: move httpx to main dependencies

frontend/src/
  lib/types.ts                MODIFY: add AlertsReport/CalibrationAlert/ChurnAlert/
                               SlackExportResult/JiraExportResult
  lib/api.ts                  MODIFY: add getAlerts, exportIssueToSlack/Jira,
                               exportNodeToSlack/Jira
  routes/predictive/RunningSimulationPage.tsx  MODIFY: Export buttons on FrictionIssueCard
  routes/predictive/RunningSimulationPage.test.tsx  CREATE
  routes/journey/JourneyGraphPage.tsx          MODIFY: Export buttons on NodeIntelligenceAside
  routes/journey/JourneyGraphPage.test.tsx     MODIFY: cover export buttons
  routes/DashboardPage.tsx                     MODIFY: alerts banner
  routes/DashboardPage.test.tsx                CREATE
```

---

## Task 1: Settings — optional Slack/Jira config + httpx as a main dependency

**Files:**
- Modify: `backend/src/flowsage_backend/config.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.slack_webhook_url: str | None`, `Settings.jira_base_url: str | None`, `Settings.jira_email: str | None`, `Settings.jira_api_token: str | None`, `Settings.jira_project_key: str | None` — all default `None`, read from env vars `SLACK_WEBHOOK_URL` / `JIRA_BASE_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` / `JIRA_PROJECT_KEY` (pydantic-settings' default env-name-from-field-name behavior, same as every other `Settings` field).

- [ ] **Step 1: Add the fields to `Settings`**

Edit `backend/src/flowsage_backend/config.py`, insert after the `events_api_key` field (before the `@model_validator`):

```python
    # Alert export integrations (Phase 2 chunk 3). Optional -- unlike JWT_SECRET/
    # EVENTS_API_KEY, exports are meant to work unconfigured: callers get a clean
    # "not configured" error from flowsage_backend.integrations, not a startup
    # failure. Per-workspace Integration rows + a settings UI are Phase 3 scope.
    slack_webhook_url: str | None = None
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None
    jira_project_key: str | None = None
```

- [ ] **Step 2: Add a test for the defaults**

Append to `backend/tests/test_config.py`:

```python
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
```

- [ ] **Step 3: Run the config tests**

Run: `cd backend && uv run pytest tests/test_config.py -v`
Expected: all `test_config.py` tests PASS, including the two new ones (no live infra needed — this file has no `app`/`db_session` fixtures).

- [ ] **Step 4: Move httpx to main dependencies**

Edit `backend/pyproject.toml`. In the `[project] dependencies` list, add `"httpx>=0.27,<0.28",` (place it alphabetically-ish near the other HTTP-adjacent deps, e.g. after `"python-multipart>=0.0.12,<0.1",`). In `[dependency-groups] dev`, remove the now-redundant `"httpx>=0.27,<0.28",` line (it stays available transitively as a main dependency; tests already `import httpx`/`from httpx import ...` so nothing else changes).

- [ ] **Step 5: Re-sync the workspace venv**

Run: `cd /home/asus/Projects/personal/FlowSage && uv sync --all-extras`
Expected: exits 0. (Run from the repo root — running `uv sync` from inside `backend/` prunes the other workspace members' deps out of the shared venv, per this repo's existing gotcha.)

---

## Task 2: `integrations/slack.py` — Slack webhook client

**Files:**
- Create: `backend/src/flowsage_backend/integrations/__init__.py`
- Create: `backend/src/flowsage_backend/integrations/slack.py`
- Test: `backend/tests/test_integrations_slack.py`

**Interfaces:**
- Produces: `SlackNotConfiguredError(Exception)`, `SlackDeliveryError(Exception)`,
  `async def post_slack_message(webhook_url: str | None, *, text: str, blocks: list[dict[str, object]] | None = None, transport: httpx.BaseTransport | None = None) -> None`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_integrations_slack.py`:

```python
import httpx
import pytest

from flowsage_backend.integrations.slack import (
    SlackDeliveryError,
    SlackNotConfiguredError,
    post_slack_message,
)


async def test_post_slack_message_raises_when_not_configured() -> None:
    with pytest.raises(SlackNotConfiguredError):
        await post_slack_message(None, text="hello")


async def test_post_slack_message_posts_expected_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = httpx.Request.read.__get__(request)() and request.content
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    await post_slack_message(
        "https://hooks.slack.com/services/x/y/z",
        text="fallback text",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}],
        transport=transport,
    )

    assert captured["url"] == "https://hooks.slack.com/services/x/y/z"


async def test_post_slack_message_raises_on_non_200() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(400, text="invalid_payload"))
    with pytest.raises(SlackDeliveryError, match="400"):
        await post_slack_message(
            "https://hooks.slack.com/services/x/y/z", text="hi", transport=transport
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_integrations_slack.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flowsage_backend.integrations'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/flowsage_backend/integrations/__init__.py` (empty file).

Create `backend/src/flowsage_backend/integrations/slack.py`:

```python
"""Slack webhook client. A webhook is a single POST -- no SDK needed. The
`transport` parameter exists purely for tests (`httpx.MockTransport`), mirroring
the `ASGITransport` idiom this codebase's own API tests already use."""

from __future__ import annotations

import httpx


class SlackNotConfiguredError(Exception):
    """Raised when no Slack webhook URL is configured."""


class SlackDeliveryError(Exception):
    """Raised when Slack rejects the webhook POST."""


async def post_slack_message(
    webhook_url: str | None,
    *,
    text: str,
    blocks: list[dict[str, object]] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> None:
    if webhook_url is None:
        raise SlackNotConfiguredError("SLACK_WEBHOOK_URL is not configured")

    payload: dict[str, object] = {"text": text}
    if blocks is not None:
        payload["blocks"] = blocks

    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post(webhook_url, json=payload)

    if response.status_code != 200:
        raise SlackDeliveryError(f"Slack webhook returned {response.status_code}: {response.text}")
```

- [ ] **Step 4: Fix the test's payload assertion (simplify from Step 1's placeholder)**

Replace the body of `test_post_slack_message_posts_expected_payload` in `backend/tests/test_integrations_slack.py` with a correct, non-placeholder assertion:

```python
async def test_post_slack_message_posts_expected_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    await post_slack_message(
        "https://hooks.slack.com/services/x/y/z",
        text="fallback text",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}],
        transport=transport,
    )

    assert captured["url"] == "https://hooks.slack.com/services/x/y/z"
    assert b"fallback text" in captured["body"]  # type: ignore[operator]
    assert b"section" in captured["body"]  # type: ignore[operator]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_integrations_slack.py -v`
Expected: 3 PASS

- [ ] **Step 6: mypy check**

Run: `cd backend && uv run mypy --strict src/flowsage_backend/integrations/slack.py`
Expected: `Success: no issues found`

---

## Task 3: `integrations/jira.py` — Jira issue-creation client

**Files:**
- Create: `backend/src/flowsage_backend/integrations/jira.py`
- Test: `backend/tests/test_integrations_jira.py`

**Interfaces:**
- Consumes: nothing from Task 2 (parallel client, same pattern).
- Produces: `JiraNotConfiguredError(Exception)`, `JiraDeliveryError(Exception)`,
  `async def create_jira_issue(*, base_url: str | None, email: str | None, api_token: str | None, project_key: str | None, summary: str, description: str, transport: httpx.BaseTransport | None = None) -> str` (returns the created issue key, e.g. `"FLOW-123"`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_integrations_jira.py`:

```python
import httpx
import pytest

from flowsage_backend.integrations.jira import (
    JiraDeliveryError,
    JiraNotConfiguredError,
    create_jira_issue,
)


async def test_create_jira_issue_raises_when_not_configured() -> None:
    with pytest.raises(JiraNotConfiguredError):
        await create_jira_issue(
            base_url=None,
            email=None,
            api_token=None,
            project_key=None,
            summary="s",
            description="d",
        )


async def test_create_jira_issue_raises_when_partially_configured() -> None:
    with pytest.raises(JiraNotConfiguredError):
        await create_jira_issue(
            base_url="https://example.atlassian.net",
            email="bot@example.com",
            api_token=None,
            project_key="FLOW",
            summary="s",
            description="d",
        )


async def test_create_jira_issue_posts_and_returns_key() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(201, json={"key": "FLOW-42"})

    transport = httpx.MockTransport(handler)
    key = await create_jira_issue(
        base_url="https://example.atlassian.net",
        email="bot@example.com",
        api_token="token123",
        project_key="FLOW",
        summary="Checkout drop-off",
        description="42% drop-off at checkout.",
        transport=transport,
    )

    assert key == "FLOW-42"
    assert captured["url"] == "https://example.atlassian.net/rest/api/3/issue"
    assert b"Checkout drop-off" in captured["body"]  # type: ignore[operator]
    assert b"FLOW" in captured["body"]  # type: ignore[operator]


async def test_create_jira_issue_raises_on_non_201() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(400, text="bad request"))
    with pytest.raises(JiraDeliveryError, match="400"):
        await create_jira_issue(
            base_url="https://example.atlassian.net",
            email="bot@example.com",
            api_token="token123",
            project_key="FLOW",
            summary="s",
            description="d",
            transport=transport,
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_integrations_jira.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flowsage_backend.integrations.jira'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/flowsage_backend/integrations/jira.py`:

```python
"""Jira Cloud REST client: creates a single issue via `/rest/api/3/issue`. Auth
is HTTP Basic with an email + API token, per Jira Cloud's documented scheme."""

from __future__ import annotations

import httpx


class JiraNotConfiguredError(Exception):
    """Raised when base_url/email/api_token/project_key aren't all set."""


class JiraDeliveryError(Exception):
    """Raised when Jira rejects the issue-creation POST."""


async def create_jira_issue(
    *,
    base_url: str | None,
    email: str | None,
    api_token: str | None,
    project_key: str | None,
    summary: str,
    description: str,
    transport: httpx.BaseTransport | None = None,
) -> str:
    if not (base_url and email and api_token and project_key):
        raise JiraNotConfiguredError(
            "Jira is not fully configured (need JIRA_BASE_URL, JIRA_EMAIL, "
            "JIRA_API_TOKEN, JIRA_PROJECT_KEY)"
        )

    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": description}]}
                ],
            },
            "issuetype": {"name": "Bug"},
        }
    }

    async with httpx.AsyncClient(
        transport=transport, auth=httpx.BasicAuth(email, api_token)
    ) as client:
        response = await client.post(f"{base_url}/rest/api/3/issue", json=payload)

    if response.status_code != 201:
        raise JiraDeliveryError(f"Jira issue creation returned {response.status_code}: {response.text}")

    key = response.json()["key"]
    assert isinstance(key, str)
    return key
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_integrations_jira.py -v`
Expected: 4 PASS

- [ ] **Step 5: mypy check**

Run: `cd backend && uv run mypy --strict src/flowsage_backend/integrations/jira.py`
Expected: `Success: no issues found`

---

## Task 4: `alerts.py` — fixed-threshold trend alerts + digest content

**Files:**
- Create: `backend/src/flowsage_backend/alerts.py`
- Test: `backend/tests/test_alerts.py`

**Interfaces:**
- Consumes: `flowsage_backend.calibration.CalibrationReport` / `.PersonaCalibration` / `.ScreenCalibration` / `.build_calibration_report` (Task-independent, already exists); `flowsage_backend.churn.ChurnRiskSegment` / `.build_churn_risk_segments` (already exists); `flowsage_backend.events.query_events` (already exists).
- Produces: `CalibrationAlert(BaseModel)` `{persona_name: str, screen: str, delta: float}`, `ChurnAlert(BaseModel)` `{cohort: str, risk_score: float, top_reason: str}`, `AlertsReport(BaseModel)` `{calibration_alerts: list[CalibrationAlert], churn_alerts: list[ChurnAlert]}`, `check_calibration_anomalies(report: CalibrationReport) -> list[CalibrationAlert]`, `check_churn_alerts(segments: list[ChurnRiskSegment]) -> list[ChurnAlert]`, `async def build_alerts_report(session: AsyncSession) -> AlertsReport`, `has_alerts(report: AlertsReport) -> bool`, `build_digest_text(report: AlertsReport) -> str`, `build_digest_blocks(report: AlertsReport) -> list[dict[str, object]]`.

  Note: `has_alerts` is a plain function, not a Pydantic `@property`/computed field — this repo already hit the bug where a plain `@property` on a Pydantic model silently doesn't serialize (see `flowsage_graph.models.FunnelStep.drop_off_rate`, fixed with `@computed_field`). Keeping `AlertsReport` to two list fields and computing `has_alerts` externally sidesteps that class of bug entirely.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_alerts.py` (pure-function tests, same style as `test_churn.py` — no DB/app fixtures needed for these):

```python
from flowsage_backend.alerts import (
    AlertsReport,
    CalibrationAlert,
    ChurnAlert,
    build_digest_blocks,
    build_digest_text,
    check_calibration_anomalies,
    check_churn_alerts,
    has_alerts,
)
from flowsage_backend.calibration import CalibrationReport, PersonaCalibration, ScreenCalibration
from flowsage_backend.churn import ChurnRiskSegment


def test_check_calibration_anomalies_returns_only_flagged_screens() -> None:
    report = CalibrationReport(
        personas=[
            PersonaCalibration(
                persona_id="p1",
                persona_name="Novice Nora",
                run_id="r1",
                screens=[
                    ScreenCalibration(
                        screen="checkout",
                        predicted_score=0.2,
                        observed_score=0.9,
                        delta=0.7,
                        anomaly=True,
                    ),
                    ScreenCalibration(
                        screen="landing",
                        predicted_score=0.2,
                        observed_score=0.25,
                        delta=0.05,
                        anomaly=False,
                    ),
                ],
            )
        ],
        accuracy_points=[],
        has_anomaly=True,
    )

    alerts = check_calibration_anomalies(report)

    assert len(alerts) == 1
    assert alerts[0].screen == "checkout"
    assert alerts[0].persona_name == "Novice Nora"


def test_check_churn_alerts_filters_by_threshold() -> None:
    segments = [
        ChurnRiskSegment(cohort="at_risk", risk_score=0.72, sessions_at_risk=5, top_reason="x"),
        ChurnRiskSegment(cohort="healthy", risk_score=0.1, sessions_at_risk=0, top_reason="y"),
    ]

    alerts = check_churn_alerts(segments)

    assert len(alerts) == 1
    assert alerts[0].cohort == "at_risk"


def test_has_alerts_true_when_either_list_nonempty() -> None:
    empty = AlertsReport(calibration_alerts=[], churn_alerts=[])
    assert has_alerts(empty) is False

    with_churn = AlertsReport(
        calibration_alerts=[],
        churn_alerts=[ChurnAlert(cohort="c", risk_score=0.9, top_reason="r")],
    )
    assert has_alerts(with_churn) is True


def test_build_digest_text_no_alerts() -> None:
    report = AlertsReport(calibration_alerts=[], churn_alerts=[])
    text = build_digest_text(report)
    assert "no calibration or churn alerts" in text.lower()


def test_build_digest_blocks_includes_a_block_per_alert() -> None:
    report = AlertsReport(
        calibration_alerts=[CalibrationAlert(persona_name="Nora", screen="checkout", delta=0.7)],
        churn_alerts=[ChurnAlert(cohort="at_risk", risk_score=0.72, top_reason="drop-off")],
    )

    blocks = build_digest_blocks(report)

    joined = " ".join(str(b) for b in blocks)
    assert "checkout" in joined
    assert "at_risk" in joined
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_alerts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flowsage_backend.alerts'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/flowsage_backend/alerts.py`:

```python
"""Trend/alert checks reused by the dashboard banner, the weekly digest, and
(indirectly, via the same threshold definitions) the export buttons' context.
Deliberately reuses the existing calibration delta threshold
(`calibration.ANOMALY_THRESHOLD`) and a fixed churn-risk threshold rather than
introducing a configurable `AlertRule` table -- there's a single definition of
"anomalous" across the app, and no rule-config UI was scoped for this chunk.

Like `calibration.py`/`churn.py`, everything here is computed on demand from
current data -- no persisted "alert" rows.
"""

from __future__ import annotations

from flowsage_graph.funnel import discover_funnel
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.calibration import CalibrationReport, build_calibration_report
from flowsage_backend.churn import ChurnRiskSegment, build_churn_risk_segments
from flowsage_backend.events import query_events

CHURN_RISK_ALERT_THRESHOLD = 0.5
"""A churn-risk segment at or above this score is alert-worthy. Matches the
"at_risk"-vs-"healthy" fixture shape used across the existing churn tests --
comfortably above normal variance, below the churn tests' own worst-case
(~0.72 for a cohort with heavy drop-off and friction)."""


class CalibrationAlert(BaseModel):
    persona_name: str
    screen: str
    delta: float


class ChurnAlert(BaseModel):
    cohort: str
    risk_score: float
    top_reason: str


class AlertsReport(BaseModel):
    calibration_alerts: list[CalibrationAlert]
    churn_alerts: list[ChurnAlert]


def has_alerts(report: AlertsReport) -> bool:
    return bool(report.calibration_alerts or report.churn_alerts)


def check_calibration_anomalies(report: CalibrationReport) -> list[CalibrationAlert]:
    return [
        CalibrationAlert(persona_name=persona.persona_name, screen=screen.screen, delta=screen.delta)
        for persona in report.personas
        for screen in persona.screens
        if screen.anomaly
    ]


def check_churn_alerts(segments: list[ChurnRiskSegment]) -> list[ChurnAlert]:
    return [
        ChurnAlert(cohort=s.cohort, risk_score=s.risk_score, top_reason=s.top_reason)
        for s in segments
        if s.risk_score >= CHURN_RISK_ALERT_THRESHOLD
    ]


async def build_alerts_report(session: AsyncSession) -> AlertsReport:
    events = await query_events(session)
    funnel = discover_funnel(events)
    calibration_report = await build_calibration_report(session, funnel)
    churn_segments = await build_churn_risk_segments(session)
    return AlertsReport(
        calibration_alerts=check_calibration_anomalies(calibration_report),
        churn_alerts=check_churn_alerts(churn_segments),
    )


def build_digest_text(report: AlertsReport) -> str:
    """Plain-text fallback for Slack's top-level `text` field (used in
    notification previews; `build_digest_blocks` is the rendered body)."""
    if not has_alerts(report):
        return "FlowSage Weekly Digest: no calibration or churn alerts this week."
    parts = [
        f"{len(report.calibration_alerts)} calibration anomalies",
        f"{len(report.churn_alerts)} churn-risk segments",
    ]
    return "FlowSage Weekly Digest: " + ", ".join(parts)


def build_digest_blocks(report: AlertsReport) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "FlowSage Weekly Digest"}},
    ]
    if not has_alerts(report):
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "No calibration or churn alerts this week."},
            }
        )
        return blocks

    for alert in report.calibration_alerts:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Calibration anomaly*: {alert.persona_name} on `{alert.screen}` "
                        f"(delta {alert.delta:+.2f})"
                    ),
                },
            }
        )
    for alert in report.churn_alerts:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Churn risk*: {alert.cohort} at {alert.risk_score * 100:.0f}% "
                        f"-- {alert.top_reason}"
                    ),
                },
            }
        )
    return blocks
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_alerts.py -v`
Expected: 5 PASS

- [ ] **Step 5: mypy check**

Run: `cd backend && uv run mypy --strict src/flowsage_backend/alerts.py`
Expected: `Success: no issues found`

---

## Task 5: `api/alerts.py` — `GET /alerts`, `POST /alerts/digest/run`

**Files:**
- Create: `backend/src/flowsage_backend/api/alerts.py`
- Modify: `backend/src/flowsage_backend/main.py`
- Test: `backend/tests/test_alerts_api.py`

**Interfaces:**
- Consumes: `flowsage_backend.alerts.{AlertsReport, build_alerts_report, build_digest_text, build_digest_blocks}` (Task 4); `flowsage_backend.integrations.slack.{post_slack_message, SlackNotConfiguredError, SlackDeliveryError}` (Task 2); `flowsage_backend.deps.{get_current_user, get_db_session}` (existing).
- Produces: `alerts_router: APIRouter` (importable as `from flowsage_backend.api.alerts import router as alerts_router`), `DigestResult(BaseModel)` `{status: str}`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_alerts_api.py`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.event import Event
from flowsage_backend.seed import upsert_user

_T0 = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def _event(session_id: str, screen: str, minutes: int, cohort: str) -> dict[str, str]:
    return {
        "session_id": session_id,
        "screen": screen,
        "event": "screen_view",
        "timestamp": (_T0 + timedelta(minutes=minutes)).isoformat(),
        "device": "mobile",
        "cohort": cohort,
    }


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, "alerts-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "alerts-api@example.com", "password": "hunter2"}
        )
        yield client


async def test_get_alerts_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/alerts")

    assert response.status_code == 401


async def test_get_alerts_flags_a_churn_risk_segment(app: FastAPI, db_session: AsyncSession) -> None:
    api_key = app.state.settings.events_api_key
    session_ids = [f"alerts-api-{i}" for i in range(4)]
    events = [
        *[_event(session_ids[i], "landing", 0, "at_risk_alerts") for i in range(4)],
        _event(session_ids[0], "checkout", 1, "at_risk_alerts"),
    ]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ingest_response = await client.post(
                "/v1/events", json=events, headers={"X-API-Key": api_key}
            )
            assert ingest_response.status_code == 201

        async with _authed_client(app, db_session) as client:
            response = await client.get("/alerts")

        assert response.status_code == 200
        body = response.json()
        cohorts = {a["cohort"] for a in body["churn_alerts"]}
        assert "at_risk_alerts" in cohorts
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()


async def test_digest_run_returns_400_when_slack_not_configured(
    app: FastAPI, db_session: AsyncSession
) -> None:
    assert app.state.settings.slack_webhook_url is None
    async with _authed_client(app, db_session) as client:
        response = await client.post("/alerts/digest/run")

    assert response.status_code == 400
    assert "not configured" in response.json()["detail"].lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_alerts_api.py -v`
Expected: FAIL — `404 Not Found` for `/alerts` (route doesn't exist yet), and a collection error is fine too if the module can't be found; either way, not the expected 401/200/400.

- [ ] **Step 3: Write the implementation**

Create `backend/src/flowsage_backend/api/alerts.py`:

```python
"""Trend alert summary (for the dashboard banner) and a manually-triggerable
weekly digest send -- the same digest content `worker.py`'s arq cron job posts
on schedule, exposed here so it can be tested/fired without waiting a week."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.alerts import AlertsReport, build_alerts_report, build_digest_blocks, build_digest_text
from flowsage_backend.deps import get_current_user, get_db_session
from flowsage_backend.integrations.slack import (
    SlackDeliveryError,
    SlackNotConfiguredError,
    post_slack_message,
)

router = APIRouter(prefix="/alerts", tags=["alerts"], dependencies=[Depends(get_current_user)])


class DigestResult(BaseModel):
    status: str = "sent"


@router.get("", response_model=AlertsReport)
async def get_alerts(session: AsyncSession = Depends(get_db_session)) -> AlertsReport:
    return await build_alerts_report(session)


@router.post("/digest/run", response_model=DigestResult)
async def run_digest_now(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> DigestResult:
    settings = request.app.state.settings
    report = await build_alerts_report(session)
    try:
        await post_slack_message(
            settings.slack_webhook_url,
            text=build_digest_text(report),
            blocks=build_digest_blocks(report),
        )
    except SlackNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SlackDeliveryError as exc:
        raise HTTPException(502, str(exc)) from exc
    return DigestResult()
```

- [ ] **Step 4: Register the router in `main.py`**

Edit `backend/src/flowsage_backend/main.py`. Add the import alongside the other routers:

```python
from flowsage_backend.api.alerts import router as alerts_router
```

And add `app.include_router(alerts_router)` alongside the other `include_router` calls (after `app.include_router(calibration_router)`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_alerts_api.py -v`
Expected: 3 PASS

- [ ] **Step 6: Full backend test suite + mypy**

Run: `cd backend && uv run pytest -q && uv run mypy --strict src`
Expected: all tests PASS, `Success: no issues found in N source files`

---

## Task 6: `api/exports.py` — friction-issue Slack/Jira export endpoints

**Files:**
- Create: `backend/src/flowsage_backend/api/exports.py`
- Modify: `backend/src/flowsage_backend/main.py`
- Test: `backend/tests/test_exports_api.py`

**Interfaces:**
- Consumes: `flowsage_backend.integrations.slack.*` (Task 2), `flowsage_backend.integrations.jira.*` (Task 3), `flowsage_backend.models.simulation.FrictionIssue` (existing).
- Produces: `exports_router: APIRouter` (`from flowsage_backend.api.exports import router as exports_router`), `SlackExportResult(BaseModel)` `{status: str}`, `JiraExportResult(BaseModel)` `{issue_key: str}`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_exports_api.py`. This needs a real `FrictionIssue` row, which requires a `SimulationRun` + `Persona` (namespaced per this suite's session-scoped-DB gotcha, same as `test_calibration_api.py`):

```python
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.persona import Persona
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from flowsage_backend.seed import upsert_user


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, "exports-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "exports-api@example.com", "password": "hunter2"}
        )
        yield client


async def _make_issue(db_session: AsyncSession) -> uuid.UUID:
    persona = Persona(
        slug=f"exports-persona-{uuid.uuid4().hex[:8]}",
        name="Exports Test Persona",
        description="d",
        baseline=False,
        tech_affinity="medium",
        primary_device="desktop",
        discovery_mode="search",
        contextual_triggers=[],
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    db_session.add(persona)
    await db_session.flush()

    run = SimulationRun(
        flow_name="checkout",
        goal="buy",
        persona_id=persona.id,
        screenshots_dir="/tmp/x",
        status=RunStatus.COMPLETED,
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()

    issue = FrictionIssue(
        run_id=run.id,
        screen="checkout",
        severity="high",
        title="Confusing CTA",
        heuristic_violated="Visibility of system status",
        persona_impact="Anxious users abandon.",
        description="The primary button is unlabeled.",
        suggested_fix="Add a clear label.",
    )
    db_session.add(issue)
    await db_session.commit()
    return issue.id


async def test_export_issue_to_slack_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/friction-issues/{uuid.uuid4()}/export/slack")

    assert response.status_code == 401


async def test_export_issue_to_slack_returns_404_for_unknown_issue(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.post(f"/friction-issues/{uuid.uuid4()}/export/slack")

    assert response.status_code == 404


async def test_export_issue_to_slack_returns_400_when_not_configured(
    app: FastAPI, db_session: AsyncSession
) -> None:
    issue_id = await _make_issue(db_session)
    assert app.state.settings.slack_webhook_url is None

    async with _authed_client(app, db_session) as client:
        response = await client.post(f"/friction-issues/{issue_id}/export/slack")

    assert response.status_code == 400


async def test_export_issue_to_jira_returns_400_when_not_configured(
    app: FastAPI, db_session: AsyncSession
) -> None:
    issue_id = await _make_issue(db_session)

    async with _authed_client(app, db_session) as client:
        response = await client.post(f"/friction-issues/{issue_id}/export/jira")

    assert response.status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_exports_api.py -v`
Expected: FAIL (404 route-not-found for all, since neither the module nor the routes exist yet)

- [ ] **Step 3: Write the implementation**

Create `backend/src/flowsage_backend/api/exports.py`:

```python
"""Export actions for predicted `FrictionIssue` rows: the "Export to
Engineering Ticket"/"Export to Jira" buttons on the Predictive Engine's
friction report. Kept separate from `api/events.py`'s node-export endpoints
(Task 7) because these operate on a `SimulationRun`'s `FrictionIssue` id -- a
different lookup and domain object than an observational graph screen."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_user, get_db_session
from flowsage_backend.integrations.jira import (
    JiraDeliveryError,
    JiraNotConfiguredError,
    create_jira_issue,
)
from flowsage_backend.integrations.slack import (
    SlackDeliveryError,
    SlackNotConfiguredError,
    post_slack_message,
)
from flowsage_backend.models.simulation import FrictionIssue

router = APIRouter(
    prefix="/friction-issues", tags=["exports"], dependencies=[Depends(get_current_user)]
)


class SlackExportResult(BaseModel):
    status: str = "sent"


class JiraExportResult(BaseModel):
    issue_key: str


async def _get_issue(session: AsyncSession, issue_id: uuid.UUID) -> FrictionIssue:
    issue = await session.get(FrictionIssue, issue_id)
    if issue is None:
        raise HTTPException(404, "Friction issue not found")
    return issue


@router.post("/{issue_id}/export/slack", response_model=SlackExportResult)
async def export_issue_to_slack(
    issue_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_db_session)
) -> SlackExportResult:
    issue = await _get_issue(session, issue_id)
    settings = request.app.state.settings
    text = f"*{issue.severity.upper()}* friction on `{issue.screen}`: {issue.title}"
    try:
        await post_slack_message(settings.slack_webhook_url, text=text)
    except SlackNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SlackDeliveryError as exc:
        raise HTTPException(502, str(exc)) from exc
    return SlackExportResult()


@router.post("/{issue_id}/export/jira", response_model=JiraExportResult)
async def export_issue_to_jira(
    issue_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_db_session)
) -> JiraExportResult:
    issue = await _get_issue(session, issue_id)
    settings = request.app.state.settings
    try:
        issue_key = await create_jira_issue(
            base_url=settings.jira_base_url,
            email=settings.jira_email,
            api_token=settings.jira_api_token,
            project_key=settings.jira_project_key,
            summary=f"[FlowSage] {issue.title}",
            description=f"{issue.description}\n\nSuggested fix: {issue.suggested_fix}",
        )
    except JiraNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc
    except JiraDeliveryError as exc:
        raise HTTPException(502, str(exc)) from exc
    return JiraExportResult(issue_key=issue_key)
```

- [ ] **Step 4: Register the router in `main.py`**

Edit `backend/src/flowsage_backend/main.py`. Add the import:

```python
from flowsage_backend.api.exports import router as exports_router
```

And `app.include_router(exports_router)` alongside the others.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_exports_api.py -v`
Expected: 4 PASS

- [ ] **Step 6: mypy check**

Run: `cd backend && uv run mypy --strict src/flowsage_backend/api/exports.py`
Expected: `Success: no issues found`

---

## Task 7: `api/events.py` — node-level Slack/Jira export endpoints

**Files:**
- Modify: `backend/src/flowsage_backend/api/events.py`
- Test: `backend/tests/test_node_export_api.py`

**Interfaces:**
- Consumes: `flowsage_backend.churn.get_node_intelligence` (existing), `flowsage_backend.integrations.slack.*` / `.jira.*` (Tasks 2-3).
- Produces: two new routes on the existing `graph_router`: `POST /graph/nodes/{screen}/export/slack`, `POST /graph/nodes/{screen}/export/jira`. Reuses `SlackExportResult`/`JiraExportResult` shapes defined locally in this file (small, deliberate duplication of Task 6's tiny DTOs rather than a shared module — same call the codebase already makes for `FrictionIssueOut` vs. `calibration.py`'s own issue-shaped models).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_node_export_api.py`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.event import Event
from flowsage_backend.seed import upsert_user

_T0 = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def _event(session_id: str, screen: str, minutes: int) -> dict[str, str]:
    return {
        "session_id": session_id,
        "screen": screen,
        "event": "screen_view",
        "timestamp": (_T0 + timedelta(minutes=minutes)).isoformat(),
        "device": "mobile",
        "cohort": "node-export",
    }


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, "node-export-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "node-export-api@example.com", "password": "hunter2"}
        )
        yield client


async def test_export_node_to_slack_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/graph/nodes/checkout/export/slack")

    assert response.status_code == 401


async def test_export_node_to_slack_returns_404_for_unknown_screen(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.post("/graph/nodes/nonexistent_screen_xyz/export/slack")

    assert response.status_code == 404


async def test_export_node_to_slack_returns_400_when_not_configured(
    app: FastAPI, db_session: AsyncSession
) -> None:
    api_key = app.state.settings.events_api_key
    session_ids = [f"node-export-{i}" for i in range(4)]
    events = [
        *[_event(session_ids[i], "landing", 0) for i in range(4)],
        *[_event(session_ids[i], "checkout", 1) for i in range(4)],
    ]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ingest_response = await client.post(
                "/v1/events", json=events, headers={"X-API-Key": api_key}
            )
            assert ingest_response.status_code == 201

        async with _authed_client(app, db_session) as client:
            response = await client.post("/graph/nodes/checkout/export/slack")

        assert response.status_code == 400
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()


async def test_export_node_to_jira_returns_400_when_not_configured(
    app: FastAPI, db_session: AsyncSession
) -> None:
    api_key = app.state.settings.events_api_key
    session_ids = [f"node-export-jira-{i}" for i in range(4)]
    events = [
        *[_event(session_ids[i], "landing", 0) for i in range(4)],
        *[_event(session_ids[i], "checkout", 1) for i in range(4)],
    ]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ingest_response = await client.post(
                "/v1/events", json=events, headers={"X-API-Key": api_key}
            )
            assert ingest_response.status_code == 201

        async with _authed_client(app, db_session) as client:
            response = await client.post("/graph/nodes/checkout/export/jira")

        assert response.status_code == 400
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_node_export_api.py -v`
Expected: FAIL (404 route-not-found for the export paths)

- [ ] **Step 3: Write the implementation**

Edit `backend/src/flowsage_backend/api/events.py`. Add to the imports at the top:

```python
from pydantic import BaseModel
```
(already imported — no change needed there.) Add these imports alongside the existing `flowsage_backend.churn` import block:

```python
from flowsage_backend.integrations.jira import (
    JiraDeliveryError,
    JiraNotConfiguredError,
    create_jira_issue,
)
from flowsage_backend.integrations.slack import (
    SlackDeliveryError,
    SlackNotConfiguredError,
    post_slack_message,
)
```

Add these new models and routes at the end of the file, after the existing `node_intelligence` route:

```python
class SlackExportResult(BaseModel):
    status: str = "sent"


class JiraExportResult(BaseModel):
    issue_key: str


@graph_router.post("/nodes/{screen}/export/slack", response_model=SlackExportResult)
async def export_node_to_slack(
    screen: str,
    request: Request,
    cohort: str | None = Query(default=None),
    device: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> SlackExportResult:
    intel = await get_node_intelligence(session, screen, cohort=cohort, device=device, since=since)
    if intel is None:
        raise HTTPException(status_code=404, detail=f"No funnel data for screen '{screen}'")

    settings = request.app.state.settings
    text = f"Friction node `{screen}`: {intel.ai_insight}"
    try:
        await post_slack_message(settings.slack_webhook_url, text=text)
    except SlackNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SlackDeliveryError as exc:
        raise HTTPException(502, str(exc)) from exc
    return SlackExportResult()


@graph_router.post("/nodes/{screen}/export/jira", response_model=JiraExportResult)
async def export_node_to_jira(
    screen: str,
    request: Request,
    cohort: str | None = Query(default=None),
    device: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> JiraExportResult:
    intel = await get_node_intelligence(session, screen, cohort=cohort, device=device, since=since)
    if intel is None:
        raise HTTPException(status_code=404, detail=f"No funnel data for screen '{screen}'")

    settings = request.app.state.settings
    try:
        issue_key = await create_jira_issue(
            base_url=settings.jira_base_url,
            email=settings.jira_email,
            api_token=settings.jira_api_token,
            project_key=settings.jira_project_key,
            summary=f"[FlowSage] Friction node: {screen}",
            description=intel.ai_insight,
        )
    except JiraNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc
    except JiraDeliveryError as exc:
        raise HTTPException(502, str(exc)) from exc
    return JiraExportResult(issue_key=issue_key)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_node_export_api.py -v`
Expected: 4 PASS

- [ ] **Step 5: Full backend test suite + mypy**

Run: `cd backend && uv run pytest -q && uv run mypy --strict src`
Expected: all PASS, `Success: no issues found`

---

## Task 8: Weekly digest — arq `cron_job`

**Files:**
- Modify: `backend/src/flowsage_backend/worker.py`
- Test: `backend/tests/test_worker.py`

**Interfaces:**
- Consumes: `flowsage_backend.alerts.{build_alerts_report, build_digest_text, build_digest_blocks}` (Task 4), `flowsage_backend.integrations.slack.{post_slack_message, SlackNotConfiguredError}` (Task 2).
- Produces: `async def run_weekly_digest_job(ctx: dict[str, Any]) -> None`, added to `WorkerSettings.cron_jobs`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_worker.py`. `run_weekly_digest_job` reads settings via `flowsage_backend.config.get_settings()` (not from `ctx`), so tests monkeypatch that module-level name directly — reusing the `settings`/`db_session` fixtures from `conftest.py` rather than needing the full `app` fixture:

```python
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend import worker as worker_module
from flowsage_backend.config import Settings
from flowsage_backend.worker import run_weekly_digest_job


async def test_run_weekly_digest_job_skips_silently_when_slack_not_configured(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert settings.slack_webhook_url is None
    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)

    ctx: dict[str, Any] = {"session_factory": lambda: db_session}

    # Must not raise -- an unconfigured Slack webhook is a normal, expected
    # state for the digest job to skip quietly (same "not configured" signal
    # the manual /alerts/digest/run endpoint turns into a 400 for a caller who
    # can see it; a background cron job has no caller to show it to).
    await run_weekly_digest_job(ctx)


async def test_run_weekly_digest_job_posts_when_slack_configured(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = settings.model_copy(update={"slack_webhook_url": "https://hooks.slack.test/x"})
    monkeypatch.setattr(worker_module, "get_settings", lambda: configured)

    posted: dict[str, object] = {}

    async def _fake_post(webhook_url: str | None, *, text: str, blocks: object = None) -> None:
        posted["webhook_url"] = webhook_url
        posted["text"] = text

    monkeypatch.setattr(worker_module, "post_slack_message", _fake_post)

    ctx: dict[str, Any] = {"session_factory": lambda: db_session}
    await run_weekly_digest_job(ctx)

    assert posted["webhook_url"] == "https://hooks.slack.test/x"
    assert "FlowSage Weekly Digest" in str(posted["text"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_worker.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_weekly_digest_job'`

- [ ] **Step 3: Write the implementation**

Edit `backend/src/flowsage_backend/worker.py`. Add imports:

```python
from arq import cron

from flowsage_backend.alerts import build_alerts_report, build_digest_blocks, build_digest_text
from flowsage_backend.integrations.slack import SlackNotConfiguredError, post_slack_message
```

Add the job function, after `run_retraining_job`:

```python
async def run_weekly_digest_job(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    session_factory = ctx["session_factory"]
    async with session_factory() as session:
        report = await build_alerts_report(session)

    try:
        await post_slack_message(
            settings.slack_webhook_url,
            text=build_digest_text(report),
            blocks=build_digest_blocks(report),
        )
    except SlackNotConfiguredError:
        # No Slack configured -- a background job has no caller to surface this
        # to, unlike POST /alerts/digest/run's 400. Quietly skip.
        pass
```

Update `WorkerSettings` to register the cron job:

```python
class WorkerSettings:
    functions = [run_simulation_job, run_retraining_job]
    cron_jobs = [cron(run_weekly_digest_job, weekday="mon", hour=9, minute=0)]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_worker.py -v`
Expected: 2 PASS

- [ ] **Step 5: Full backend test suite + mypy**

Run: `cd backend && uv run pytest -q && uv run mypy --strict src`
Expected: all PASS, `Success: no issues found`

---

## Task 9: Frontend types + API client

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Produces (types.ts): `CalibrationAlert`, `ChurnAlert`, `AlertsReport`, `SlackExportResult`, `JiraExportResult`.
- Produces (api.ts): `api.getAlerts()`, `api.exportIssueToSlack(issueId)`, `api.exportIssueToJira(issueId)`, `api.exportNodeToSlack(screen)`, `api.exportNodeToJira(screen)`.

- [ ] **Step 1: Add the types**

Append to `frontend/src/lib/types.ts`:

```typescript
export interface CalibrationAlert {
  persona_name: string;
  screen: string;
  delta: number;
}

export interface ChurnAlert {
  cohort: string;
  risk_score: number;
  top_reason: string;
}

export interface AlertsReport {
  calibration_alerts: CalibrationAlert[];
  churn_alerts: ChurnAlert[];
}

export interface SlackExportResult {
  status: string;
}

export interface JiraExportResult {
  issue_key: string;
}
```

- [ ] **Step 2: Add the API client methods**

Edit `frontend/src/lib/api.ts`. Add to the `import type { ... }` block at the top:

```typescript
import type {
  AlertsReport,
  CalibrationReport,
  ChurnRiskSegment,
  CohortComparisonReport,
  FunnelFilters,
  FunnelReport,
  JiraExportResult,
  NodeIntelligence,
  Persona,
  RetrainingJob,
  SimulationRun,
  SimulationRunDetail,
  SlackExportResult,
  User,
} from "./types";
```

Add these methods inside the `export const api = { ... }` object, after `getNodeIntelligence`:

```typescript
  getAlerts: (): Promise<AlertsReport> => request<AlertsReport>("/alerts"),

  exportIssueToSlack: (issueId: string): Promise<SlackExportResult> =>
    request<SlackExportResult>(`/friction-issues/${issueId}/export/slack`, { method: "POST" }),

  exportIssueToJira: (issueId: string): Promise<JiraExportResult> =>
    request<JiraExportResult>(`/friction-issues/${issueId}/export/jira`, { method: "POST" }),

  exportNodeToSlack: (screen: string): Promise<SlackExportResult> =>
    request<SlackExportResult>(
      `/graph/nodes/${encodeURIComponent(screen)}/export/slack`,
      { method: "POST" },
    ),

  exportNodeToJira: (screen: string): Promise<JiraExportResult> =>
    request<JiraExportResult>(
      `/graph/nodes/${encodeURIComponent(screen)}/export/jira`,
      { method: "POST" },
    ),
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: exits 0, no errors.

---

## Task 10: Export buttons on `FrictionIssueCard` (RunningSimulationPage)

**Files:**
- Modify: `frontend/src/routes/predictive/RunningSimulationPage.tsx`
- Create: `frontend/src/routes/predictive/RunningSimulationPage.test.tsx`

**Interfaces:**
- Consumes: `api.exportIssueToSlack`, `api.exportIssueToJira` (Task 9).

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/predictive/RunningSimulationPage.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { SimulationRunDetail } from "../../lib/types";
import { RunningSimulationPage } from "./RunningSimulationPage";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getSimulation: vi.fn(),
      simulationStreamUrl: vi.fn().mockReturnValue("http://test/stream"),
      exportIssueToSlack: vi.fn(),
      exportIssueToJira: vi.fn(),
    },
  };
});

class MockEventSource {
  static instances: MockEventSource[] = [];
  onerror: (() => void) | null = null;
  listeners: Record<string, ((event: MessageEvent<string>) => void)[]> = {};
  constructor() {
    MockEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    (this.listeners[type] ??= []).push(listener);
  }
  close() {}
}
vi.stubGlobal("EventSource", MockEventSource);

const RUN: SimulationRunDetail = {
  id: "run-1",
  flow_name: "Checkout",
  goal: "Buy a widget",
  persona_id: "persona-1",
  status: "completed",
  error: null,
  steps: [],
  issues: [
    {
      id: "issue-1",
      screen: "checkout",
      severity: "high",
      title: "Confusing CTA",
      heuristic_violated: "Visibility of system status",
      persona_impact: "Anxious users abandon.",
      description: "The primary button is unlabeled.",
      suggested_fix: "Add a clear label.",
    },
  ],
};

describe("RunningSimulationPage export buttons", () => {
  it("exports a friction issue to Slack and shows a success message", async () => {
    vi.mocked(api.getSimulation).mockResolvedValue(RUN);
    vi.mocked(api.exportIssueToSlack).mockResolvedValue({ status: "sent" });

    render(
      <MemoryRouter initialEntries={["/predictive/runs/run-1"]}>
        <Routes>
          <Route path="/predictive/runs/:runId" element={<RunningSimulationPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const slackButton = await screen.findByRole("button", { name: "Export to Slack" });
    fireEvent.click(slackButton);

    expect(await screen.findByText(/Exported to Slack/)).toBeInTheDocument();
    expect(api.exportIssueToSlack).toHaveBeenCalledWith("issue-1");
  });

  it("exports a friction issue to Jira and shows the created issue key", async () => {
    vi.mocked(api.getSimulation).mockResolvedValue(RUN);
    vi.mocked(api.exportIssueToJira).mockResolvedValue({ issue_key: "FLOW-42" });

    render(
      <MemoryRouter initialEntries={["/predictive/runs/run-1"]}>
        <Routes>
          <Route path="/predictive/runs/:runId" element={<RunningSimulationPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const jiraButton = await screen.findByRole("button", { name: "Export to Jira" });
    fireEvent.click(jiraButton);

    expect(await screen.findByText(/FLOW-42/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/routes/predictive/RunningSimulationPage.test.tsx`
Expected: FAIL — no "Export to Slack"/"Export to Jira" buttons found.

- [ ] **Step 3: Implement the export buttons**

Edit `frontend/src/routes/predictive/RunningSimulationPage.tsx`. Replace the `FrictionIssueCard` function (the whole function, at the bottom of the file) with:

```tsx
function FrictionIssueCard({ issue }: { issue: FrictionIssue }) {
  const [exportStatus, setExportStatus] = useState<string | null>(null);

  const handleExport = async (target: "slack" | "jira") => {
    setExportStatus(null);
    try {
      if (target === "slack") {
        await api.exportIssueToSlack(issue.id);
        setExportStatus("Exported to Slack.");
      } else {
        const result = await api.exportIssueToJira(issue.id);
        setExportStatus(`Created Jira issue ${result.issue_key}.`);
      }
    } catch (err) {
      setExportStatus(err instanceof ApiError ? err.message : "Export failed.");
    }
  };

  return (
    <li className="ghost-border rounded-lg p-4">
      <div className="flex items-center gap-2">
        <span className="text-xs font-label uppercase tracking-wide text-error">
          {SEVERITY_LABEL[issue.severity]}
        </span>
        <p className="font-medium">{issue.title}</p>
      </div>
      <p className="text-sm text-on-surface-variant mt-2">{issue.description}</p>
      <dl className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm">
        <div>
          <dt className="text-on-surface-variant">Heuristic violated</dt>
          <dd>{issue.heuristic_violated}</dd>
        </div>
        <div>
          <dt className="text-on-surface-variant">Persona impact</dt>
          <dd>{issue.persona_impact}</dd>
        </div>
      </dl>
      <p className="text-sm mt-3">
        <span className="text-on-surface-variant">Suggested fix: </span>
        {issue.suggested_fix}
      </p>
      <div className="flex items-center gap-4 mt-3">
        <button
          type="button"
          onClick={() => void handleExport("slack")}
          className="text-sm text-primary hover:underline"
        >
          Export to Slack
        </button>
        <button
          type="button"
          onClick={() => void handleExport("jira")}
          className="text-sm text-primary hover:underline"
        >
          Export to Jira
        </button>
      </div>
      {exportStatus !== null ? (
        <p className="text-xs text-on-surface-variant mt-2">{exportStatus}</p>
      ) : null}
    </li>
  );
}
```

`useState` is already imported at the top of the file (`import { useEffect, useRef, useState } from "react";`) — no import changes needed.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/routes/predictive/RunningSimulationPage.test.tsx`
Expected: 2 PASS

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: exits 0

---

## Task 11: Export buttons on `NodeIntelligenceAside` (JourneyGraphPage)

**Files:**
- Modify: `frontend/src/routes/journey/JourneyGraphPage.tsx`
- Modify: `frontend/src/routes/journey/JourneyGraphPage.test.tsx`

**Interfaces:**
- Consumes: `api.exportNodeToSlack`, `api.exportNodeToJira` (Task 9).

- [ ] **Step 1: Extend the failing test**

Edit `frontend/src/routes/journey/JourneyGraphPage.test.tsx`. Add `exportNodeToSlack`/`exportNodeToJira` to the `vi.mock` block's `api` object:

```typescript
vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getFunnel: vi.fn(),
      getChurnRisk: vi.fn().mockResolvedValue([]),
      getCohortComparison: vi.fn().mockResolvedValue({ cohorts: [], screens: [] }),
      getNodeIntelligence: vi.fn(),
      exportNodeToSlack: vi.fn(),
      exportNodeToJira: vi.fn(),
    },
  };
});
```

Add a new test at the end of the `describe("JourneyGraphPage", ...)` block (find the existing `it("opens the Node Intelligence aside when a friction node is clicked", ...)` test to see the exact node-opening flow it already exercises, and add a sibling test after it):

```tsx
  it("exports the open node to Slack and shows a success message", async () => {
    vi.mocked(api.getFunnel).mockResolvedValue({
      funnel: [{ screen: "checkout", sessions_entered: 10, sessions_continued: 5, drop_off_rate: 0.5 }],
      friction_nodes: [
        { screen: "checkout", kind: "abnormal_drop_off", detail: "High drop-off.", sessions_affected: 5 },
      ],
      total_sessions: 10,
      total_events: 20,
    });
    vi.mocked(api.getNodeIntelligence).mockResolvedValue({
      screen: "checkout",
      drop_off_rate: 0.5,
      avg_seconds_on_node: 30,
      friction_nodes: [],
      ai_insight: "High drop-off at checkout.",
      recommendations: [],
    });
    vi.mocked(api.exportNodeToSlack).mockResolvedValue({ status: "sent" });

    render(<JourneyGraphPage />);

    const nodeButton = await screen.findByText("checkout");
    fireEvent.click(nodeButton);
    await screen.findByText("High drop-off at checkout.");

    const slackButton = screen.getByRole("button", { name: "Export to Slack" });
    fireEvent.click(slackButton);

    expect(await screen.findByText(/Exported to Slack/)).toBeInTheDocument();
    expect(api.exportNodeToSlack).toHaveBeenCalledWith("checkout");
  });
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/routes/journey/JourneyGraphPage.test.tsx`
Expected: FAIL — no "Export to Slack" button in the aside yet.

- [ ] **Step 3: Implement the export buttons**

Edit `frontend/src/routes/journey/JourneyGraphPage.tsx`. Replace the `NodeIntelligenceAside` function with a version that adds export buttons + status. Full replacement:

```tsx
function NodeIntelligenceAside({
  node,
  intel,
  error,
  onClose,
}: {
  node: FrictionNode;
  intel: NodeIntelligence | null;
  error: string | null;
  onClose: () => void;
}) {
  const [exportStatus, setExportStatus] = useState<string | null>(null);

  const handleExport = async (target: "slack" | "jira") => {
    setExportStatus(null);
    try {
      if (target === "slack") {
        await api.exportNodeToSlack(node.screen);
        setExportStatus("Exported to Slack.");
      } else {
        const result = await api.exportNodeToJira(node.screen);
        setExportStatus(`Created Jira issue ${result.issue_key}.`);
      }
    } catch (err) {
      setExportStatus(err instanceof ApiError ? err.message : "Export failed.");
    }
  };

  return (
    <aside className="w-[380px] shrink-0 bg-surface-container-lowest rounded-xl p-6 h-fit sticky top-6">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h2 className="font-headline text-xl">Node Intelligence</h2>
          <p className="text-sm text-on-surface-variant">Analysis of {node.screen}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="text-on-surface-variant hover:text-on-surface text-xl leading-none"
        >
          ×
        </button>
      </div>

      {error !== null ? <p className="text-error text-sm">{error}</p> : null}

      {intel === null && error === null ? (
        <p className="text-on-surface-variant text-sm">Loading…</p>
      ) : null}

      {intel !== null ? (
        <div className="flex flex-col gap-4">
          <div className="rounded-lg border-l-4 border-error bg-error-container/20 p-4">
            <p className="text-xs font-label uppercase tracking-wide text-error mb-1">
              AI Insight
            </p>
            <p className="text-sm">{intel.ai_insight}</p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="ghost-border rounded-lg p-3">
              <p className="text-xs text-on-surface-variant">Drop-off Rate</p>
              <p className="font-headline text-xl">{(intel.drop_off_rate * 100).toFixed(1)}%</p>
            </div>
            <div className="ghost-border rounded-lg p-3">
              <p className="text-xs text-on-surface-variant">Avg. Time on Node</p>
              <p className="font-headline text-xl">
                {intel.avg_seconds_on_node === null
                  ? "—"
                  : `${intel.avg_seconds_on_node.toFixed(0)}s`}
              </p>
            </div>
          </div>

          {intel.recommendations.length > 0 ? (
            <div>
              <p className="text-xs font-label uppercase tracking-wide text-on-surface-variant mb-2">
                Re-engagement Recommendations
              </p>
              <ul className="flex flex-col gap-2">
                {intel.recommendations.map((rec) => (
                  <li key={rec.rank} className="ghost-border rounded-lg p-3">
                    <p className="font-medium text-sm">
                      {rec.rank} — {rec.title}
                    </p>
                    <p className="text-xs text-on-surface-variant mt-1">{rec.description}</p>
                    {rec.expected_lift_pct !== null ? (
                      <p className="text-xs text-primary mt-1">
                        Expected conversion lift: +{rec.expected_lift_pct.toFixed(0)}%
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={() => void handleExport("slack")}
              className="text-sm text-primary hover:underline"
            >
              Export to Slack
            </button>
            <button
              type="button"
              onClick={() => void handleExport("jira")}
              className="text-sm text-primary hover:underline"
            >
              Export to Jira
            </button>
          </div>
          {exportStatus !== null ? (
            <p className="text-xs text-on-surface-variant">{exportStatus}</p>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/routes/journey/JourneyGraphPage.test.tsx`
Expected: all PASS (existing tests + the new one)

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: exits 0

---

## Task 12: Dashboard alerts banner

**Files:**
- Modify: `frontend/src/routes/DashboardPage.tsx`
- Create: `frontend/src/routes/DashboardPage.test.tsx`

**Interfaces:**
- Consumes: `api.getAlerts` (Task 9).

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/DashboardPage.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import { DashboardPage } from "./DashboardPage";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listPersonas: vi.fn().mockResolvedValue([]),
      getFunnel: vi.fn().mockResolvedValue({
        funnel: [],
        friction_nodes: [],
        total_sessions: 0,
        total_events: 0,
      }),
      getAlerts: vi.fn(),
    },
  };
});

describe("DashboardPage alerts banner", () => {
  it("shows nothing when there are no alerts", async () => {
    vi.mocked(api.getAlerts).mockResolvedValue({ calibration_alerts: [], churn_alerts: [] });

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    await screen.findByText("Executive Summary");
    expect(screen.queryByText("Alerts")).not.toBeInTheDocument();
  });

  it("shows a banner with calibration and churn alerts", async () => {
    vi.mocked(api.getAlerts).mockResolvedValue({
      calibration_alerts: [{ persona_name: "Nora", screen: "checkout", delta: 0.7 }],
      churn_alerts: [{ cohort: "at_risk", risk_score: 0.72, top_reason: "High drop-off" }],
    });

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Alerts")).toBeInTheDocument();
    expect(screen.getByText(/Nora on checkout/)).toBeInTheDocument();
    expect(screen.getByText(/at_risk at 72%/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/routes/DashboardPage.test.tsx`
Expected: FAIL — no "Alerts" banner rendered.

- [ ] **Step 3: Implement the alerts banner**

Edit `frontend/src/routes/DashboardPage.tsx`. Update the imports:

```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import type { AlertsReport, FunnelReport, Persona } from "../lib/types";
```

Update the component to load and render alerts. Replace the whole `DashboardPage` function body:

```tsx
export function DashboardPage() {
  const [personas, setPersonas] = useState<Persona[] | null>(null);
  const [funnel, setFunnel] = useState<FunnelReport | null>(null);
  const [alerts, setAlerts] = useState<AlertsReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.listPersonas(), api.getFunnel()])
      .then(([personaList, funnelReport]) => {
        setPersonas(personaList);
        setFunnel(funnelReport);
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load dashboard data.");
      });

    api.getAlerts().then(setAlerts).catch(() => setAlerts(null));
  }, []);

  const topFriction = funnel?.friction_nodes.slice(0, 3) ?? [];
  const hasAlerts =
    alerts !== null && (alerts.calibration_alerts.length > 0 || alerts.churn_alerts.length > 0);

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="font-headline text-3xl">Executive Summary</h1>
        <p className="text-on-surface-variant mt-1">
          Where users will struggle, where they are struggling, and how well FlowSage is
          predicting the difference.
        </p>
      </div>

      {error !== null ? <p className="text-error text-sm">{error}</p> : null}

      {hasAlerts && alerts !== null ? (
        <div className="rounded-xl border-l-4 border-error bg-error-container/20 p-4">
          <span className="inline-block rounded-full bg-error-container px-3 py-1 text-xs font-label uppercase tracking-wide text-on-error-container mb-2">
            Alerts
          </span>
          <ul className="text-sm mt-2 flex flex-col gap-1">
            {alerts.calibration_alerts.map((alert) => (
              <li key={`cal-${alert.persona_name}-${alert.screen}`}>
                Calibration anomaly: {alert.persona_name} on {alert.screen} (delta{" "}
                {alert.delta.toFixed(2)})
              </li>
            ))}
            {alerts.churn_alerts.map((alert) => (
              <li key={`churn-${alert.cohort}`}>
                Churn risk: {alert.cohort} at {(alert.risk_score * 100).toFixed(0)}% —{" "}
                {alert.top_reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <SummaryCard
          label="Total Sessions Observed"
          value={funnel?.total_sessions.toLocaleString() ?? "—"}
        />
        <SummaryCard label="Events Ingested" value={funnel?.total_events.toLocaleString() ?? "—"} />
        <SummaryCard label="Active Personas" value={personas?.length.toString() ?? "—"} />
      </section>

      <section className="bg-surface-container-lowest rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-headline text-xl">Top Friction Nodes</h2>
          <Link to="/journey" className="text-sm text-primary hover:underline">
            View Journey Graph →
          </Link>
        </div>
        {topFriction.length === 0 ? (
          <p className="text-on-surface-variant text-sm">
            No friction detected yet. Ingest events to see the journey graph populate.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {topFriction.map((node) => (
              <li key={`${node.screen}-${node.kind}`} className="ghost-border rounded-lg p-4">
                <p className="font-medium">{node.screen}</p>
                <p className="text-sm text-on-surface-variant mt-1">{node.detail}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="bg-surface-container-lowest rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-headline text-xl">Persona Insights</h2>
          <Link to="/predictive" className="text-sm text-primary hover:underline">
            Manage Personas →
          </Link>
        </div>
        {personas === null || personas.length === 0 ? (
          <p className="text-on-surface-variant text-sm">No personas loaded yet.</p>
        ) : (
          <ul className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {personas.map((persona) => (
              <li key={persona.id} className="ghost-border rounded-lg p-4">
                <p className="font-medium">{persona.name}</p>
                <p className="text-sm text-on-surface-variant mt-1 line-clamp-2">
                  {persona.description}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
```

(`SummaryCard` below it is unchanged — do not remove it.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/routes/DashboardPage.test.tsx`
Expected: 2 PASS

- [ ] **Step 5: Full frontend test suite + typecheck**

Run: `cd frontend && npm run test && npm run typecheck`
Expected: all PASS, exits 0

---

## Task 13: Review, format, full-stack verification, single commit + push

**Files:** none new — this task reviews/formats everything touched in Tasks 1-12 and performs the repo's standing pre-commit checklist (per `[[feedback-build-process-rules]]`).

- [ ] **Step 1: autoflake8 + black on touched Python**

Run:
```bash
cd backend
uv run autoflake8 --in-place --remove-all-unused-imports -r src/flowsage_backend/alerts.py src/flowsage_backend/integrations src/flowsage_backend/api/alerts.py src/flowsage_backend/api/exports.py src/flowsage_backend/api/events.py src/flowsage_backend/config.py src/flowsage_backend/worker.py src/flowsage_backend/main.py
uv run black src/flowsage_backend/alerts.py src/flowsage_backend/integrations src/flowsage_backend/api/alerts.py src/flowsage_backend/api/exports.py src/flowsage_backend/api/events.py src/flowsage_backend/config.py src/flowsage_backend/worker.py src/flowsage_backend/main.py tests/test_alerts.py tests/test_alerts_api.py tests/test_exports_api.py tests/test_node_export_api.py tests/test_integrations_slack.py tests/test_integrations_jira.py tests/test_worker.py tests/test_config.py
```
Expected: exits 0; review the diff for anything unexpected (autoflake8 removing an import that's actually used via `TYPE_CHECKING` would be the one thing to double check — none of this plan's files use that pattern, so none is expected).

- [ ] **Step 2: Full backend suite + strict mypy**

Run: `cd backend && uv run pytest -q && uv run mypy --strict src`
Expected: all tests PASS, `Success: no issues found in N source files`

- [ ] **Step 3: Full frontend suite + typecheck + lint**

Run: `cd frontend && npm run test && npm run typecheck && npm run lint`
Expected: all PASS

- [ ] **Step 4: Safety/security review pass**

Manually re-read the diff (`git diff`) for:
- Slack/Jira credentials never logged (check `alerts.py`, `integrations/slack.py`, `integrations/jira.py`, and both `api/exports.py`/`api/events.py` export handlers for any stray `print`/`logging` of `settings.jira_api_token` or the webhook URL — there should be none).
- `Jira` client uses `httpx.BasicAuth`, not string-formatted into a URL or header manually (already the case in Task 3's implementation — confirm it wasn't changed).
- Every new/modified route still carries `Depends(get_current_user)` (via its router's `dependencies=`) — confirm `alerts_router`, `exports_router`, and the two new `graph_router` routes are unauthenticated-401 as tested in Tasks 5-7.
- No new endpoint accepts a caller-supplied Slack webhook URL or Jira base URL (both always come from `request.app.state.settings`, never from request body/query) — this is the key SSRF-shaped risk for a "post to this URL" feature, and this plan's design never takes a URL from the caller, so confirm that held in the actual diff.

- [ ] **Step 5: `docker-compose` full-stack verification**

```bash
cd /home/asus/Projects/personal/FlowSage
docker compose -f infra/docker-compose.yml up -d --build
```

Wait for all services healthy, then:

```bash
# Log in and capture the session cookie
curl -c /tmp/flowsage-cookies.txt -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "<seeded admin email>", "password": "<seeded admin password>"}'

# GET /alerts -- should return 200 with (possibly empty) calibration_alerts/churn_alerts
curl -b /tmp/flowsage-cookies.txt http://localhost:8000/alerts

# POST /alerts/digest/run -- Slack env var unset in this compose stack, expect 400 "not configured"
curl -i -b /tmp/flowsage-cookies.txt -X POST http://localhost:8000/alerts/digest/run

# Friction-issue export -- use a real issue id from a completed simulation run
# (seed one first if none exists), expect 400 "not configured"
curl -i -b /tmp/flowsage-cookies.txt -X POST http://localhost:8000/friction-issues/<issue-id>/export/slack
curl -i -b /tmp/flowsage-cookies.txt -X POST http://localhost:8000/friction-issues/<issue-id>/export/jira

# Node export -- use a real screen name from the ingested sample data
curl -i -b /tmp/flowsage-cookies.txt -X POST http://localhost:8000/graph/nodes/<screen>/export/slack
curl -i -b /tmp/flowsage-cookies.txt -X POST http://localhost:8000/graph/nodes/<screen>/export/jira
```

Expected: `/alerts` returns 200 JSON; all four export curls and the digest curl return 400 with a "not configured" detail message (no real Slack/Jira credentials are available in this environment, per the approved spec — this confirms the unconfigured path is clean, not that a real message/issue was created).

Then drive the browser: log in via the real frontend, open a completed simulation run and click "Export to Slack" on a friction issue (confirm the inline "not configured"-derived error text renders instead of a blank failure), then open the Journey Graph, click a friction node, and click "Export to Slack" in the Node Intelligence aside (same expectation). Confirm the Dashboard shows an "Alerts" banner if the seeded/sample data produces any calibration or churn alerts, or confirm its absence is silent (no broken layout) if not.

Tear down:
```bash
docker compose -f infra/docker-compose.yml down
```

- [ ] **Step 6: Commit and push**

```bash
cd /home/asus/Projects/personal/FlowSage
git add backend/src/flowsage_backend/alerts.py \
        backend/src/flowsage_backend/integrations \
        backend/src/flowsage_backend/api/alerts.py \
        backend/src/flowsage_backend/api/exports.py \
        backend/src/flowsage_backend/api/events.py \
        backend/src/flowsage_backend/config.py \
        backend/src/flowsage_backend/worker.py \
        backend/src/flowsage_backend/main.py \
        backend/pyproject.toml \
        backend/tests/test_alerts.py \
        backend/tests/test_alerts_api.py \
        backend/tests/test_exports_api.py \
        backend/tests/test_node_export_api.py \
        backend/tests/test_integrations_slack.py \
        backend/tests/test_integrations_jira.py \
        backend/tests/test_worker.py \
        backend/tests/test_config.py \
        frontend/src/lib/types.ts \
        frontend/src/lib/api.ts \
        frontend/src/routes/predictive/RunningSimulationPage.tsx \
        frontend/src/routes/predictive/RunningSimulationPage.test.tsx \
        frontend/src/routes/journey/JourneyGraphPage.tsx \
        frontend/src/routes/journey/JourneyGraphPage.test.tsx \
        frontend/src/routes/DashboardPage.tsx \
        frontend/src/routes/DashboardPage.test.tsx \
        uv.lock

git commit -m "$(cat <<'EOF'
feat: trend alerts, Slack/Jira export, weekly digest (Phase 2 chunk 3/4)

Fixed-threshold calibration/churn alerts (no new AlertRule table), Slack
webhook + Jira issue export on friction issues/nodes, and an arq cron
weekly digest. Slack/Jira config is env-var-only (no Integration model/UI
yet -- that's Phase 3); verified against mocked HTTP transports since no
live Slack/Jira credentials are available in this environment.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"

git push origin main
```

- [ ] **Step 7: Update memory**

After pushing, update the `project-build-status` memory (`/home/asus/.claude/projects/-home-asus-Projects-personal-FlowSage/memory/project_build_status.md`) with a new dated entry: Phase 2 chunk 3/4 done, commit hash, and the same kind of "what shipped / key decisions / gotchas found" summary as prior entries — specifically noting the env-var-only config choice, the fixed-threshold (not configurable) alert design, and that Slack/Jira delivery itself is untested against real infra (mocked only) pending real credentials. Note next up: Phase 2 chunk 4/4 (persona library CRUD + `/settings/model-calibration`).

---

## Self-Review Notes

**Spec coverage:** All five spec sections map to tasks — architecture (Tasks 2-4), endpoints (Tasks 5-7), weekly digest (Task 8), frontend (Tasks 9-12), testing (embedded in every task) + the final full-stack pass (Task 13). Out-of-scope items (Integration model, configurable alert rules, live Slack/Jira) are explicitly not built anywhere in this plan.

**Placeholder scan:** An earlier draft of Task 4 (Step 4) and Task 8 (Steps 1-2) showed an intermediate "wrong" version before the real one as a didactic device; removed during self-review since it's itself a form of placeholder/dead code a fresh implementer could copy verbatim. Every task now goes straight to complete, correct code with no TBD/TODO.

**Type consistency:** `SlackExportResult`/`JiraExportResult` are defined twice (Task 6's `api/exports.py`, Task 7's `api/events.py`) with identical shapes, intentionally (see Task 7's Interfaces note) — both serialize identically on the wire, so the frontend's single `SlackExportResult`/`JiraExportResult` TypeScript types (Task 9) work against either endpoint family without needing to know which Python class produced them. `AlertsReport`/`CalibrationAlert`/`ChurnAlert` are defined once in `alerts.py` (Task 4) and consumed by `api/alerts.py` (Task 5) and the frontend (Tasks 9, 12) with matching field names throughout (`persona_name`, `screen`, `delta`, `cohort`, `risk_score`, `top_reason`).
