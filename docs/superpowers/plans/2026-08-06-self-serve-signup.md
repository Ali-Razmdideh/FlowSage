# Self-Serve Signup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a public `POST /auth/signup` (+ a `GET /auth/signup-config` for the CAPTCHA site key) that creates a `User` + personal `Workspace` + `ADMIN` `Membership` without operator involvement, auto-logs-in, and routes a Pro/Team tier pick into the existing Stripe Checkout flow — plus the `/signup` frontend page and landing-page CTA wiring.

**Architecture:** One new backend route pair in the existing `api/auth.py` (no new router file — signup is an auth concern, same file as login/logout/me), a new `integrations/turnstile.py` client mirroring `integrations/slack.py`'s thin-wrapper-with-injectable-transport shape, and a new `SignupPage.tsx` + `TurnstileWidget.tsx` on the frontend. No new database tables.

**Tech Stack:** FastAPI, SQLAlchemy async/Postgres, slowapi (rate limiting), httpx (Turnstile siteverify call), the existing Stripe integration, React 19 + TypeScript strict.

## Global Constraints

- `mypy --strict` clean on `backend/` after every task (this repo's CI gate); `tsc -b` + `oxlint` clean on `frontend/` after every frontend task.
- Explicitly out of scope, per the approved spec — do not add any of these: email verification, token-based email invites, password reset. Accounts created via signup are immediately usable, same trust level as an operator-created account.
- Both Turnstile and Stripe must degrade cleanly when unconfigured (`None` settings) — the entire test suite runs with both unset, and this must keep working after every task.
- Signup writes exactly the same row shapes `seed.py::upsert_user` already writes (`User`, a personal `Workspace`, an `ADMIN` `Membership`) — no new tables, no schema changes, no migration.
- Rate-limited routes need both `@resolve_signature` (directly beneath `@limiter.limit(...)`) and `@limiter.limit(...)` — `rate_limit.py`'s `resolve_signature` docstring explains why; skipping it turns every call into a 422.
- `record_audit_event` is best-effort (never raises) — call it, don't wrap it.
- A `GeneratedInsight`-style "unconfigured means skip, don't 500" pattern applies to Turnstile exactly as it already does to Stripe (`StripeNotConfiguredError` → clean 400, never an unhandled exception).

---

## Task 1: `flowsage_backend.integrations.turnstile` — Cloudflare Turnstile client

**Files:**
- Create: `backend/src/flowsage_backend/integrations/turnstile.py`
- Create: `backend/tests/test_integrations_turnstile.py`

**Interfaces:**
- Produces: `async def verify_turnstile_token(secret_key: str, token: str, remote_ip: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_integrations_turnstile.py
import httpx

from flowsage_backend.integrations.turnstile import verify_turnstile_token


async def test_verify_turnstile_token_returns_true_on_success() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(handler)
    result = await verify_turnstile_token(
        "sk_test_fake", "token-abc", "203.0.113.5", transport=transport
    )

    assert result is True
    assert captured["url"] == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    body = captured["body"]
    assert isinstance(body, bytes)
    assert b"sk_test_fake" in body
    assert b"token-abc" in body
    assert b"203.0.113.5" in body


async def test_verify_turnstile_token_returns_false_on_unsuccessful_response() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"success": False, "error-codes": ["invalid-input-response"]})
    )
    result = await verify_turnstile_token("sk_test_fake", "bad-token", transport=transport)
    assert result is False


async def test_verify_turnstile_token_returns_false_on_non_200() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="oops"))
    result = await verify_turnstile_token("sk_test_fake", "token-abc", transport=transport)
    assert result is False


async def test_verify_turnstile_token_omits_remoteip_when_not_given() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(handler)
    await verify_turnstile_token("sk_test_fake", "token-abc", transport=transport)

    body = captured["body"]
    assert isinstance(body, bytes)
    assert b"remoteip" not in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_integrations_turnstile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flowsage_backend.integrations.turnstile'`

- [ ] **Step 3: Write the module**

```python
# backend/src/flowsage_backend/integrations/turnstile.py
"""Cloudflare Turnstile CAPTCHA verification: a single POST to Cloudflare's
siteverify endpoint. No SDK needed -- mirrors `integrations/slack.py`'s shape,
including the `transport` parameter for `httpx.MockTransport` injection in
tests, so no real network call happens anywhere in this test suite.

Deliberately never raises: a network hiccup or a malformed Cloudflare
response is treated the same as a failed verification (returns `False`) so
the caller has exactly one branch to handle ("did the human check pass?"),
not a separate error path for infrastructure problems."""

from __future__ import annotations

import httpx

_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_turnstile_token(
    secret_key: str,
    token: str,
    remote_ip: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    payload = {"secret": secret_key, "response": token}
    if remote_ip is not None:
        payload["remoteip"] = remote_ip

    async with httpx.AsyncClient(transport=transport) as client:
        try:
            response = await client.post(_SITEVERIFY_URL, data=payload)
        except httpx.HTTPError:
            return False

    if response.status_code != 200:
        return False

    body = response.json()
    return bool(body.get("success", False))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_integrations_turnstile.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: mypy --strict**

Run: `cd backend && uv run mypy --strict src`
Expected: `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/integrations/turnstile.py backend/tests/test_integrations_turnstile.py
git commit -m "feat: add Cloudflare Turnstile verification client"
```

---

## Task 2: Settings + signup rate limit

**Files:**
- Modify: `backend/src/flowsage_backend/config.py`
- Modify: `backend/src/flowsage_backend/rate_limit.py`
- Modify: `backend/tests/test_rate_limit.py`
- Modify: `.env.example`

**Interfaces:**
- Produces (config.py): `Settings.turnstile_secret_key: str | None`, `Settings.turnstile_site_key: str | None`
- Produces (rate_limit.py): `SIGNUP_RATE_LIMIT: str`, `_resolve_signup_rate_limit() -> str`
- Consumes: nothing new (both files already exist)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_rate_limit.py`:

```python
def test_signup_rate_limit_override_env_var_raises_the_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flowsage_backend.rate_limit import _resolve_signup_rate_limit

    monkeypatch.setenv("SIGNUP_RATE_LIMIT_OVERRIDE", "1000/minute")
    assert _resolve_signup_rate_limit() == "1000/minute"


def test_signup_rate_limit_override_treats_empty_string_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flowsage_backend.rate_limit import _resolve_signup_rate_limit

    monkeypatch.setenv("SIGNUP_RATE_LIMIT_OVERRIDE", "")
    assert _resolve_signup_rate_limit() == "5/hour"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_rate_limit.py -v`
Expected: FAIL with `ImportError: cannot import name '_resolve_signup_rate_limit'`

- [ ] **Step 3: Add the rate limit constant**

In `backend/src/flowsage_backend/rate_limit.py`, immediately after the existing `AUTH_RATE_LIMIT = _resolve_auth_rate_limit()` / `INGEST_RATE_LIMIT` / `DEFAULT_RATE_LIMIT` block:

```python
def _resolve_signup_rate_limit() -> str:
    return os.environ.get("SIGNUP_RATE_LIMIT_OVERRIDE") or "5/hour"


SIGNUP_RATE_LIMIT = _resolve_signup_rate_limit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_rate_limit.py -v`
Expected: PASS (all tests, including the 2 new ones)

- [ ] **Step 5: Add the Turnstile settings**

In `backend/src/flowsage_backend/config.py`, add after the existing `app_base_url` field:

```python
    # Cloudflare Turnstile CAPTCHA for public signup (optional -- unconfigured
    # means POST /auth/signup skips the CAPTCHA check entirely, same
    # "unconfigured -> degrade cleanly" pattern as Stripe above; the whole
    # test suite relies on this staying unconfigured by default).
    turnstile_secret_key: str | None = None
    turnstile_site_key: str | None = None
```

Also update the now-stale comment on `jwt_secret` a few lines above (currently reads `# Single-tenant JWT session (Phase 1: one seeded user, no public signup).`) to:

```python
    # JWT session cookie. jwt_secret has no safe default for anything but local dev --
```

(drop the "no public signup" clause, which `POST /auth/signup` in Task 3 makes false; keep the rest of that comment block, including the `>= 32 bytes` note, unchanged).

- [ ] **Step 6: Run the config tests to confirm nothing broke**

Run: `cd backend && uv run pytest tests/test_config.py -v`
Expected: PASS (unchanged -- `env_ignore_empty` already applies generically to every `str | None` field, so no new test is needed for the empty-string case; `test_empty_string_env_var_treated_as_unset` already covers the mechanism via `STRIPE_SECRET_KEY`)

- [ ] **Step 7: Document the new env vars**

Append to `.env.example`, after the existing Stripe block:

```
# Cloudflare Turnstile CAPTCHA for public signup (optional). Leave unset for
# local dev: POST /auth/signup works with no CAPTCHA check at all.
# TURNSTILE_SECRET_KEY=...
# TURNSTILE_SITE_KEY=...
```

- [ ] **Step 8: mypy --strict**

Run: `cd backend && uv run mypy --strict src`
Expected: `Success: no issues found`

- [ ] **Step 9: Commit**

```bash
git add backend/src/flowsage_backend/config.py backend/src/flowsage_backend/rate_limit.py backend/tests/test_rate_limit.py .env.example
git commit -m "feat: add Turnstile settings and a signup rate limit tier"
```

---

## Task 3: `POST /auth/signup` + `GET /auth/signup-config`

**Files:**
- Modify: `backend/src/flowsage_backend/api/auth.py`
- Modify: `backend/src/flowsage_backend/seed.py` (docstring only)
- Modify: `backend/src/flowsage_backend/models/user.py` (docstring only)
- Modify: `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `verify_turnstile_token` (Task 1), `SIGNUP_RATE_LIMIT` (Task 2), `settings.turnstile_secret_key` / `settings.turnstile_site_key` (Task 2), `create_checkout_session`/`StripeNotConfiguredError` (existing `integrations/stripe_client.py`), `hash_password` (existing `security.py`), `_set_session_cookie`/`_build_me_out`/`_first_membership_or_401`-adjacent helpers already in `api/auth.py`.
- Produces: `POST /auth/signup` → `SignupResult { user: MeOut, checkout_url: str | None }`, `GET /auth/signup-config` → `SignupConfigOut { turnstile_site_key: str | None }`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_auth.py`. Add `from unittest.mock import AsyncMock, MagicMock` to the file's existing top-level imports (alongside `import uuid`), then append these tests:

```python
async def test_signup_config_returns_null_when_turnstile_unconfigured(app: FastAPI) -> None:
    async with await _client(app) as client:
        response = await client.get("/auth/signup-config")

    assert response.status_code == 200
    assert response.json() == {"turnstile_site_key": None}


async def test_signup_config_returns_site_key_when_configured(app: FastAPI) -> None:
    app.state.settings.turnstile_site_key = "1x00000000000000000000AA"

    async with await _client(app) as client:
        response = await client.get("/auth/signup-config")

    assert response.status_code == 200
    assert response.json() == {"turnstile_site_key": "1x00000000000000000000AA"}


async def test_signup_creates_account_workspace_and_sets_cookie(app: FastAPI) -> None:
    async with await _client(app) as client:
        response = await client.post(
            "/auth/signup",
            json={
                "email": "new-founder@example.com",
                "password": "hunter22222",
                "workspace_name": "Acme Inc",
                "tier": "free",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "new-founder@example.com"
    assert body["user"]["role"] == "admin"
    assert body["user"]["workspaces"][0]["name"] == "Acme Inc"
    assert body["checkout_url"] is None
    assert "flowsage_session" in response.cookies


async def test_signup_new_session_authenticates_immediately(app: FastAPI) -> None:
    async with await _client(app) as client:
        await client.post(
            "/auth/signup",
            json={
                "email": "auto-login@example.com",
                "password": "hunter22222",
                "workspace_name": "Acme Inc",
                "tier": "free",
            },
        )
        response = await client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["email"] == "auto-login@example.com"


async def test_signup_rejects_duplicate_email(app: FastAPI, db_session: AsyncSession) -> None:
    await upsert_user(db_session, "dupe@example.com", "hunter2")

    async with await _client(app) as client:
        response = await client.post(
            "/auth/signup",
            json={
                "email": "dupe@example.com",
                "password": "hunter22222",
                "workspace_name": "Someone Else",
                "tier": "free",
            },
        )

    assert response.status_code == 409


async def test_signup_rejects_short_password(app: FastAPI) -> None:
    async with await _client(app) as client:
        response = await client.post(
            "/auth/signup",
            json={
                "email": "short-pw@example.com",
                "password": "abc",
                "workspace_name": "Acme Inc",
                "tier": "free",
            },
        )

    assert response.status_code == 422


async def test_signup_skips_turnstile_check_when_unconfigured(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def spy_verify(*args: object, **kwargs: object) -> bool:
        calls.append("called")
        return True

    monkeypatch.setattr(auth_module, "verify_turnstile_token", spy_verify)

    async with await _client(app) as client:
        response = await client.post(
            "/auth/signup",
            json={
                "email": "no-captcha@example.com",
                "password": "hunter22222",
                "workspace_name": "Acme Inc",
                "tier": "free",
            },
        )

    assert response.status_code == 201
    assert calls == []


async def test_signup_rejects_missing_turnstile_token_when_configured(app: FastAPI) -> None:
    app.state.settings.turnstile_secret_key = "sk_test_fake"

    async with await _client(app) as client:
        response = await client.post(
            "/auth/signup",
            json={
                "email": "missing-token@example.com",
                "password": "hunter22222",
                "workspace_name": "Acme Inc",
                "tier": "free",
            },
        )

    assert response.status_code == 400


async def test_signup_rejects_failed_turnstile_verification(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.state.settings.turnstile_secret_key = "sk_test_fake"

    async def fake_verify(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(auth_module, "verify_turnstile_token", fake_verify)

    async with await _client(app) as client:
        response = await client.post(
            "/auth/signup",
            json={
                "email": "bad-captcha@example.com",
                "password": "hunter22222",
                "workspace_name": "Acme Inc",
                "tier": "free",
                "turnstile_token": "wrong-token",
            },
        )

    assert response.status_code == 400


async def test_signup_accepts_valid_turnstile_verification(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.state.settings.turnstile_secret_key = "sk_test_fake"

    async def fake_verify(*args: object, **kwargs: object) -> bool:
        return True

    monkeypatch.setattr(auth_module, "verify_turnstile_token", fake_verify)

    async with await _client(app) as client:
        response = await client.post(
            "/auth/signup",
            json={
                "email": "good-captcha@example.com",
                "password": "hunter22222",
                "workspace_name": "Acme Inc",
                "tier": "free",
                "turnstile_token": "right-token",
            },
        )

    assert response.status_code == 201


async def test_signup_pro_tier_returns_checkout_url(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.state.settings.stripe_secret_key = "sk_test_fake"
    app.state.settings.stripe_price_id_pro = "price_test_fake"
    fake_session = MagicMock(url="https://checkout.stripe.com/pay/cs_test_signup")
    monkeypatch.setattr(
        "stripe.checkout.Session.create_async", AsyncMock(return_value=fake_session)
    )

    async with await _client(app) as client:
        response = await client.post(
            "/auth/signup",
            json={
                "email": "pro-signup@example.com",
                "password": "hunter22222",
                "workspace_name": "Acme Inc",
                "tier": "pro",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["checkout_url"] == "https://checkout.stripe.com/pay/cs_test_signup"
    # The account must still exist and be logged-in even though the tier is paid --
    # Free is the row's actual tier until the (separately tested) webhook flips it.
    assert "flowsage_session" in response.cookies


async def test_signup_pro_tier_still_succeeds_when_stripe_unconfigured(app: FastAPI) -> None:
    async with await _client(app) as client:
        response = await client.post(
            "/auth/signup",
            json={
                "email": "pro-no-stripe@example.com",
                "password": "hunter22222",
                "workspace_name": "Acme Inc",
                "tier": "pro",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["checkout_url"] is None
    assert "flowsage_session" in response.cookies
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_auth.py -v -k signup`
Expected: FAIL with 404s (routes don't exist yet)

- [ ] **Step 3: Write the endpoint**

In `backend/src/flowsage_backend/api/auth.py`:

Update the imports at the top of the file:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

import httpx
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowsage_backend.audit import record_audit_event
from flowsage_backend.deps import get_current_membership, get_db_session
from flowsage_backend.integrations.stripe_client import (
    StripeNotConfiguredError,
    create_checkout_session,
)
from flowsage_backend.integrations.turnstile import verify_turnstile_token
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership, Role, Workspace
from flowsage_backend.rate_limit import AUTH_RATE_LIMIT, SIGNUP_RATE_LIMIT, limiter, resolve_signature
from flowsage_backend.security import (
    create_access_token,
    dummy_password_hash,
    hash_password,
    verify_password,
)
```

Add the new request/response models, near the existing `LoginRequest`/`SwitchWorkspaceRequest`/`WorkspaceSummary`/`MeOut`:

```python
class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    workspace_name: str = Field(min_length=1, max_length=200)
    tier: Literal["free", "pro", "team"]
    turnstile_token: str | None = None


class SignupResult(BaseModel):
    user: MeOut
    checkout_url: str | None


class SignupConfigOut(BaseModel):
    turnstile_site_key: str | None
```

Add the two routes at the end of the file (after `switch_workspace`):

```python
@router.get("/signup-config", response_model=SignupConfigOut)
async def signup_config(request: Request) -> SignupConfigOut:
    settings = request.app.state.settings
    return SignupConfigOut(turnstile_site_key=settings.turnstile_site_key)


@router.post("/signup", response_model=SignupResult, status_code=status.HTTP_201_CREATED)
@resolve_signature
@limiter.limit(SIGNUP_RATE_LIMIT)
async def signup(
    payload: SignupRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> SignupResult:
    settings = request.app.state.settings
    client_ip = request.client.host if request.client else None

    if settings.turnstile_secret_key is not None:
        if payload.turnstile_token is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "CAPTCHA verification is required")
        verified = await verify_turnstile_token(
            settings.turnstile_secret_key, payload.turnstile_token, client_ip
        )
        if not verified:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "CAPTCHA verification failed")

    existing = await session.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")

    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    session.add(user)
    await session.flush()

    workspace = Workspace(name=payload.workspace_name, slug=f"fs-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.flush()

    membership = Membership(user_id=user.id, workspace_id=workspace.id, role=Role.ADMIN)
    session.add(membership)
    await session.commit()
    await session.refresh(membership)

    _set_session_cookie(response, request, user.id, workspace.id)
    await record_audit_event(
        session,
        workspace.id,
        actor_user_id=user.id,
        action="auth.signup",
        extra_data={"tier": payload.tier},
        ip_address=client_ip,
    )

    checkout_url: str | None = None
    if payload.tier != "free":
        price_id = (
            settings.stripe_price_id_pro if payload.tier == "pro" else settings.stripe_price_id_team
        )
        try:
            checkout_url = await create_checkout_session(
                secret_key=settings.stripe_secret_key,
                price_id=price_id,
                customer_email=user.email,
                existing_customer_id=None,
                workspace_id=workspace.id,
                tier=payload.tier,
                success_url=f"{settings.app_base_url}/settings/billing?checkout=success",
                cancel_url=f"{settings.app_base_url}/settings/billing?checkout=cancel",
            )
        except (StripeNotConfiguredError, stripe.StripeError):
            # A brand-new account must never be lost because Stripe hiccuped or
            # isn't configured -- the account/workspace above are already
            # committed. The frontend falls back to "you can upgrade anytime
            # from Settings" when checkout_url comes back null.
            checkout_url = None

    me_out = await _build_me_out(session, user, membership)
    return SignupResult(user=me_out, checkout_url=checkout_url)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_auth.py -v -k signup`
Expected: PASS (all new signup tests)

- [ ] **Step 5: Run the full auth + rate-limit + billing test files to confirm no regressions**

Run: `cd backend && uv run pytest tests/test_auth.py tests/test_rate_limit.py tests/test_billing_api.py tests/test_integrations_stripe.py -v`
Expected: PASS (everything, including the pre-existing tests)

- [ ] **Step 6: Fix the two stale "no public signup" docstrings**

In `backend/src/flowsage_backend/models/user.py`, replace the module docstring:

```python
"""A FlowSage account. Created either via the `flowsage-backend create-user`
CLI (operator-provisioned, used for the pilot/ops path) or via the public
`POST /auth/signup` (self-serve, creates its own personal workspace)."""
```

In `backend/src/flowsage_backend/seed.py`, replace the module docstring:

```python
"""Seed data: an operator-provisioned admin user (bootstrapped with a personal
workspace), and the 5 baseline personas.

`upsert_user` below is also the model `POST /auth/signup` follows for the
public self-serve path (`api/auth.py::signup`) -- same three-row shape (user +
personal workspace + admin membership), just reachable without the CLI. The
CLI stays useful for operator-provisioned pilot accounts and password resets
(there's still no self-serve password reset).
"""
```

- [ ] **Step 7: mypy --strict + autoflake8 + black**

Run: `cd backend && uv run autoflake8 --in-place --remove-all-unused-imports -r src tests && uv run black src tests && uv run mypy --strict src`
Expected: `Success: no issues found`, clean formatting diff (or no diff)

- [ ] **Step 8: Full backend suite**

Run: `cd backend && uv run pytest -q`
Expected: all tests pass, no regressions anywhere else in the suite

- [ ] **Step 9: Commit**

```bash
git add backend/src/flowsage_backend/api/auth.py backend/src/flowsage_backend/models/user.py backend/src/flowsage_backend/seed.py backend/tests/test_auth.py
git commit -m "feat: add public POST /auth/signup and GET /auth/signup-config"
```

---

## Task 4: Frontend — signup plumbing + `SignupPage`

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/auth/AuthContext.ts`
- Modify: `frontend/src/auth/AuthProvider.tsx`
- Modify: `frontend/src/routes/LoginPage.test.tsx` (add `signup: vi.fn()` to the default `AuthState`)
- Modify: `frontend/src/routes/settings/BillingSettingsPage.test.tsx` (same)
- Modify: `frontend/src/App.test.tsx` (same)
- Create: `frontend/src/components/TurnstileWidget.tsx`
- Create: `frontend/src/routes/SignupPage.tsx`
- Create: `frontend/src/routes/SignupPage.test.tsx`

**Interfaces:**
- Consumes: `api.request` (existing internal helper in `api.ts`), `AuthContext`/`useAuth` (existing)
- Produces: `SignupConfig`, `SignupPayload`, `SignupResult` types; `api.getSignupConfig(): Promise<SignupConfig>`; `api.signup(payload: SignupPayload): Promise<SignupResult>`; `AuthState.signup: (payload: SignupPayload) => Promise<{ checkoutUrl: string | null }>`; `TurnstileWidget({ siteKey: string; onToken: (token: string | null) => void })`; `SignupPage` (default export via named export, matching `LoginPage`'s convention)

- [ ] **Step 1: Add the new types**

In `frontend/src/lib/types.ts`, insert after the existing `ScheduledSimulationUpdatePayload` interface (alphabetically just before `SimulationRun`):

```ts
export interface SignupConfig {
  turnstile_site_key: string | null;
}

export interface SignupPayload {
  email: string;
  password: string;
  workspace_name: string;
  tier: "free" | "pro" | "team";
  turnstile_token?: string;
}

export interface SignupResult {
  user: User;
  checkout_url: string | null;
}
```

- [ ] **Step 2: Add the API client methods**

In `frontend/src/lib/api.ts`, add `SignupConfig`, `SignupPayload`, `SignupResult` to the type-import list at the top (alphabetically, same spot as in `types.ts`), then add near `login`/`logout`/`me`:

```ts
  getSignupConfig: (): Promise<SignupConfig> => request<SignupConfig>("/auth/signup-config"),

  signup: (payload: SignupPayload): Promise<SignupResult> =>
    request<SignupResult>("/auth/signup", { method: "POST", body: JSON.stringify(payload) }),
```

- [ ] **Step 3: Add `signup` to `AuthState` and `AuthProvider`**

In `frontend/src/auth/AuthContext.ts`:

```ts
import { createContext, useContext } from "react";
import type { SignupPayload, User } from "../lib/types";

export interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  switchWorkspace: (workspaceId: string) => Promise<void>;
  signup: (payload: SignupPayload) => Promise<{ checkoutUrl: string | null }>;
}

export const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
```

In `frontend/src/auth/AuthProvider.tsx`, add the `signup` callback and wire it into the memoized value:

```tsx
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import type { SignupPayload, User } from "../lib/types";
import { AuthContext, type AuthState } from "./AuthContext";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const loggedInUser = await api.login(email, password);
    setUser(loggedInUser);
  }, []);

  const logout = useCallback(async () => {
    await api.logout();
    setUser(null);
  }, []);

  const switchWorkspace = useCallback(async (workspaceId: string) => {
    const updatedUser = await api.switchWorkspace(workspaceId);
    setUser(updatedUser);
  }, []);

  const signup = useCallback(async (payload: SignupPayload) => {
    const result = await api.signup(payload);
    setUser(result.user);
    return { checkoutUrl: result.checkout_url };
  }, []);

  const value = useMemo<AuthState>(
    () => ({ user, loading, login, logout, switchWorkspace, signup }),
    [user, loading, login, logout, switchWorkspace, signup],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
```

- [ ] **Step 4: Fix the 3 test files that construct `AuthState` literals**

In `frontend/src/routes/LoginPage.test.tsx`, `frontend/src/routes/settings/BillingSettingsPage.test.tsx`, and `frontend/src/App.test.tsx`, add `signup: vi.fn(),` to each `AuthState` object literal (each currently lists `login`/`logout`/`switchWorkspace` — add `signup` as a 4th `vi.fn()` line in the same spot).

- [ ] **Step 5: Run the frontend suite to confirm the plumbing compiles and nothing broke**

Run: `cd frontend && npx tsc -b && npx vitest run`
Expected: `tsc -b` clean; all existing tests still pass (no new tests yet)

- [ ] **Step 6: Write the failing `TurnstileWidget` + `SignupPage` tests**

```tsx
// frontend/src/routes/SignupPage.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthContext, type AuthState } from "../auth/AuthContext";
import { api } from "../lib/api";
import { SignupPage } from "./SignupPage";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getSignupConfig: vi.fn(),
    },
  };
});

function renderWithAuth(overrides: Partial<AuthState> = {}, initialPath = "/signup") {
  const state: AuthState = {
    user: null,
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    switchWorkspace: vi.fn(),
    signup: vi.fn(),
    ...overrides,
  };
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthContext.Provider value={state}>
        <SignupPage />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
  return state;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SignupPage", () => {
  it("does not render a CAPTCHA widget when Turnstile is unconfigured", async () => {
    vi.mocked(api.getSignupConfig).mockResolvedValue({ turnstile_site_key: null });
    renderWithAuth();

    await waitFor(() => expect(api.getSignupConfig).toHaveBeenCalled());
    expect(screen.queryByTestId("turnstile-widget")).not.toBeInTheDocument();
  });

  it("signs up on Free and lets the caller redirect to the dashboard", async () => {
    vi.mocked(api.getSignupConfig).mockResolvedValue({ turnstile_site_key: null });
    const state = renderWithAuth({
      signup: vi.fn().mockResolvedValue({ checkoutUrl: null }),
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Workspace name"), "Acme Inc");
    await user.type(screen.getByLabelText("Email"), "founder@acme.example");
    await user.type(screen.getByLabelText("Password"), "supersecret123");
    await user.click(screen.getByRole("button", { name: /sign up/i }));

    await waitFor(() => {
      expect(state.signup).toHaveBeenCalledWith({
        email: "founder@acme.example",
        password: "supersecret123",
        workspace_name: "Acme Inc",
        tier: "free",
        turnstile_token: undefined,
      });
    });
  });

  it("redirects to Stripe Checkout when a paid tier is selected", async () => {
    vi.mocked(api.getSignupConfig).mockResolvedValue({ turnstile_site_key: null });
    renderWithAuth({
      signup: vi.fn().mockResolvedValue({
        checkoutUrl: "https://checkout.stripe.com/pay/cs_test_123",
      }),
    });
    const assignMock = vi.fn();
    Object.defineProperty(window, "location", { value: { assign: assignMock }, writable: true });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Workspace name"), "Acme Inc");
    await user.type(screen.getByLabelText("Email"), "founder@acme.example");
    await user.type(screen.getByLabelText("Password"), "supersecret123");
    await user.click(screen.getByLabelText(/Pro/));
    await user.click(screen.getByRole("button", { name: /sign up/i }));

    await waitFor(() => {
      expect(assignMock).toHaveBeenCalledWith("https://checkout.stripe.com/pay/cs_test_123");
    });
  });

  it("pre-selects the tier from the ?tier= query param", async () => {
    vi.mocked(api.getSignupConfig).mockResolvedValue({ turnstile_site_key: null });
    renderWithAuth({}, "/signup?tier=pro");

    await waitFor(() => expect(api.getSignupConfig).toHaveBeenCalled());
    expect(screen.getByLabelText(/Pro/)).toBeChecked();
  });

  it("shows the error message when signup rejects", async () => {
    vi.mocked(api.getSignupConfig).mockResolvedValue({ turnstile_site_key: null });
    renderWithAuth({
      signup: vi.fn().mockRejectedValue(new Error("An account with this email already exists")),
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Workspace name"), "Acme Inc");
    await user.type(screen.getByLabelText("Email"), "founder@acme.example");
    await user.type(screen.getByLabelText("Password"), "supersecret123");
    await user.click(screen.getByRole("button", { name: /sign up/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong.");
  });

  it("redirects away from the signup form when already authenticated", () => {
    renderWithAuth({
      user: {
        id: "u1",
        email: "admin@flowsage.dev",
        created_at: "now",
        workspace_id: "w1",
        role: "admin",
        workspaces: [{ id: "w1", name: "Workspace 1" }],
      },
    });

    expect(screen.queryByRole("button", { name: /sign up/i })).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/routes/SignupPage.test.tsx`
Expected: FAIL with a module-not-found error for `./SignupPage`

- [ ] **Step 8: Write `TurnstileWidget`**

```tsx
// frontend/src/components/TurnstileWidget.tsx
import { useEffect, useRef, useState } from "react";

declare global {
  interface Window {
    turnstile?: {
      render: (
        container: HTMLElement,
        options: {
          sitekey: string;
          callback: (token: string) => void;
          "expired-callback"?: () => void;
        },
      ) => string;
      remove: (widgetId: string) => void;
    };
  }
}

const SCRIPT_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js";

export function TurnstileWidget({
  siteKey,
  onToken,
}: {
  siteKey: string;
  onToken: (token: string | null) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [scriptReady, setScriptReady] = useState(window.turnstile !== undefined);

  useEffect(() => {
    if (scriptReady) return;
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${SCRIPT_SRC}"]`);
    const markReady = () => setScriptReady(true);
    if (existing) {
      existing.addEventListener("load", markReady);
      return () => existing.removeEventListener("load", markReady);
    }
    const script = document.createElement("script");
    script.src = SCRIPT_SRC;
    script.async = true;
    script.addEventListener("load", markReady);
    document.head.appendChild(script);
    return () => script.removeEventListener("load", markReady);
  }, [scriptReady]);

  useEffect(() => {
    if (!scriptReady || containerRef.current === null || window.turnstile === undefined) return;
    const widgetId = window.turnstile.render(containerRef.current, {
      sitekey: siteKey,
      callback: onToken,
      "expired-callback": () => onToken(null),
    });
    return () => window.turnstile?.remove(widgetId);
  }, [scriptReady, siteKey, onToken]);

  return <div ref={containerRef} data-testid="turnstile-widget" />;
}
```

- [ ] **Step 9: Write `SignupPage`**

```tsx
// frontend/src/routes/SignupPage.tsx
import { useEffect, useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { TurnstileWidget } from "../components/TurnstileWidget";
import { useAuth } from "../auth/AuthContext";
import { ApiError, api } from "../lib/api";

type Tier = "free" | "pro" | "team";

const TIERS: { value: Tier; label: string; price: string }[] = [
  { value: "free", label: "Free", price: "$0/mo" },
  { value: "pro", label: "Pro", price: "$49/mo" },
  { value: "team", label: "Team", price: "$199/mo" },
];

function parseTier(raw: string | null): Tier {
  return raw === "pro" || raw === "team" ? raw : "free";
}

export function SignupPage() {
  const { user, signup } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [workspaceName, setWorkspaceName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tier, setTier] = useState<Tier>(() => parseTier(searchParams.get("tier")));
  const [turnstileSiteKey, setTurnstileSiteKey] = useState<string | null>(null);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api
      .getSignupConfig()
      .then((config) => setTurnstileSiteKey(config.turnstile_site_key))
      .catch(() => setTurnstileSiteKey(null));
  }, []);

  if (user !== null) {
    return <Navigate to="/dashboard" replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const { checkoutUrl } = await signup({
        email,
        password,
        workspace_name: workspaceName,
        tier,
        turnstile_token: turnstileToken ?? undefined,
      });
      if (checkoutUrl !== null) {
        window.location.assign(checkoutUrl);
      } else {
        navigate("/dashboard", { replace: true });
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <p className="font-headline text-3xl text-primary text-center mb-1">FlowSage</p>
        <p className="font-label text-xs uppercase tracking-wide text-on-surface-variant text-center mb-8">
          Create your workspace
        </p>

        <form
          onSubmit={(event) => void handleSubmit(event)}
          className="bg-surface-container-lowest rounded-xl p-8 shadow-sm flex flex-col gap-4"
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Workspace name</span>
            <input
              type="text"
              required
              value={workspaceName}
              onChange={(event) => setWorkspaceName(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2 focus:outline-2 focus:outline-primary"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Email</span>
            <input
              type="email"
              required
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2 focus:outline-2 focus:outline-primary"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Password</span>
            <input
              type="password"
              required
              minLength={8}
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2 focus:outline-2 focus:outline-primary"
            />
          </label>

          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm text-on-surface-variant mb-1">Plan</legend>
            {TIERS.map((t) => (
              <label key={t.value} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="tier"
                  value={t.value}
                  checked={tier === t.value}
                  onChange={() => setTier(t.value)}
                />
                {t.label} — {t.price}
              </label>
            ))}
          </fieldset>

          {turnstileSiteKey !== null ? (
            <TurnstileWidget siteKey={turnstileSiteKey} onToken={setTurnstileToken} />
          ) : null}

          {error !== null ? (
            <p role="alert" className="text-sm text-error">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={submitting}
            className="mt-2 rounded-lg bg-primary py-2.5 text-on-primary font-medium hover:opacity-90 transition disabled:opacity-50"
          >
            {submitting ? "Creating your workspace…" : "Sign up"}
          </button>

          <p className="text-center text-sm text-on-surface-variant">
            Already have an account?{" "}
            <Link to="/login" className="text-primary hover:opacity-90">
              Log in
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/routes/SignupPage.test.tsx`
Expected: PASS (all 6 tests)

- [ ] **Step 11: Full frontend verification**

Run: `cd frontend && npx tsc -b && npx oxlint && npx vitest run`
Expected: all clean, no regressions

- [ ] **Step 12: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/auth/AuthContext.ts frontend/src/auth/AuthProvider.tsx frontend/src/routes/LoginPage.test.tsx frontend/src/routes/settings/BillingSettingsPage.test.tsx frontend/src/App.test.tsx frontend/src/components/TurnstileWidget.tsx frontend/src/routes/SignupPage.tsx frontend/src/routes/SignupPage.test.tsx
git commit -m "feat: add self-serve signup page, Turnstile widget, and auth plumbing"
```

---

## Task 5: Route wiring + landing page CTAs

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/routes/LandingPage.tsx`
- Modify: `frontend/src/routes/LandingPage.test.tsx`

**Interfaces:**
- Consumes: `SignupPage` (Task 4)

- [ ] **Step 1: Write the failing test**

Replace the second `it` block in `frontend/src/routes/LandingPage.test.tsx` (`"renders all three pricing tiers with correct limits and links each to /login"`) with:

```tsx
  it("renders all three pricing tiers with correct limits, and links the header to /login and every CTA to /signup", () => {
    render(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>,
    );
    expect(screen.getByText(/1,000 events\/mo/i)).toBeInTheDocument();
    expect(screen.getByText(/\$49/)).toBeInTheDocument();
    expect(screen.getByText(/50,000 events\/mo/i)).toBeInTheDocument();
    expect(screen.getByText(/\$199/)).toBeInTheDocument();
    expect(screen.getByText(/500,000 events\/mo/i)).toBeInTheDocument();

    const loginLinks = screen.getAllByRole("link", { name: /log in/i });
    expect(loginLinks).toHaveLength(1);
    expect(loginLinks[0]).toHaveAttribute("href", "/login");

    const signupLinks = screen.getAllByRole("link", { name: /get started/i });
    expect(signupLinks.length).toBeGreaterThanOrEqual(4); // hero + 3 pricing cards
    for (const link of signupLinks) {
      expect(link.getAttribute("href")).toMatch(/^\/signup/);
    }
  });
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/routes/LandingPage.test.tsx`
Expected: FAIL (there's currently only 1 "get started"-named link, or none, and 5 "log in" links)

- [ ] **Step 3: Wire the `/signup` route**

In `frontend/src/App.tsx`, add the import and route next to `/login`:

```tsx
import { SignupPage } from "./routes/SignupPage";
```

```tsx
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
```

- [ ] **Step 4: Update the landing page CTAs**

In `frontend/src/routes/LandingPage.tsx`, leave the header's "Log in" link exactly as-is (that's the "smaller link for returning users"). Change the hero CTA:

```tsx
        <Link
          to="/signup"
          className="mt-10 inline-block rounded-lg bg-primary px-8 py-3 font-medium text-on-primary hover:opacity-90 transition"
        >
          Get started free
        </Link>
```

Change each of the 3 pricing-card CTAs from `to="/login"` / `Log in` to a tier-specific signup link, keeping each card's existing className unchanged:

```tsx
            <Link
              to="/signup?tier=free"
              className="mt-6 rounded-lg bg-surface-container px-4 py-2 text-center text-sm font-medium text-primary hover:opacity-90 transition"
            >
              Get started
            </Link>
```

```tsx
            <Link
              to="/signup?tier=pro"
              className="mt-6 rounded-lg bg-primary px-4 py-2 text-center text-sm font-medium text-on-primary hover:opacity-90 transition"
            >
              Get started
            </Link>
```

```tsx
            <Link
              to="/signup?tier=team"
              className="mt-6 rounded-lg bg-surface-container px-4 py-2 text-center text-sm font-medium text-primary hover:opacity-90 transition"
            >
              Get started
            </Link>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/routes/LandingPage.test.tsx src/App.test.tsx`
Expected: PASS

- [ ] **Step 6: Full frontend verification**

Run: `cd frontend && npx tsc -b && npx oxlint && npx vitest run`
Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx frontend/src/routes/LandingPage.tsx frontend/src/routes/LandingPage.test.tsx
git commit -m "feat: wire /signup route and point landing-page CTAs at it"
```

---

## Task 6: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Full backend suite**

Run: `cd backend && uv run autoflake8 --check -r src tests && uv run black --check src tests && uv run mypy --strict src && uv run pytest -q`
Expected: all clean, all tests pass (baseline was 317 before this feature)

- [ ] **Step 2: Full frontend suite**

Run: `cd frontend && npx tsc -b && npx oxlint && npx vitest run`
Expected: all clean, all tests pass (baseline was 78 before this feature)

- [ ] **Step 3: flowsage-predict and flowsage-graph suites (sanity check nothing cross-package broke)**

Run: `cd scripts/flowsage-predict && uv run pytest -q && cd ../flowsage-graph && uv run pytest -q`
Expected: 40 and 25 passing respectively, unchanged

- [ ] **Step 4: Live `docker-compose` smoke test**

Bring up the full stack (`docker compose -f infra/docker-compose.yml up -d --build`), then against the real running backend:
- `curl http://localhost:8000/auth/signup-config` → `{"turnstile_site_key": null}` (unconfigured in this environment)
- `curl -i -X POST http://localhost:8000/auth/signup -H 'Content-Type: application/json' -d '{"email":"smoke-test@example.com","password":"hunter22222","workspace_name":"Smoke Test Inc","tier":"free"}'` → 201, a `Set-Cookie: flowsage_session=...` header, `checkout_url: null`
- Re-use that cookie on `curl -i http://localhost:8000/auth/me` → 200, confirms the session from signup actually authenticates
- Repeat the signup POST 5 more times rapidly from the same IP → confirm a 429 shows up (signup rate limit)
- Drive `/signup` in a real browser (Chrome DevTools MCP or Playwright, whichever is available this session): fill the form, submit on Free, confirm redirect to `/dashboard`; separately confirm the pricing-card "Get started" links on `/` land on `/signup` with the right tier radio pre-selected
- Tear the stack down afterward (`docker compose -f infra/docker-compose.yml down`)

- [ ] **Step 5: Update project memory and push**

Update the `project_build_status` memory with what shipped this session, then `git push origin main` (per this project's standing workflow rule: commit + push every big change immediately).
