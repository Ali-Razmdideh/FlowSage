# Phase 3 Chunk 3 — Security Hardening (Audit Log, Rate Limiting, Secrets Encryption, Retention Purge)

**Status:** Approved 2026-07-24. Closes the SOC2-track bullet of `plans/full-project-coding-plan.md` Phase 3 item 3 (per-workspace Neo4j label isolation already shipped in `177614b`, so this chunk covers the remaining sub-items: audit log + Security Logs view, rate limiting, secrets hygiene).

## Context

Phase 3 chunks 1 (workspace multi-tenancy) and 2 (integrations) are merged to `main`. Investigation before this design found two live gaps beyond the plan's literal wording:

1. `JiraIntegration.api_token` and `Webhook.secret` are stored as plaintext `String` columns in Postgres. `ApiKey` already does this correctly (only `key_hash` is stored, via `key_prefix`/`key_hash`), so the fix pattern is "make Jira/webhook match ApiKey's existing discipline," not a new invention.
2. `Workspace.retention_days` has existed since Phase 3 chunk 1 but nothing enforces it — no purge job exists. This chunk wires it up for both the new audit log and the pre-existing `Event` table, since building the audit log purge job requires touching this code anyway.

There is no hi-fi prototype for a Security Logs screen (unlike every other settings tab, which mapped onto a `design-hifi-prototypes/settings_*` mock) — its UI is designed fresh in this spec, following the visual/structural conventions of the existing four settings pages (`GeneralSettingsPage`, `TeamSettingsPage`, `IntegrationsSettingsPage`, `ModelCalibrationSettingsPage`).

## Scope

**In scope:**
- `AuditLog` model + migration, written at: login, logout, invite sent, role changed, member removed, workspace archived, API key created/revoked, Slack/Jira connected/disconnected, webhook created/deleted, persona created/deleted, simulation run started, calibration settings changed.
- `GET /audit-logs` (paginated, filterable by action/actor), behind `get_current_membership` like every other workspace-scoped route.
- `SecurityLogsPage.tsx` at `/settings/security`, new sidebar entry alongside the other 4 settings tabs.
- Redis-backed rate limiting (slowapi) on: auth endpoints (per-IP), `POST /v1/events` (per-API-key), all other authenticated routes (per-user) — three tiers, 429 + `Retry-After`.
- Fernet encryption at rest for `JiraIntegration.api_token` and `Webhook.secret` via a SQLAlchemy `TypeDecorator`, new `SECRET_ENCRYPTION_KEY` setting (placeholder-guarded outside dev, same pattern as `JWT_SECRET`).
- Daily arq cron job purging `AuditLog` and `Event` rows older than each workspace's `retention_days`.

**Out of scope (explicitly deferred):**
- Retroactive backfill/migration of already-stored plaintext Jira tokens / webhook secrets in any existing deployed data — this is a fresh local dev stack, no production data exists to migrate.
- Per-endpoint configurable rate limit thresholds via a settings UI — thresholds are fixed constants this chunk, matching how `CHURN_RISK_ALERT_THRESHOLD` etc. started as constants before chunk 4 of Phase 2 made some of them configurable.
- Alerting/paging when rate limits are hit repeatedly (would be a monitoring/observability feature, not this chunk).

## Architecture & Data Flow

**Audit log write path:** `record_audit_event(session, workspace_id, actor_user_id, action, target_type, target_id, metadata, ip_address)` — a plain async function in `backend/src/flowsage_backend/audit.py`, called inline at each action site within the same DB transaction as the action itself (so an audit write failure can be caught and logged without ever blocking or rolling back the primary action — best-effort, mirroring how the existing Neo4j mirror write in the events path is already best-effort and never fails the primary request).

**Read path:** `GET /audit-logs?action=&actor_id=&cursor=&limit=` in `backend/src/flowsage_backend/api/audit.py`, paginated by `created_at DESC, id DESC` keyset pagination (consistent with growing-table pagination needs; no existing list endpoint in this codebase needs pagination yet, so this is the first one — keyset chosen over offset because audit logs are the kind of table that grows unbounded and offset pagination degrades linearly with table size).

**Rate limiting:** `slowapi.Limiter` with a Redis storage backend (reuses `settings.redis_url`, no new infra). Three named limiter instances/decorators applied at route registration:
- `@auth_limiter.limit("5/minute")` on `/auth/login` (per-IP, via `get_remote_address`)
- `@ingest_limiter.limit("120/minute")` on `POST /v1/events` (per-API-key, custom key func reading `X-API-Key` header)
- `@default_limiter.limit("300/minute")` applied globally via middleware for every other authenticated route (per-user, keyed off the JWT `sub` claim)

429 responses include `Retry-After`; slowapi's default `RateLimitExceeded` handler is registered on the app in `create_app()`.

**Secrets encryption:** `backend/src/flowsage_backend/crypto.py` exposes `EncryptedString(TypeDecorator)` wrapping `cryptography.fernet.Fernet` — `process_bind_param` encrypts on write, `process_result_value` decrypts on read, both using `Settings.secret_encryption_key`. `JiraIntegration.api_token` and `Webhook.secret` columns switch their `Mapped[str]` type annotation to use `EncryptedString` instead of `String`. No API/service-layer code changes needed since the encryption is transparent at the ORM boundary — every place that currently reads `.api_token` or `.secret` (Jira client, webhook HMAC signer) keeps working unchanged, now transparently getting the decrypted plaintext.

**Retention purge:** `run_retention_purge_job` added to `worker.py`'s arq cron list (daily, mirrors `run_digest_job`'s iterate-every-workspace shape). For each workspace: `DELETE FROM audit_logs WHERE workspace_id = :id AND created_at < now() - retention_days` and same for `events`. Runs in a single transaction per workspace so a failure on one workspace doesn't block others (loop continues, logs the exception — same resilience shape as the digest job's per-workspace iteration).

## Components

| File | Purpose |
|---|---|
| `backend/src/flowsage_backend/models/audit_log.py` + migration | `AuditLog` table |
| `backend/src/flowsage_backend/audit.py` | `record_audit_event()`, `list_audit_logs()` query helper |
| `backend/src/flowsage_backend/api/audit.py` | `GET /audit-logs` |
| `backend/src/flowsage_backend/rate_limit.py` | slowapi `Limiter` setup, 3 keyed limiter instances, exception handler registration |
| `backend/src/flowsage_backend/crypto.py` | `EncryptedString` TypeDecorator, Fernet helpers |
| `backend/src/flowsage_backend/config.py` | + `secret_encryption_key` field, placeholder guard extended |
| `backend/src/flowsage_backend/models/integration.py`, `models/webhook.py` | swap `api_token`/`secret` columns to `EncryptedString` + migration (column type change, no data to migrate) |
| Call sites across `api/auth.py`, `api/workspaces.py`, `api/integrations.py`, `api/personas.py`, `api/simulations.py`, `api/settings.py` | inline `record_audit_event()` calls at each listed action |
| `backend/src/flowsage_backend/worker.py` | + `run_retention_purge_job` cron entry |
| `backend/src/flowsage_backend/main.py` | wire `Limiter` into `create_app()`, register rate-limit exception handler, apply default limiter middleware |
| `frontend/src/pages/SecurityLogsPage.tsx` | `/settings/security` — paginated table (actor, action, target, IP, timestamp), action-type filter dropdown |
| `frontend/src/api/audit.ts` + types | `getAuditLogs()` client function |
| Sidebar nav | new "Security" entry alongside General/Team/Integrations/Model Calibration |

## Error Handling

- Audit write failure: caught, logged via standard logging, does not raise — the action it's logging always completes regardless.
- Rate limit exceeded: 429 with `Retry-After` header, standard slowapi response body (`{"error": "rate limit exceeded"}`).
- Missing/invalid `SECRET_ENCRYPTION_KEY` outside dev: fail fast at `Settings` construction (extends the existing `_reject_placeholder_secret_outside_dev` validator), same as `JWT_SECRET` today.
- Retention purge failure on one workspace: logged, loop continues to the next workspace (never lets one bad workspace block the whole cron run).

## Testing

- `audit.py`: unit tests for write + keyset-paginated query + action/actor filters.
- `crypto.py`: round-trip encrypt/decrypt test; tamper test (flipped ciphertext byte raises `InvalidToken`); confirms a raw DB read of the column is not the plaintext.
- `rate_limit.py`: integration test against a real Redis (not mocked) — burst past threshold, confirm 429, confirm reset after window.
- Migration: upgrade → downgrade → upgrade cycle for both the new `AuditLog` table and the column-type change on `api_token`/`secret`.
- Cross-tenant isolation test for `GET /audit-logs` (workspace A cannot see workspace B's entries) — same pattern as the existing API-key/webhook isolation tests from chunk 2.
- Playwright e2e: land on `/settings/security`, confirm a just-performed action (e.g. inviting a member in the same test) appears in the log table.
- Full `docker-compose up -d --build` pass: trigger each audited action for real, confirm it appears in the Security Logs page; confirm `psql` raw select on `jira_integrations.api_token`/`webhooks.secret` is not plaintext; curl-burst `/auth/login` and confirm 429; manually run the purge job against a seeded old row and confirm deletion.
