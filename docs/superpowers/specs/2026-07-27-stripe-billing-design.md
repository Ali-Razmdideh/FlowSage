# Stripe Billing (Phase 4, item 1) — Design

**Status:** Approved
**Date:** 2026-07-27
**Scope:** Plan item "Stripe subscription tiers + freemium limits (runs/month, events/month, seats), Upgrade Plan CTA" (`plans/full-project-coding-plan.md`, Phase 4 #1).

## Context

FlowSage is currently free-to-use, single- and multi-tenant (workspaces), with no billing. This chunk adds a freemium tier ladder with hard usage caps, Stripe-hosted Checkout/Portal for upgrade/downgrade, and a webhook to keep subscription state in sync.

No live Stripe API keys are available this session. All Stripe SDK calls are built for real (not stubbed application logic) but verified only via mocked `stripe` module calls in tests — the same pattern already used for Slack/Jira in `integrations/{slack,jira}.py` (no live network calls in that test suite either).

## Tiers & Limits

Defined as code constants (`billing.py::TIER_LIMITS`), not DB rows — nothing here needs to be admin-editable yet, and hardcoding avoids a migration every time a number changes pre-launch.

| Tier | Price | Seats | Events/mo | Sim runs/mo | Workspaces owned |
|---|---|---|---|---|---|
| Free | $0 | 1 | 1,000 | 5 | 1 |
| Pro | $49/mo | 10 | 50,000 | 100 | 3 |
| Team | $199/mo | unlimited | 500,000 | 1,000 | unlimited |

"Workspaces owned" counts workspaces where the current user is the creator/admin-owner — deferred enforcement (see Out of Scope) but the constant lives alongside the others for when it's wired up.

## Data Model

New `WorkspaceSubscription` (`backend/src/flowsage_backend/models/billing.py`), one singleton row per workspace, created lazily on first access — mirrors `CalibrationSettings`'s existing per-workspace-singleton pattern exactly (see `models/settings.py`):

```python
class SubscriptionTier(str, enum.Enum):
    FREE = "free"
    PRO = "pro"
    TEAM = "team"

class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"

class WorkspaceSubscription(Base):
    __tablename__ = "workspace_subscriptions"
    id: Mapped[uuid.UUID]
    workspace_id: Mapped[uuid.UUID]  # FK workspaces.id, ondelete=CASCADE, unique index
    tier: Mapped[SubscriptionTier]  # default FREE
    status: Mapped[SubscriptionStatus]  # default ACTIVE (free tier is always "active")
    stripe_customer_id: Mapped[str | None]
    stripe_subscription_id: Mapped[str | None]
    current_period_end: Mapped[datetime | None]
    updated_at: Mapped[datetime]
```

A workspace with no row yet is treated as `tier=FREE, status=ACTIVE` by the read helper (`get_or_create_subscription`), which inserts the row on first read rather than requiring every workspace-creation code path to remember to create one.

## Usage Counting

On-demand `COUNT(*)`, no new counters/aggregates table — consistent with `calibration.py`/`churn.py`'s existing compute-on-demand philosophy:

- Events this month: `SELECT count(*) FROM events WHERE workspace_id = :ws AND timestamp >= :month_start`
- Sim runs this month: same shape against `simulation_runs.created_at`
- Seats: `COUNT(*)` on `memberships` for the workspace (no time window)

`month_start` = first of the current UTC calendar month. No rolling-30-day window — simpler, matches how Stripe's own billing-period language ("X/month") is usually read by users.

## Enforcement

`backend/src/flowsage_backend/billing.py`:
- `get_usage(session, workspace_id) -> UsageSnapshot` (pydantic: events_used, runs_used, seats_used + each resource's cap for the workspace's current tier)
- `check_within_limits(session, workspace_id, resource: Literal["events", "runs"]) -> None`, raises `HTTPException(402, detail="...")` if at/over cap for that resource

Wired in as an explicit call (not a FastAPI `Depends`, since it needs a resource-type parameter) at the top of:
- `POST /v1/events` handler, after `require_api_key` succeeds, before the row insert
- `POST /simulations` handler, after auth, before the arq job is enqueued

Seats are enforced separately at invite time (`POST /workspaces/{id}/invites` or equivalent existing membership-creation endpoint) — same `check_within_limits(..., resource="seats")` call, also 402.

## Stripe Integration

`backend/src/flowsage_backend/integrations/stripe_client.py` — thin wrapper functions, same shape as `integrations/slack.py`/`integrations/jira.py`:
- `create_checkout_session(customer_email, tier, workspace_id, success_url, cancel_url) -> str` (returns hosted Checkout URL)
- `create_portal_session(stripe_customer_id, return_url) -> str`
- `verify_webhook(payload: bytes, sig_header: str) -> stripe.Event` (wraps `stripe.Webhook.construct_event`, raises on bad signature)

`Settings` gains optional fields: `stripe_secret_key`, `stripe_webhook_secret`, `stripe_price_id_pro`, `stripe_price_id_team` — all `None`-default, no startup placeholder-guard (matches Slack/Jira, not JWT_SECRET/EVENTS_API_KEY), endpoints 400 cleanly if unconfigured.

New `api/billing.py` router, all behind `Depends(get_current_user)` except the webhook:
- `POST /billing/checkout` — body `{tier: "pro" | "team"}`, returns `{url: str}`. Looks up/creates the workspace's Stripe Customer (create on first checkout, store `stripe_customer_id`) then a Checkout Session in `subscription` mode.
- `POST /billing/portal` — returns `{url: str}`, 400 if no `stripe_customer_id` yet (nothing to manage).
- `GET /billing/usage` — returns `UsageSnapshot` for the current workspace (powers the settings page + banners).
- `POST /billing/webhook` — **no auth dependency** (Stripe calls this directly); verifies signature via `verify_webhook`, then on:
  - `checkout.session.completed` → read `subscription_id`/`customer_id`/tier from session metadata, upsert `WorkspaceSubscription` (tier, status=ACTIVE, stripe ids, current_period_end)
  - `customer.subscription.updated` → sync `status`/`current_period_end` (maps Stripe's `active`/`past_due`/`canceled`/etc. to our 3-value enum, collapsing anything not active/past_due to canceled)
  - `customer.subscription.deleted` → set `status=CANCELED`, `tier=FREE`

Webhook handler always returns 200 on a recognized-but-irrelevant event type (Stripe retries on non-2xx) and 400 only on signature failure — never lets a downstream bug (e.g. workspace not found) surface as a 5xx that triggers Stripe's retry storm; logs and swallows instead, same "best-effort, never raises" spirit as `record_audit_event()`.

## Frontend

- New `frontend/src/routes/settings/BillingSettingsPage.tsx` at `/settings/billing`: current plan name, usage bars (events/runs/seats vs. cap, color shifts at 80%/100%), "Upgrade to Pro/Team" buttons (→ `POST /billing/checkout`, redirect to returned URL), "Manage billing" button if a Stripe customer exists (→ `POST /billing/portal`). New Settings sidebar nav item, same convention as the other 4 settings pages.
- New shared `frontend/src/components/UsageLimitBanner.tsx`: renders when an API call catches a 402, shows "You've reached your Free plan's event limit — Upgrade" with a link to `/settings/billing`. Wired into the same catch blocks that already handle other API errors on Dashboard, Predictive Engine (upload/run-simulation form), and the events ingestion path's caller-facing surfaces — reuses the existing plain `useState`/`useEffect`/try-catch convention, no new state library.
- No pricing-page redesign — the plan only calls for the CTA + tier mechanics, and no design-hifi prototype exists for this (confirmed: no pricing/billing prototype in `design-hifi-prototypes/`).

## Testing

- `stripe` Python SDK calls are monkeypatched/mocked in unit tests (`unittest.mock.patch` on `stripe.checkout.Session.create` etc.), same spirit as `httpx.MockTransport` for Slack/Jira — no real network calls.
- Webhook tests construct a real signed payload using `stripe.Webhook.construct_event`'s companion signing helper with a test secret, so signature verification logic itself is exercised for real, just against a fabricated event.
- `billing.py` usage/limit unit tests seed `events`/`simulation_runs`/`memberships` rows directly (same namespaced-test-data discipline as every other test touching those tables — see the Phase 2 chunk 1 test-isolation gotcha in project memory) and assert both under-cap (200/enqueue succeeds) and at-cap (402) behavior.
- Migration gets the standard upgrade→downgrade→upgrade cycle check.
- Full `docker-compose up -d --build` pass at the end: curl `/billing/usage` unauthenticated (401) and authenticated (200, free tier defaults), curl `/billing/checkout`/`/billing/portal` with no Stripe key configured (400, matches Slack/Jira's unconfigured behavior), seed events past the free cap and confirm `POST /v1/events` returns 402, confirm the frontend banner renders on a 402 via Playwright.

## Out of Scope (deferred, noted so nothing is assumed done)

- Workspace-count-per-owner enforcement (the "workspaces owned" cap in the tier table) — no existing endpoint models "workspace ownership" distinctly from admin membership; wiring this needs its own look at the workspace-creation flow and isn't blocking the core billing loop.
- Proration UI, invoice history/download, annual billing — Stripe Customer Portal covers all of this out of the box once a customer exists, no custom UI needed.
- Usage-based (metered) billing — flat tier caps only, no per-event overage charges.
