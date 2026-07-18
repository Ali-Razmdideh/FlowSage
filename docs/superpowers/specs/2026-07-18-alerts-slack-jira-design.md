# Phase 2 chunk 3: Trend alerts + Slack/Jira export + weekly digest

Date: 2026-07-18
Status: Approved, pending implementation plan.

## Context

Phase 2 chunks 1-2 (calibration engine, cohort/churn) are done and pushed. This is chunk
3 of 4: "Trend tracking + alert rules; Slack webhook + Jira issue auto-filing (annotated
tickets, 'Export to Engineering Ticket'/'Export to Jira' buttons); weekly digest job" per
`plans/full-project-coding-plan.md` Phase 2 item 3.

The full multi-tenant `Integration` model + `/settings/integrations` UI (API key issue/
revoke, webhook delivery log, marketplace cards) is explicitly a Phase 3 item. This chunk
only needs Slack/Jira *export actions* to work for the current single-tenant app, not a
configuration UI or per-workspace integration rows.

No real Slack workspace or Jira instance credentials are available for this build. Slack/
Jira client code is real (real HTTP calls, real payload shapes) but is verified with a
mocked HTTP transport, not a live external call. Live verification is deferred until the
user configures real credentials via env vars.

## Decisions made during brainstorming

1. **Credentials/config: env vars only**, not a new DB table or settings UI. `Settings`
   (`backend/src/flowsage_backend/config.py`) gains optional Slack/Jira fields. This
   matches the single-tenant nature of the app today; Phase 3 replaces this with
   per-workspace `Integration` rows once multi-tenancy lands.
2. **Alert rules: fixed built-in thresholds**, not a new configurable `AlertRule` table/
   UI. Reuses the existing calibration delta threshold and churn risk threshold as the
   trigger condition. No rule-config screen was scoped in the plan for this chunk (that's
   more of a Phase 3/settings concern if it ever gets built).
3. **Verification: mocked HTTP**, not live Slack/Jira. Client functions are real and
   tested against a mocked transport; the `docker-compose` verification pass at the end
   confirms endpoints correctly no-op/error when Slack/Jira env vars are unset ("not
   configured" response), not that a real message/issue was created.

## Architecture

New `backend/src/flowsage_backend/alerts.py` — compute-on-demand, same pattern as
`churn.py`/`calibration.py` (no new "alert" DB rows, always freshly computed):

- `check_calibration_anomalies()` — reuses the existing calibration delta threshold
  (`calibration.py`), returns triggered alerts (screen, delta, severity).
- `check_churn_alerts()` — reuses the existing churn risk threshold (`churn.py`), returns
  triggered segments.

New `backend/src/flowsage_backend/integrations/` package — thin httpx clients:

- `slack.py`: `post_slack_message(webhook_url, blocks)` — POSTs a Block Kit payload to
  `SLACK_WEBHOOK_URL`. Raises on non-2xx.
- `jira.py`: `create_jira_issue(base_url, email, api_token, project_key, summary,
  description)` — POSTs to Jira REST `/rest/api/3/issue`. Raises on non-2xx.
- Callers catch client exceptions and surface a clean error in the API response — no
  silent failure.

`Settings` (`config.py`) gains optional fields: `slack_webhook_url: str | None`,
`jira_base_url: str | None`, `jira_email: str | None`, `jira_api_token: str | None`,
`jira_project_key: str | None`. All optional — unlike `JWT_SECRET`/`EVENTS_API_KEY`, these
have no placeholder-secret startup guard, since the feature is meant to work unconfigured
(exports just return a "not configured" error until set).

## Endpoints

- `POST /friction-issues/{id}/export/slack`
- `POST /friction-issues/{id}/export/jira`
  — for predicted `FrictionIssue` rows (surfaced today in `RunningSimulationPage`'s
  `FrictionIssueCard`).
- `POST /graph/nodes/{screen}/export/slack`
- `POST /graph/nodes/{screen}/export/jira`
  — for observational `FrictionNode`/`NodeIntelligence` (surfaced today in
  `JourneyGraphPage`'s `NodeIntelligenceAside`).
- `GET /alerts` — returns currently triggered calibration + churn alerts (for a dashboard
  banner), built from `alerts.py`.
- `POST /alerts/digest/run` — manually fires the weekly digest immediately (for testing
  without waiting a week); posts a Slack summary of current alerts + top friction.

All four export endpoints return a clean 400-style "Slack/Jira not configured" response
when the relevant env vars are unset, rather than a raw httpx/connection error.

## Weekly digest job

An arq `cron_job` (native arq scheduling — arq/Redis is already infra) registered in
`worker.py`, running weekly. It calls the same digest-building function that
`POST /alerts/digest/run` calls — one code path, two triggers (scheduled + manual-test).
The digest content is a Slack message summarizing current `alerts.py` output (calibration
anomalies + churn risk segments) plus top friction nodes, reusing existing report-building
functions rather than duplicating summary logic.

## Frontend

- "Export to Slack" / "Export to Jira" buttons added to:
  - `FrictionIssueCard` in `frontend/src/routes/predictive/RunningSimulationPage.tsx`
  - `NodeIntelligenceAside` in `frontend/src/routes/journey/JourneyGraphPage.tsx`
  - Each POSTs to the matching export endpoint; shows inline success or error text (e.g.
    "Jira not configured") — no toast library, matches existing plain-`useState` fetch
    convention used throughout the app.
- Small alert banner on `DashboardPage` sourced from `GET /alerts`, reusing the visual
  pattern of `CalibrationPage`'s existing anomaly banner.

## Testing

- `alerts.py`: unit tests as pure functions against fixture predicted/observed and churn
  data, same style as existing `churn.py` tests.
- `slack.py`/`jira.py`: tested against a mocked httpx transport (`httpx.MockTransport` or
  `respx`) — asserts the correct request shape (URL, payload) is sent; no live network
  call.
- Export endpoints: tested end-to-end with the Slack/Jira client functions monkeypatched,
  covering both the "configured" (client called, 200) and "not configured" (400, client
  not called) paths.
- Digest job: unit test that the digest-building function produces the expected Slack
  payload from fixture alert data; the arq `cron_job` registration itself is exercised via
  the manual `POST /alerts/digest/run` endpoint rather than waiting on a real weekly
  schedule.
- Full `docker-compose up -d --build` stack verification pass at the end (same bar as
  prior chunks): curl all new endpoints against real seeded Postgres data, confirm
  "not configured" responses are correct (no real Slack/Jira creds available), and drive
  the new frontend buttons with a real Playwright browser session to confirm the
  success/error UI renders correctly.

## Out of scope (deferred to Phase 3)

- Per-workspace `Integration` model, `WebhookEndpoint` delivery log, `/settings/integrations`
  UI, API key issue/revoke.
- Configurable alert-rule thresholds/UI.
- Live Slack/Jira verification (requires real credentials the user doesn't currently have).
