# Self-Serve Signup — Design Spec

**Date:** 2026-08-06
**Status:** Approved for planning

## Problem

FlowSage has no public account-creation path. The only way to get a `User`/`Workspace`/`Membership` row is `flowsage-backend create-user`, a CLI command run by an operator (`seed.py::upsert_user`) — its own docstring states "no public registration endpoint." The multi-tenant workspace model (Phase 3), Stripe billing with Free/Pro/Team tiers (Phase 4), and the public landing page (Phase 4) all already exist, but every landing-page CTA points at `/login` because there is nothing to sign up *into*. This spec adds a public self-serve signup flow that creates an account, a workspace, and (optionally) starts a paid subscription, without requiring an operator in the loop.

## Scope

In scope: a public signup endpoint + page, workspace creation as part of signup, tier selection at signup time (routing into the existing Stripe Checkout flow for Pro/Team), and abuse prevention (rate limiting + CAPTCHA) on that new public surface.

Out of scope: **email verification** (no email-sending capability exists anywhere in this codebase today — confirmed by repo-wide search; building it is a separate, larger feature this spec deliberately does not bundle in — accounts are usable immediately, unverified, same trust level as today's manually-created accounts). **Token-based email invites** (`POST /workspaces/current/members` already exists and is untouched — it still requires the invitee to already have an account; that gap stays open, tracked separately). **Password reset** (still doesn't exist anywhere in this codebase; a real gap, but unrelated to signup).

## Architecture

### `POST /auth/signup` (new, public)

New route in `api/auth.py`, alongside the existing `/auth/login`/`/auth/logout`/`/auth/me`. Not behind `get_current_membership` — this is the one auth-adjacent endpoint in the app reachable with no prior session. Request body: `email`, `password`, `workspace_name`, `tier` (`free` | `pro` | `team`), `turnstile_token` (opaque widget response, absent/ignored when Turnstile isn't configured).

Handler flow, in one DB transaction (mirrors `seed.py::upsert_user`'s shape, but public-safe):

1. **CAPTCHA check** — if `settings.turnstile_secret_key` is set, POST `turnstile_token` + the caller's IP to Cloudflare's siteverify endpoint; reject with 400 on failure. If unset, skip entirely — same graceful-degrade pattern `billing.py`'s Stripe integration already uses for optional external services, so local dev and the test suite never need real Turnstile keys.
2. **Duplicate check** — 409 if a `User` with that email already exists (same email-uniqueness the DB already enforces; this just returns a clean error instead of an `IntegrityError`).
3. **Create rows** — `User` (Argon2id hash via the existing `hash_password`), `Workspace` (`name=workspace_name`, `slug=f"fs-{uuid4().hex[:8]}"`, same slug scheme `POST /workspaces` already uses), `Membership(role=Role.ADMIN)`.
4. **Auto-login** — set the session cookie exactly as `/auth/login` does (`_set_session_cookie`); signup ends already-authenticated, no separate login round-trip.
5. **Tier routing** — if `tier == "free"`, respond `{"checkout_url": null}`. Otherwise call the existing `create_checkout_session` (same call `POST /billing/checkout` already makes) for the new workspace and respond `{"checkout_url": "..."}`. No tier is written to any row at signup time either way — Free stays exactly as today's lazy default (`billing_store.get_or_create_subscription` creates the row on first real access), and Pro/Team only actually flips via the existing Stripe webhook on `checkout.session.completed`. An abandoned checkout therefore just leaves the workspace on Free — nothing to reconcile, no half-created paid state.

Frontend redirects the browser to `checkout_url` when present, otherwise straight to `/dashboard`.

### Rate limiting

New `SIGNUP_RATE_LIMIT` (default `"5/hour"`, overridable via env like `AUTH_RATE_LIMIT_OVERRIDE`) applied to `/auth/signup` via the existing shared `Limiter` + the required `resolve_signature` decorator workaround (`rate_limit.py`) that `/auth/login` already needs for the same slowapi/postponed-annotations issue. Keyed by IP (the existing `_rate_limit_key` already falls back to `get_remote_address` when there's no session/API key yet, which is always true pre-signup).

### CAPTCHA

Cloudflare Turnstile. Two new optional `Settings` fields: `turnstile_secret_key` (server-side verify, never sent to the client), `turnstile_site_key` (public, needed by the frontend widget — exposed the same way the frontend already learns its API base URL, a build-time env var). Both `None` by default, so an unconfigured deployment (and the entire test suite) never talks to Cloudflare.

## Data model

No new tables, no new columns. `User`/`Workspace`/`Membership` inserts are structurally identical to `seed.py::upsert_user`'s existing inserts — signup is a second, public-safe entry point to the same shape of write, not a new schema.

## Frontend

New public route `/signup` (`SignupPage.tsx`), same unauthenticated-shell pattern as `LoginPage.tsx`. Form fields: email, password, workspace name, a tier picker (Free/Pro/Team, reusing the tier-limit copy `LandingPage.tsx`'s pricing section already sources from `billing.py`'s `TIER_LIMITS` — no duplicated pricing text), Turnstile widget (rendered only if the frontend has a site key configured). On submit: `POST /auth/signup`, then redirect per `checkout_url` as above.

`LandingPage.tsx`'s CTAs switch from all pointing at `/login` to primary CTAs pointing at `/signup`, keeping a smaller "Log in" link for returning users.

## Error handling

- CAPTCHA failure → 400, form shows an inline error, no DB write attempted.
- Duplicate email → 409, form shows "an account with this email already exists" + a link to `/login`.
- Rate limit tripped → 429 (existing slowapi default response shape, same as login).
- Stripe checkout-session creation fails (e.g. misconfigured price ID) → the account/workspace is still created and the session cookie still set (step 3-4 already committed); respond `{"checkout_url": null}` with a non-fatal warning field rather than rolling back the whole signup — a user should never lose a created account because Stripe hiccuped. Frontend falls back to redirecting to `/dashboard` with a "you can upgrade anytime from Settings" note.

## Testing

Backend (`test_auth_api.py`, new cases): signup happy path on Free (200, cookie set, `Workspace`/`Membership` rows exist); signup with `tier=pro` returns a `checkout_url` (mock Stripe client, same injection pattern `test_billing_api.py` already uses); duplicate email → 409; N+1th request within the rate-limit window → 429; Turnstile-configured-but-invalid-token → 400 (mocked siteverify call); Turnstile unconfigured → signup proceeds (dev/test parity, no network call attempted — assert via a spy that the verify function is never called).

Frontend (`SignupPage.test.tsx`, new file): client-side field validation, submit → redirect to `/dashboard` (Free), submit → redirect to `checkout_url` (Pro/Team, mocked `api.signup`). No e2e test drives a real Turnstile challenge — same limitation this repo already accepts for real Stripe/Slack/Jira in its Playwright suite (those get curl/manual verification instead of e2e).

## Verification plan (matches this project's established pattern)

Full test suites (backend + both frontend layers) plus a real `docker-compose up -d --build` pass: curl `/auth/signup` end-to-end against live Postgres/Redis (Free path), confirm the created workspace is immediately usable (cookie from the response actually authenticates a follow-up `/auth/me` call), confirm rate-limit trips after 5 requests from one IP, and — since no live Stripe/Turnstile keys exist in this sandbox — verify the Pro/Team `checkout_url` path and the CAPTCHA-configured-reject path via mocked/unit coverage rather than a live external call, consistent with how this project has already verified Stripe webhooks (manually HMAC-signed payloads, no real Stripe account) and Slack/Jira (`httpx.MockTransport`).
