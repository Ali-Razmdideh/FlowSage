# Docs Page Design Spec

**Phase:** 4, item 4 (hardening) — third of 3 sub-chunks (CI hardening ✓ → deploy ✓ → landing/docs), second half of the landing/docs sub-chunk (landing page ✓ already shipped in `docs/superpowers/specs/2026-07-30-landing-page-design.md`).

## Problem

FlowSage has no developer-facing documentation beyond FastAPI's auto-generated Swagger UI at `/api/docs` — accurate but exhaustive and reference-only, no narrative path for "how do I actually get events into this thing." A prospect or new pilot customer has no page that explains, in order: how to get an API key, how to send events, how outbound webhooks work and how to verify their signature, before dropping into the full endpoint reference.

## Design

### Architecture

New route `frontend/src/routes/DocsPage.tsx`, mounted at `/docs` in `App.tsx` as a plain unconditional `<Route>` — unlike `/`'s `HomeRoute`, `/docs` is public for both logged-out and logged-in visitors (no redirect logic; a docs page is useful to authenticated users too). `LandingPage.tsx`'s header gets a second link, "Docs", next to "Log in".

Single scrollable page, four anchored sections, with a sticky in-page table of contents (this is the one deliberate structural departure from `LandingPage.tsx` — a long-form document reads differently from a hero/pricing marketing page, and Hallmark's diversification rule against repeating a macrostructure applies here even within the same design-token system).

### Content — every field, header, and payload shape below is copied from the actual implementation, not invented

1. **Quickstart** — two steps: log in, then create an API key at Settings → Integrations (`/settings/integrations`, existing authenticated route — linked, not duplicated here). The full 4-step onboarding checklist already lives at `/getting-started` for authenticated users; this section is not a second copy of it.

2. **Send events** — `POST /v1/events`, auth via `X-API-Key` header (`backend/src/flowsage_backend/deps.py`'s `require_workspace_api_key`, reads `request.headers.get("X-API-Key")`). Body is a JSON array of `EventIn` objects (`backend/src/flowsage_backend/api/events.py`):

   | Field | Required | Notes |
   |---|---|---|
   | `session_id` | yes | |
   | `screen` | yes | |
   | `event` | yes | |
   | `timestamp` | yes | ISO 8601 |
   | `device` | no | defaults to `"unknown"` |
   | `cohort` | no | defaults to `"unknown"` |

   Response: `{"ingested": <n>}` (`IngestResult`). Rate limit: `120/minute` (`rate_limit.py`'s `INGEST_RATE_LIMIT`). One real curl example.

3. **Webhooks** — outbound delivery, registered at Settings → Integrations. Exactly one event type exists today, `alert.triggered` (`models/webhook.py`'s docstring: *"v1 has exactly one event type"*), fired by the digest job in `worker.py` when a calibration or churn alert is due. Real payload shape, from `worker.py`'s actual call site (`report.model_dump(mode="json")` where `report: AlertsReport`):

   ```json
   {
     "event": "alert.triggered",
     "data": {
       "calibration_alerts": [{"persona_name": "...", "screen": "...", "delta": 0.0}],
       "churn_alerts": [{"cohort": "...", "risk_score": 0.0, "top_reason": "..."}]
     }
   }
   ```

   Signature verification: header `X-FlowSage-Signature: sha256=<hex>`, computed as `hmac.new(secret, raw_body, sha256).hexdigest()` prefixed with `sha256=` (`integrations/webhooks.py`'s `deliver_webhook`). Two verification code samples (Python `hmac`/`hashlib`, Node `crypto`), both re-deriving the signature and comparing with constant-time equality.

4. **Full API reference** — a single link to `/api/docs` (FastAPI's default Swagger UI, reachable in every environment through the existing `frontend/nginx.conf` `/api/` → `backend:8000` proxy — same proxy the app's own API calls already use, nothing new to wire up).

### Testing

`frontend/src/routes/DocsPage.test.tsx` — asserts all four section headings render, and that the rendered page contains the real field names (`session_id`, `X-API-Key`, `alert.triggered`, `X-FlowSage-Signature`) so a future API change that isn't reflected here fails a test instead of silently going stale.

### Out of scope

- No new SDK — none exists; every example is raw HTTP (curl / fetch-equivalent).
- No auth-gating on `/docs`.
- No multi-page docs system / versioning / search — content is small enough for one page today.
- No change to `/getting-started`'s existing onboarding checklist.

## Verification

- `npm run typecheck && npm test && npm run build` in `frontend/` all clean.
- Manual: rebuild the `frontend` container against the already-running local deploy stack, browser-check `/docs` at desktop and 375px mobile width (same process used for the landing page), confirm the Swagger link actually opens `/api/docs` with real endpoint data.
- Cross-check every code sample against the actual backend source at implementation time (not copied verbatim from this spec), in case the API has drifted since this doc was written.
