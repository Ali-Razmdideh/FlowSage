# Phase 3 chunk 3: Security Hardening (audit log, rate limiting, secrets encryption, retention purge) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-workspace audit log with a `/settings/security` view, Redis-backed rate
limiting on auth/ingestion/general routes, Fernet encryption at rest for the two plaintext
secret columns (`JiraIntegration.api_token`, `Webhook.secret`), and a daily retention-purge
job that finally enforces the long-dormant `Workspace.retention_days` field.

**Architecture:** `audit.py` is a new pure-function module (mirrors `calibration.py`/`churn.py`'s
compute-on-demand shape) called inline from existing route handlers — no event bus. `crypto.py`
adds a SQLAlchemy `TypeDecorator` so encryption is transparent at the ORM boundary; no
service-layer code that already reads `.api_token`/`.secret` needs to change. `rate_limit.py`
wraps `slowapi.Limiter` with a Redis backend (reuses `settings.redis_url`, no new infra).
Retention purge is one more arq cron job, following `run_digest_job`'s
iterate-every-workspace-independently shape.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, `slowapi` (new), `cryptography`'s
Fernet (new), arq, React 19 + TypeScript, Vitest, Playwright.

## Global Constraints

- No backwards-compat shim: the plaintext `api_token`/`secret` columns are widened and
  switched to store ciphertext directly; this is a fresh local dev stack with no production
  data to migrate (per the spec's explicit scope note).
- Every new/modified table stays `workspace_id`-scoped and gets a cross-tenant isolation test,
  matching every prior chunk's convention (`backend/tests/test_workspace_isolation.py`).
- Mutating audit-adjacent actions keep whatever role requirement they already have today
  (e.g. `require_role(Role.ADMIN)` on member/integration/webhook mutations) — this chunk adds
  logging, it does not change authorization.
- `SECRET_ENCRYPTION_KEY` is placeholder-guarded outside `environment=development`, exactly
  like `JWT_SECRET` today (`Settings._reject_placeholder_secret_outside_dev`).
- Backend: `cd backend && uv run pytest -q && uv run mypy --strict src/` must stay green after
  every task. Frontend: `cd frontend && npm run typecheck && npm run test && npm run lint`
  must stay green after every frontend task.
- Full spec: `docs/superpowers/specs/2026-07-24-security-hardening-design.md`.

---

## File Structure

**Backend — create:**
- `backend/src/flowsage_backend/crypto.py` — `EncryptedString` TypeDecorator, Fernet helpers
- `backend/src/flowsage_backend/models/audit_log.py` — `AuditLog`
- `backend/src/flowsage_backend/audit.py` — `record_audit_event()`, `list_audit_logs()`
- `backend/src/flowsage_backend/api/audit.py` — `GET /audit-logs`
- `backend/src/flowsage_backend/rate_limit.py` — `Limiter` + 3 keyed decorators + exception handler
- `backend/migrations/versions/<rev>_add_audit_logs_and_encrypt_secrets.py`
- `backend/tests/test_crypto.py`
- `backend/tests/test_audit.py`
- `backend/tests/test_audit_api.py`
- `backend/tests/test_rate_limit.py`
- `backend/tests/test_retention_purge.py`

**Backend — modify:**
- `backend/src/flowsage_backend/config.py` — `secret_encryption_key` field + guard
- `backend/src/flowsage_backend/models/__init__.py` — export `AuditLog`
- `backend/src/flowsage_backend/models/integration.py` — `JiraIntegration.api_token` → `EncryptedString`
- `backend/src/flowsage_backend/models/webhook.py` — `Webhook.secret` → `EncryptedString`
- `backend/src/flowsage_backend/main.py` — wire `Limiter` into `create_app()`, register `audit_router`
- `backend/src/flowsage_backend/api/auth.py` — audit `login`/`logout`; rate-limit `/auth/login`
- `backend/src/flowsage_backend/api/workspaces.py` — audit invite/role-change/remove/archive
- `backend/src/flowsage_backend/api/integrations.py` — audit API key create/revoke, Slack/Jira connect/disconnect, webhook create/delete
- `backend/src/flowsage_backend/api/personas.py` — audit persona create/delete
- `backend/src/flowsage_backend/api/simulations.py` — audit simulation run start
- `backend/src/flowsage_backend/api/settings.py` — audit calibration settings change
- `backend/src/flowsage_backend/api/events.py` — rate-limit `POST /v1/events`
- `backend/src/flowsage_backend/worker.py` — `run_retention_purge_job` cron entry
- `backend/pyproject.toml` — add `slowapi`, `cryptography`
- `backend/tests/conftest.py` — `create_workspace_and_admin` helper reused by new test files (see Task 4)
- `backend/tests/test_workspace_isolation.py` — add audit-log isolation case

**Frontend — create:**
- `frontend/src/routes/settings/SecurityLogsPage.tsx`
- `frontend/src/routes/settings/SecurityLogsPage.test.tsx`
- `frontend/e2e/security-logs.spec.ts`

**Frontend — modify:**
- `frontend/src/lib/types.ts` — `AuditLogEntry`, `AuditLogPage`
- `frontend/src/lib/api.ts` — `getAuditLogs()`
- `frontend/src/components/Sidebar.tsx` — "Security" nav entry
- `frontend/src/App.tsx` — `/settings/security` route

---

### Task 1: `SECRET_ENCRYPTION_KEY` setting + `crypto.py`

**Files:**
- Modify: `backend/src/flowsage_backend/config.py`
- Modify: `backend/pyproject.toml`
- Create: `backend/src/flowsage_backend/crypto.py`
- Test: `backend/tests/test_crypto.py`

**Interfaces:**
- Produces: `encrypt(plaintext: str, key: str) -> str`, `decrypt(ciphertext: str, key: str) -> str`
  (raises `cryptography.fernet.InvalidToken` on tamper/wrong key), `EncryptedString(TypeDecorator)`
  — a SQLAlchemy column type bound to `Settings.secret_encryption_key` at engine-creation time via
  a module-level `_current_key: ContextVar[str]`-free approach: the decorator takes the key as a
  constructor argument (`EncryptedString(key_provider=get_settings)`), not a global, so tests can
  use a distinct key per `Settings` instance without cross-test leakage.
- Consumes: `Settings.secret_encryption_key: str` (new field, this task).

- [ ] **Step 1: Add the dependency**

Edit `backend/pyproject.toml`, in the `dependencies` list (after `"httpx>=0.27,<0.28,"`):

```toml
    "httpx>=0.27,<0.28",
    "cryptography>=43.0,<44.0",
    "slowapi>=0.1.9,<0.2",
    "flowsage-predict",
```

Run: `cd backend && uv sync --all-extras` from the **repo root** (per the workspace gotcha in
project memory — running `uv sync` from inside `backend/` prunes other workspace members'
deps from the shared venv). Expected: resolves and installs `cryptography` + `slowapi` (and
`limits`, slowapi's rate-limiting engine) with no conflicts.

- [ ] **Step 2: Add `secret_encryption_key` to `Settings` + extend the placeholder guard**

Edit `backend/src/flowsage_backend/config.py`:

```python
_PLACEHOLDER_JWT_SECRET = "dev-secret-change-me-before-deploying-32bytes"
_PLACEHOLDER_ENCRYPTION_KEY = "dev-encryption-key-change-me-before-deploy"
```

Add a field after `jwt_algorithm`:

```python
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24 * 7  # 1 week
    cookie_name: str = "flowsage_session"
    cookie_secure: bool = False  # set True once served over HTTPS

    # Encrypts JiraIntegration.api_token / Webhook.secret at rest (crypto.py's
    # EncryptedString). Must be a 32-byte urlsafe-base64 Fernet key, e.g.
    # `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
    secret_encryption_key: str = _PLACEHOLDER_ENCRYPTION_KEY
```

Extend the validator:

```python
    @model_validator(mode="after")
    def _reject_placeholder_secret_outside_dev(self) -> "Settings":
        if self.environment == "development":
            return self
        placeholders = {
            "JWT_SECRET": self.jwt_secret == _PLACEHOLDER_JWT_SECRET,
            "SECRET_ENCRYPTION_KEY": self.secret_encryption_key == _PLACEHOLDER_ENCRYPTION_KEY,
        }
        still_placeholder = [name for name, is_default in placeholders.items() if is_default]
        if still_placeholder:
            raise ValueError(
                f"{', '.join(still_placeholder)} still set to the dev placeholder but "
                f"ENVIRONMENT is {self.environment!r} -- set real secrets "
                "(e.g. `openssl rand -hex 32`, or for SECRET_ENCRYPTION_KEY: "
                "`python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\"`)."
            )
        return self
```

- [ ] **Step 3: Write the failing crypto test**

```python
# backend/tests/test_crypto.py
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet, InvalidToken

from flowsage_backend.crypto import decrypt, derive_fernet_key, encrypt


def test_encrypt_decrypt_round_trips() -> None:
    key = derive_fernet_key("a-passphrase-not-a-real-fernet-key")
    ciphertext = encrypt("super-secret-token", key)
    assert ciphertext != "super-secret-token"
    assert decrypt(ciphertext, key) == "super-secret-token"


def test_decrypt_rejects_tampered_ciphertext() -> None:
    key = derive_fernet_key("a-passphrase-not-a-real-fernet-key")
    ciphertext = encrypt("super-secret-token", key)
    tampered = ciphertext[:-4] + ("A" if ciphertext[-4] != "A" else "B") + ciphertext[-3:]
    with pytest.raises(InvalidToken):
        decrypt(tampered, key)


def test_decrypt_rejects_wrong_key() -> None:
    ciphertext = encrypt("super-secret-token", derive_fernet_key("key-one"))
    with pytest.raises(InvalidToken):
        decrypt(ciphertext, derive_fernet_key("key-two"))
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_crypto.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flowsage_backend.crypto'`

- [ ] **Step 5: Implement `crypto.py`**

```python
# backend/src/flowsage_backend/crypto.py
"""Encryption at rest for secret columns (`JiraIntegration.api_token`,
`Webhook.secret`). `Settings.secret_encryption_key` is an arbitrary string (not
required to already be a valid Fernet key) -- `derive_fernet_key` stretches it
into one via SHA-256, so operators can set a plain passphrase env var the same
way `JWT_SECRET` works today, without needing to run a key-generation command
first."""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Callable

from cryptography.fernet import Fernet
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator


def derive_fernet_key(passphrase: str) -> bytes:
    digest = hashlib.sha256(passphrase.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt(plaintext: str, key: bytes) -> str:
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str, key: bytes) -> str:
    return Fernet(key).decrypt(ciphertext.encode("ascii")).decode("utf-8")


class EncryptedString(TypeDecorator[str]):
    """Transparently encrypts on write / decrypts on read. `key_provider` is
    called lazily on each bind/result (not once at class-definition time) so
    tests can swap in a per-`Settings`-instance key without module-level
    global state leaking between tests."""

    impl = String
    cache_ok = False

    def __init__(self, key_provider: Callable[[], str], *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._key_provider = key_provider

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        return encrypt(value, derive_fernet_key(self._key_provider()))

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        return decrypt(value, derive_fernet_key(self._key_provider()))
```

Fix the test's `decrypt`/`encrypt` calls to take `bytes` from `derive_fernet_key` — the test
above already does (`derive_fernet_key(...)` return value passed straight through).

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_crypto.py -v`
Expected: 3 passed

- [ ] **Step 7: mypy + commit**

Run: `cd backend && uv run mypy --strict src/`
Expected: no errors

```bash
git add backend/pyproject.toml backend/src/flowsage_backend/config.py \
  backend/src/flowsage_backend/crypto.py backend/tests/test_crypto.py backend/uv.lock
git commit -m "feat: add SECRET_ENCRYPTION_KEY + EncryptedString column type"
```

---

### Task 2: Encrypt `JiraIntegration.api_token` and `Webhook.secret` at rest

**Files:**
- Modify: `backend/src/flowsage_backend/models/integration.py`
- Modify: `backend/src/flowsage_backend/models/webhook.py`
- Create: `backend/migrations/versions/<rev>_encrypt_secret_columns.py`
- Test: `backend/tests/test_integrations_models.py` (extend)

**Interfaces:**
- Consumes: `EncryptedString` from `crypto.py` (Task 1), `get_settings` from `config.py`.
- Produces: `JiraIntegration.api_token` and `Webhook.secret` remain `Mapped[str]` at the Python
  level (no caller-visible type change — every existing read site keeps working unchanged).

- [ ] **Step 1: Write the failing "stored value is not plaintext" test**

Append to `backend/tests/test_integrations_models.py`:

```python
async def test_jira_api_token_is_encrypted_at_rest(db_session: AsyncSession) -> None:
    workspace_id = await _make_workspace(db_session)
    db_session.add(
        JiraIntegration(
            workspace_id=workspace_id,
            base_url="https://acme.atlassian.net",
            email="bot@acme.test",
            api_token="plaintext-jira-token-value",
            project_key="ACME",
        )
    )
    await db_session.commit()

    raw = await db_session.execute(
        text("SELECT api_token FROM jira_integrations WHERE workspace_id = :wid"),
        {"wid": workspace_id},
    )
    stored_value = raw.scalar_one()
    assert stored_value != "plaintext-jira-token-value"

    result = await db_session.execute(
        select(JiraIntegration).where(JiraIntegration.workspace_id == workspace_id)
    )
    assert result.scalar_one().api_token == "plaintext-jira-token-value"


async def test_webhook_secret_is_encrypted_at_rest(db_session: AsyncSession) -> None:
    workspace_id = await _make_workspace(db_session)
    db_session.add(Webhook(workspace_id=workspace_id, url="https://example.test/hook", secret="raw-secret-value", event_types=["alert.triggered"]))
    await db_session.commit()

    raw = await db_session.execute(
        text("SELECT secret FROM webhooks WHERE workspace_id = :wid"), {"wid": workspace_id}
    )
    assert raw.scalar_one() != "raw-secret-value"
```

Add `from sqlalchemy import text` to the file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_integrations_models.py -k encrypted -v`
Expected: FAIL — `stored_value != "plaintext-jira-token-value"` assertion fails because the
column still stores plaintext.

- [ ] **Step 3: Switch the columns to `EncryptedString`**

Edit `backend/src/flowsage_backend/models/integration.py` — add the import and change the
`api_token` column:

```python
from flowsage_backend.config import get_settings
from flowsage_backend.crypto import EncryptedString
```

```python
    api_token: Mapped[str] = mapped_column(
        EncryptedString(lambda: get_settings().secret_encryption_key, length=1000)
    )
```

Edit `backend/src/flowsage_backend/models/webhook.py` similarly for `secret`:

```python
from flowsage_backend.config import get_settings
from flowsage_backend.crypto import EncryptedString
```

```python
    secret: Mapped[str] = mapped_column(
        EncryptedString(lambda: get_settings().secret_encryption_key, length=500)
    )
```

`length=1000`/`500` widen the underlying `VARCHAR` — Fernet ciphertext is base64 of
(IV + HMAC + ciphertext), roughly `1.35 * plaintext_len + 100` bytes, so the original
`String(500)`/`String(64)` bounds are too small once encrypted.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_integrations_models.py -v`
Expected: all pass, including the 2 new ones. Also run the full integration/webhook suites
to confirm nothing else broke: `uv run pytest tests/test_integrations_api.py tests/test_webhooks.py -v`
Expected: all pass (transparent decrypt-on-read means `api.py`/`webhooks_store.py` need zero changes).

- [ ] **Step 5: Migration — widen columns (schema shape unchanged, `String`→`String` with new length)**

```bash
cd backend && uv run alembic revision -m "encrypt secret columns"
```

Edit the generated file (fill in `down_revision` with the current head, check via
`uv run alembic heads` if unsure):

```python
"""encrypt secret columns

Revision ID: <generated>
Revises: 3769700b545d
Create Date: 2026-07-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "<generated>"
down_revision: Union[str, Sequence[str], None] = "3769700b545d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Widen api_token/secret to fit Fernet ciphertext; existing rows in this
    fresh dev stack have no prior data to re-encrypt (see design spec's scope
    note -- no production deployment exists yet)."""
    op.alter_column("jira_integrations", "api_token", type_=sa.String(length=1000))
    op.alter_column("webhooks", "secret", type_=sa.String(length=500))


def downgrade() -> None:
    op.alter_column("webhooks", "secret", type_=sa.String(length=64))
    op.alter_column("jira_integrations", "api_token", type_=sa.String(length=500))
```

- [ ] **Step 6: mypy + commit**

Run: `cd backend && uv run mypy --strict src/`
Expected: no errors

```bash
git add backend/src/flowsage_backend/models/integration.py \
  backend/src/flowsage_backend/models/webhook.py backend/tests/test_integrations_models.py \
  backend/migrations/versions/
git commit -m "feat: encrypt Jira api_token and webhook secret at rest"
```

---

### Task 3: `AuditLog` model + migration

**Files:**
- Create: `backend/src/flowsage_backend/models/audit_log.py`
- Modify: `backend/src/flowsage_backend/models/__init__.py`
- Modify: `backend/migrations/versions/<rev from Task 2>_encrypt_secret_columns.py` — **no**, keep
  separate: Create a new migration file this task.
- Create: `backend/migrations/versions/<rev>_add_audit_logs.py`
- Test: `backend/tests/test_audit_models.py`

**Interfaces:**
- Produces: `AuditLog(id, workspace_id, actor_user_id: uuid.UUID | None, action: str,
  target_type: str | None, target_id: str | None, extra_data: dict[str, Any], ip_address: str
  | None, created_at: datetime)`, importable from `flowsage_backend.models`.

- [ ] **Step 1: Write the failing model test**

```python
# backend/tests/test_audit_models.py
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models import AuditLog
from flowsage_backend.models.workspace import Workspace


async def test_audit_log_round_trips(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Audit Test", slug=f"audit-test-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.commit()
    await db_session.refresh(workspace)

    entry = AuditLog(
        workspace_id=workspace.id,
        actor_user_id=None,
        action="member.role_changed",
        target_type="membership",
        target_id=str(uuid.uuid4()),
        extra_data={"from_role": "viewer", "to_role": "admin"},
        ip_address="203.0.113.7",
    )
    db_session.add(entry)
    await db_session.commit()

    result = await db_session.execute(select(AuditLog).where(AuditLog.workspace_id == workspace.id))
    fetched = result.scalar_one()
    assert fetched.action == "member.role_changed"
    assert fetched.extra_data == {"from_role": "viewer", "to_role": "admin"}
    assert fetched.actor_user_id is None


async def test_audit_log_extra_data_defaults_to_empty_dict(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Audit Default", slug=f"audit-default-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.commit()
    await db_session.refresh(workspace)

    entry = AuditLog(workspace_id=workspace.id, action="auth.login")
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)
    assert entry.extra_data == {}
    assert entry.target_type is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_audit_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'AuditLog'`

- [ ] **Step 3: Implement the model**

```python
# backend/src/flowsage_backend/models/audit_log.py
"""SOC2-track audit log: one row per security-relevant action (auth, membership,
integrations/secrets, persona/settings changes). See `audit.py` for the write/query
helpers -- this module is schema only."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from flowsage_backend.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

Edit `backend/src/flowsage_backend/models/__init__.py`:

```python
from flowsage_backend.models.api_key import ApiKey
from flowsage_backend.models.audit_log import AuditLog
from flowsage_backend.models.base import Base
```

Add `"AuditLog",` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_audit_models.py -v`
Expected: 2 passed

- [ ] **Step 5: Migration**

```bash
cd backend && uv run alembic revision -m "add audit logs"
```

Fill in (set `down_revision` to Task 2's revision id):

```python
"""add audit logs

Revision ID: <generated>
Revises: <task 2's revision id>
Create Date: 2026-07-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "<generated>"
down_revision: Union[str, Sequence[str], None] = "<task 2's revision id>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("extra_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_workspace_id", "audit_logs", ["workspace_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
```

- [ ] **Step 6: mypy + commit**

Run: `cd backend && uv run mypy --strict src/`
Expected: no errors

```bash
git add backend/src/flowsage_backend/models/audit_log.py \
  backend/src/flowsage_backend/models/__init__.py backend/tests/test_audit_models.py \
  backend/migrations/versions/
git commit -m "feat: add AuditLog model + migration"
```

---

### Task 4: `audit.py` write/query helpers + `conftest.py` helper

**Files:**
- Create: `backend/src/flowsage_backend/audit.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_audit.py`

**Interfaces:**
- Consumes: `AuditLog` (Task 3).
- Produces: `async def record_audit_event(session, workspace_id, *, actor_user_id=None, action,
  target_type=None, target_id=None, extra_data=None, ip_address=None) -> None`,
  `async def list_audit_logs(session, workspace_id, *, action=None, actor_user_id=None,
  cursor=None, limit=50) -> tuple[list[AuditLog], str | None]` where the returned second value
  is the next cursor (`None` when there are no more pages). Cursor is the opaque string
  `f"{created_at.isoformat()}|{id}"`.
- `record_audit_event` is best-effort: catches and logs any exception, never raises, per the
  spec's "audit write failure must not block the action" requirement.
- New `conftest.py` helper: `async def create_workspace_and_admin(session, email) ->
  tuple[User, Membership]` — used by this task's tests and every subsequent task's tests that
  need a bare workspace without going through `/auth/login` first.

- [ ] **Step 1: Add the `conftest.py` helper**

Append to `backend/tests/conftest.py` (after `create_api_key_for`):

```python
async def create_workspace_and_admin(
    session: AsyncSession, email: str, password: str = "hunter2"
) -> tuple[User, Membership]:
    """Creates a brand-new workspace with `email` as its sole admin -- for tests
    that need an isolated workspace rather than reusing the shared 'fs-default'
    one `login_to_default_workspace` targets."""
    user = await upsert_user(session, email, password)
    workspace = Workspace(name=f"Workspace for {email}", slug=f"ws-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.flush()
    membership = Membership(user_id=user.id, workspace_id=workspace.id, role=Role.ADMIN)
    session.add(membership)
    await session.commit()
    await session.refresh(membership)
    return user, membership
```

(`upsert_user`, `Workspace`, `Membership`, `Role`, `uuid` are already imported in this file.)

- [ ] **Step 2: Write the failing audit.py tests**

```python
# backend/tests/test_audit.py
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.audit import list_audit_logs, record_audit_event
from tests.conftest import create_workspace_and_admin


async def test_record_and_list_audit_event(db_session: AsyncSession) -> None:
    user, membership = await create_workspace_and_admin(db_session, f"audit-{uuid.uuid4().hex[:8]}@example.com")

    await record_audit_event(
        db_session,
        membership.workspace_id,
        actor_user_id=user.id,
        action="auth.login",
        ip_address="203.0.113.7",
    )

    entries, next_cursor = await list_audit_logs(db_session, membership.workspace_id)
    assert len(entries) == 1
    assert entries[0].action == "auth.login"
    assert entries[0].actor_user_id == user.id
    assert next_cursor is None


async def test_list_audit_logs_filters_by_action(db_session: AsyncSession) -> None:
    user, membership = await create_workspace_and_admin(db_session, f"audit-filter-{uuid.uuid4().hex[:8]}@example.com")
    await record_audit_event(db_session, membership.workspace_id, actor_user_id=user.id, action="auth.login")
    await record_audit_event(db_session, membership.workspace_id, actor_user_id=user.id, action="member.role_changed")

    entries, _ = await list_audit_logs(db_session, membership.workspace_id, action="auth.login")
    assert len(entries) == 1
    assert entries[0].action == "auth.login"


async def test_list_audit_logs_paginates_with_cursor(db_session: AsyncSession) -> None:
    user, membership = await create_workspace_and_admin(db_session, f"audit-page-{uuid.uuid4().hex[:8]}@example.com")
    for i in range(3):
        await record_audit_event(db_session, membership.workspace_id, actor_user_id=user.id, action=f"test.event.{i}")

    page_one, cursor = await list_audit_logs(db_session, membership.workspace_id, limit=2)
    assert len(page_one) == 2
    assert cursor is not None

    page_two, cursor_two = await list_audit_logs(db_session, membership.workspace_id, limit=2, cursor=cursor)
    assert len(page_two) == 1
    assert cursor_two is None
    assert {e.id for e in page_one} & {e.id for e in page_two} == set()


async def test_record_audit_event_never_raises_on_bad_input(db_session: AsyncSession) -> None:
    """Passing a nonexistent workspace_id violates the FK -- this must be swallowed,
    not propagated, per the spec's best-effort audit-write requirement."""
    await record_audit_event(db_session, uuid.uuid4(), action="auth.login")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_audit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flowsage_backend.audit'`

- [ ] **Step 4: Implement `audit.py`**

```python
# backend/src/flowsage_backend/audit.py
"""Audit log write/query helpers. `record_audit_event` is called inline from route
handlers right after the action it's logging succeeds; it never raises -- a failed
audit write must not roll back or fail the action it's documenting (mirrors the
existing Neo4j-mirror-write best-effort pattern in events.py)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


async def record_audit_event(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID | None = None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    extra_data: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    try:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                extra_data=extra_data or {},
                ip_address=ip_address,
            )
        )
        await session.commit()
    except Exception:  # noqa: BLE001 - a broken audit write must never block the
        # action it's documenting (e.g. a bad workspace_id, a DB hiccup).
        await session.rollback()
        logger.warning("Failed to record audit event %r for workspace %s", action, workspace_id, exc_info=True)


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    created_at_str, id_str = cursor.split("|", 1)
    return datetime.fromisoformat(created_at_str), uuid.UUID(id_str)


def _encode_cursor(entry: AuditLog) -> str:
    return f"{entry.created_at.isoformat()}|{entry.id}"


async def list_audit_logs(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    action: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[AuditLog], str | None]:
    query = select(AuditLog).where(AuditLog.workspace_id == workspace_id)
    if action is not None:
        query = query.where(AuditLog.action == action)
    if actor_user_id is not None:
        query = query.where(AuditLog.actor_user_id == actor_user_id)
    if cursor is not None:
        cursor_created_at, cursor_id = _decode_cursor(cursor)
        query = query.where(
            (AuditLog.created_at, AuditLog.id) < (cursor_created_at, cursor_id)
        )
    query = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1)

    result = await session.execute(query)
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1]) if has_more else None
    return page, next_cursor
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_audit.py -v`
Expected: 4 passed

- [ ] **Step 6: mypy + commit**

Run: `cd backend && uv run mypy --strict src/`
Expected: no errors

```bash
git add backend/src/flowsage_backend/audit.py backend/tests/test_audit.py backend/tests/conftest.py
git commit -m "feat: add audit log write/query helpers"
```

---

### Task 5: Wire `record_audit_event` into existing routes

**Files:**
- Modify: `backend/src/flowsage_backend/api/auth.py` — `login` (success), `logout`
- Modify: `backend/src/flowsage_backend/api/workspaces.py` — `add_member`, `update_member_role`, `remove_member`, `archive_current_workspace`
- Modify: `backend/src/flowsage_backend/api/integrations.py` — `create_api_key`, `revoke_api_key`, `connect_slack`, `disconnect_slack`, `connect_jira`, `disconnect_jira`, `create_webhook`, `delete_webhook`
- Modify: `backend/src/flowsage_backend/api/personas.py` — `create_persona`, `delete_persona`
- Modify: `backend/src/flowsage_backend/api/simulations.py` — `create_simulation`
- Modify: `backend/src/flowsage_backend/api/settings.py` — `update_model_calibration_settings`
- Test: `backend/tests/test_audit_wiring.py`

**Interfaces:**
- Consumes: `record_audit_event` (Task 4).
- No new interfaces produced — this task is call-site wiring only.

Action name conventions (used consistently across all call sites, and asserted on in this
task's tests): `auth.login`, `auth.logout`, `member.invited`, `member.role_changed`,
`member.removed`, `workspace.archived`, `api_key.created`, `api_key.revoked`,
`slack.connected`, `slack.disconnected`, `jira.connected`, `jira.disconnected`,
`webhook.created`, `webhook.deleted`, `persona.created`, `persona.deleted`,
`simulation.started`, `settings.calibration_updated`.

- [ ] **Step 1: Write the failing wiring test (covers a representative sample, not every route — full coverage lands in Task 6's isolation test)**

```python
# backend/tests/test_audit_wiring.py
from __future__ import annotations

import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models import AuditLog
from flowsage_backend.seed import upsert_user


async def test_login_is_audited(app: FastAPI, db_session: AsyncSession) -> None:
    email = f"audit-login-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/auth/login", json={"email": email, "password": "hunter2"})
    assert response.status_code == 200
    workspace_id = uuid.UUID(response.json()["workspace_id"])

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.workspace_id == workspace_id, AuditLog.action == "auth.login")
    )
    assert result.scalar_one_or_none() is not None


async def test_member_role_change_is_audited(app: FastAPI, db_session: AsyncSession) -> None:
    from tests.conftest import create_workspace_and_admin

    admin_user, admin_membership = await create_workspace_and_admin(
        db_session, f"audit-role-admin-{uuid.uuid4().hex[:8]}@example.com"
    )
    other_email = f"audit-role-other-{uuid.uuid4().hex[:8]}@example.com"
    other_user = await upsert_user(db_session, other_email, "hunter2")
    from flowsage_backend.models.workspace import Membership, Role

    db_session.add(
        Membership(user_id=other_user.id, workspace_id=admin_membership.workspace_id, role=Role.VIEWER)
    )
    await db_session.commit()
    result = await db_session.execute(
        select(Membership).where(
            Membership.user_id == other_user.id, Membership.workspace_id == admin_membership.workspace_id
        )
    )
    other_membership_id = result.scalar_one().id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": admin_user.email, "password": "hunter2"})
        await client.post(
            "/auth/switch-workspace", json={"workspace_id": str(admin_membership.workspace_id)}
        )
        response = await client.patch(
            f"/workspaces/current/members/{other_membership_id}", json={"role": "admin"}
        )
    assert response.status_code == 200

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.workspace_id == admin_membership.workspace_id,
            AuditLog.action == "member.role_changed",
        )
    )
    entry = result.scalar_one()
    assert entry.target_id == str(other_membership_id)
    assert entry.actor_user_id == admin_user.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_audit_wiring.py -v`
Expected: FAIL — both assertions find `None`/raise `NoResultFound` (no audit rows written yet).

- [ ] **Step 3: Wire `auth.py`**

Edit `backend/src/flowsage_backend/api/auth.py` imports:

```python
from flowsage_backend.audit import record_audit_event
```

In `login`, right before `return await _build_me_out(...)`:

```python
    membership = await _first_membership_or_401(session, user.id)
    _set_session_cookie(response, request, user.id, membership.workspace_id)
    await record_audit_event(
        session,
        membership.workspace_id,
        actor_user_id=user.id,
        action="auth.login",
        ip_address=request.client.host if request.client else None,
    )
    return await _build_me_out(session, user, membership)
```

In `logout`, the handler currently has no `session`/membership — add both so there's a
workspace to attribute the log to:

```python
@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    settings = request.app.state.settings
    user, membership = membership_pair
    response.delete_cookie(
        settings.cookie_name,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    await record_audit_event(
        session, membership.workspace_id, actor_user_id=user.id, action="auth.logout"
    )
    return {"status": "logged_out"}
```

This adds `get_current_membership`/`get_db_session` to `logout`'s imports (already imported
at the top of the file) — no new import lines needed for those two.

- [ ] **Step 4: Wire `workspaces.py`**

Add the import, then insert one `record_audit_event` call at the end of each handler (before
its `return`), all using `membership.workspace_id` and `user.id` from the existing
`_, membership = membership_pair` / `user, _ = membership_pair` locals already in scope:

```python
from flowsage_backend.audit import record_audit_event
```

`add_member` (after `await session.refresh(new_membership)`):
```python
    await record_audit_event(
        session, membership.workspace_id, actor_user_id=membership.user_id,
        action="member.invited", target_type="membership", target_id=str(new_membership.id),
        extra_data={"email": target_user.email, "role": new_membership.role.value},
    )
```

`update_member_role` (after `await session.refresh(target)`, before re-fetching `user`):
```python
    await record_audit_event(
        session, membership.workspace_id, actor_user_id=membership.user_id,
        action="member.role_changed", target_type="membership", target_id=str(target.id),
        extra_data={"to_role": target.role.value},
    )
```

`remove_member` (after `await session.delete(target)` / `await session.commit()`):
```python
    await record_audit_event(
        session, membership.workspace_id, actor_user_id=membership.user_id,
        action="member.removed", target_type="membership", target_id=str(target.id),
    )
```

`archive_current_workspace` (after `await session.refresh(workspace)`):
```python
    await record_audit_event(
        session, membership.workspace_id, actor_user_id=membership.user_id, action="workspace.archived",
    )
```

Note: `membership.user_id` — every `Membership` row already carries `user_id`, so
`membership_pair`'s `Membership` half is sufficient; `add_member`/`update_member_role`/
`remove_member`/`archive_current_workspace` all resolve `membership_pair =
Depends(require_role(Role.ADMIN))`, so `_, membership = membership_pair` already gives the
*acting* admin's membership, not the target's — correct actor attribution.

- [ ] **Step 5: Wire `integrations.py`**

Add the import, then one call per mutating handler, mirroring the same pattern (actor =
`membership.user_id`, workspace = `membership.workspace_id`):

```python
from flowsage_backend.audit import record_audit_event
```

`create_api_key` (after `await session.refresh(key)`):
```python
    await record_audit_event(
        session, membership.workspace_id, actor_user_id=membership.user_id,
        action="api_key.created", target_type="api_key", target_id=str(key.id),
        extra_data={"name": key.name, "key_prefix": key.key_prefix},
    )
```

`revoke_api_key` (after `await session.commit()`):
```python
    await record_audit_event(
        session, membership.workspace_id, actor_user_id=membership.user_id,
        action="api_key.revoked", target_type="api_key", target_id=str(key.id),
    )
```

`connect_slack` (after `await session.commit()`):
```python
    await record_audit_event(
        session, membership.workspace_id, actor_user_id=membership.user_id, action="slack.connected",
    )
```

`disconnect_slack` (inside the `if integration is not None:` block, after `await session.commit()`):
```python
        await record_audit_event(
            session, membership.workspace_id, actor_user_id=membership.user_id, action="slack.disconnected",
        )
```

`connect_jira` (after `await session.commit()`):
```python
    await record_audit_event(
        session, membership.workspace_id, actor_user_id=membership.user_id, action="jira.connected",
        extra_data={"base_url": payload.base_url, "project_key": payload.project_key},
    )
```

`disconnect_jira` (inside the `if integration is not None:` block):
```python
        await record_audit_event(
            session, membership.workspace_id, actor_user_id=membership.user_id, action="jira.disconnected",
        )
```

`create_webhook` (after `await session.refresh(webhook)`):
```python
    await record_audit_event(
        session, membership.workspace_id, actor_user_id=membership.user_id,
        action="webhook.created", target_type="webhook", target_id=str(webhook.id),
        extra_data={"url": webhook.url, "event_types": webhook.event_types},
    )
```

`delete_webhook` (after `await session.delete(webhook)` / `await session.commit()`):
```python
    await record_audit_event(
        session, membership.workspace_id, actor_user_id=membership.user_id,
        action="webhook.deleted", target_type="webhook", target_id=str(webhook_id),
    )
```

- [ ] **Step 6: Wire `personas.py`**

```python
from flowsage_backend.audit import record_audit_event
```

`create_persona` (after `await session.refresh(persona)`, before `return persona`):
```python
    await record_audit_event(
        session, membership.workspace_id, actor_user_id=membership.user_id,
        action="persona.created", target_type="persona", target_id=str(persona.id),
        extra_data={"slug": persona.slug},
    )
```

`delete_persona` (after the successful `await session.commit()` in the `try` block, before the
function ends — note this sits inside the same `try`/`except IntegrityError` the existing code
already has, so place it right after `commit()` succeeds):
```python
        await session.commit()
        await record_audit_event(
            session, membership.workspace_id, actor_user_id=membership.user_id,
            action="persona.deleted", target_type="persona", target_id=str(persona_id),
            extra_data={"slug": persona.slug},
        )
    except IntegrityError as exc:
```

- [ ] **Step 7: Wire `simulations.py`**

```python
from flowsage_backend.audit import record_audit_event
```

Find where `create_simulation` commits the new `SimulationRun` row (look for
`session.add(run)` / the subsequent commit further down in the function — read the file
first to locate the exact line before inserting) and add, right after the run is persisted
and its `id` is available:
```python
    await record_audit_event(
        session, membership.workspace_id, actor_user_id=membership.user_id,
        action="simulation.started", target_type="simulation_run", target_id=str(run.id),
        extra_data={"flow_name": flow_name, "goal": goal},
    )
```

- [ ] **Step 8: Wire `settings.py`**

```python
from flowsage_backend.audit import record_audit_event
```

`update_model_calibration_settings` (after `await session.refresh(settings)`, before `return settings`):
```python
    await record_audit_event(
        session, membership.workspace_id, actor_user_id=membership.user_id,
        action="settings.calibration_updated",
        extra_data={
            "anomaly_threshold": payload.anomaly_threshold,
            "auto_retrain_on_anomaly": payload.auto_retrain_on_anomaly,
            "digest_frequency": payload.digest_frequency.value,
        },
    )
```

(Rename the local variable if it shadows the module-level `settings_router` import name
conflict — check the file first; if `settings` is already used as the `CalibrationSettings`
instance's variable name, as it is in the current implementation, no rename is needed.)

- [ ] **Step 9: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_audit_wiring.py -v`
Expected: 2 passed

Run the full suite to confirm no regressions from the call-site edits:
Run: `cd backend && uv run pytest -q`
Expected: all pass

- [ ] **Step 10: mypy + commit**

Run: `cd backend && uv run mypy --strict src/`
Expected: no errors

```bash
git add backend/src/flowsage_backend/api/auth.py backend/src/flowsage_backend/api/workspaces.py \
  backend/src/flowsage_backend/api/integrations.py backend/src/flowsage_backend/api/personas.py \
  backend/src/flowsage_backend/api/simulations.py backend/src/flowsage_backend/api/settings.py \
  backend/tests/test_audit_wiring.py
git commit -m "feat: audit auth, membership, integrations, persona, simulation, settings actions"
```

---

### Task 6: `GET /audit-logs` endpoint + cross-tenant isolation

**Files:**
- Create: `backend/src/flowsage_backend/api/audit.py`
- Modify: `backend/src/flowsage_backend/main.py` — register `audit_router`
- Modify: `backend/tests/test_workspace_isolation.py` — add isolation case
- Test: `backend/tests/test_audit_api.py`

**Interfaces:**
- Consumes: `list_audit_logs` (Task 4).
- Produces: `GET /audit-logs?action=&actor_id=&cursor=&limit=` → `{"entries": [...], "next_cursor":
  str | null}`, behind `get_current_membership` (any role can view — matches every other `GET` in
  this codebase; only mutations are `require_role(Role.ADMIN)`-gated).

- [ ] **Step 1: Write the failing API test**

```python
# backend/tests/test_audit_api.py
from __future__ import annotations

import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.audit import record_audit_event
from tests.conftest import create_workspace_and_admin


async def test_get_audit_logs_returns_workspace_entries(app: FastAPI, db_session: AsyncSession) -> None:
    email = f"audit-api-{uuid.uuid4().hex[:8]}@example.com"
    user, membership = await create_workspace_and_admin(db_session, email)
    await record_audit_event(
        db_session, membership.workspace_id, actor_user_id=user.id, action="auth.login"
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        response = await client.get("/audit-logs")

    assert response.status_code == 200
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["entries"][0]["action"] == "auth.login"
    assert body["next_cursor"] is None


async def test_get_audit_logs_requires_auth(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/audit-logs")
    assert response.status_code == 401
```

Also append to `backend/tests/test_workspace_isolation.py`:

```python
async def test_audit_log_is_workspace_scoped(app: FastAPI, db_session: AsyncSession) -> None:
    from flowsage_backend.audit import record_audit_event
    from tests.conftest import create_workspace_and_admin

    tenant_a_email = f"isolation-audit-a-{uuid.uuid4().hex[:8]}@example.com"
    tenant_b_email = f"isolation-audit-b-{uuid.uuid4().hex[:8]}@example.com"
    user_a, membership_a = await create_workspace_and_admin(db_session, tenant_a_email)
    await create_workspace_and_admin(db_session, tenant_b_email)

    await record_audit_event(
        db_session, membership_a.workspace_id, actor_user_id=user_a.id, action="auth.login"
    )

    async with _authed_client(app, tenant_b_email) as client_b:
        response = await client_b.get("/audit-logs")

    assert response.status_code == 200
    assert response.json()["entries"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_audit_api.py -v`
Expected: FAIL with a 404 (route doesn't exist yet)

- [ ] **Step 3: Implement `api/audit.py`**

```python
# backend/src/flowsage_backend/api/audit.py
"""`GET /audit-logs`: the Security Logs view's data source (`/settings/security`)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.audit import list_audit_logs
from flowsage_backend.deps import get_current_membership, get_db_session
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership

router = APIRouter(
    prefix="/audit-logs", tags=["audit"], dependencies=[Depends(get_current_membership)]
)


class AuditLogEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    extra_data: dict[str, object]
    ip_address: str | None
    created_at: datetime


class AuditLogPageOut(BaseModel):
    entries: list[AuditLogEntryOut]
    next_cursor: str | None


@router.get("", response_model=AuditLogPageOut)
async def get_audit_logs(
    action: str | None = Query(default=None),
    actor_id: uuid.UUID | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> AuditLogPageOut:
    _, membership = membership_pair
    entries, next_cursor = await list_audit_logs(
        session,
        membership.workspace_id,
        action=action,
        actor_user_id=actor_id,
        cursor=cursor,
        limit=limit,
    )
    return AuditLogPageOut(
        entries=[AuditLogEntryOut.model_validate(e) for e in entries], next_cursor=next_cursor
    )
```

- [ ] **Step 4: Register the router**

Edit `backend/src/flowsage_backend/main.py`:

```python
from flowsage_backend.api.audit import router as audit_router
```

```python
    app.include_router(auth_router)
    app.include_router(audit_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_audit_api.py tests/test_workspace_isolation.py -v`
Expected: all pass

- [ ] **Step 6: mypy + commit**

Run: `cd backend && uv run mypy --strict src/`
Expected: no errors

```bash
git add backend/src/flowsage_backend/api/audit.py backend/src/flowsage_backend/main.py \
  backend/tests/test_audit_api.py backend/tests/test_workspace_isolation.py
git commit -m "feat: add GET /audit-logs endpoint"
```

---

### Task 7: Redis-backed rate limiting

**Files:**
- Create: `backend/src/flowsage_backend/rate_limit.py`
- Modify: `backend/src/flowsage_backend/main.py`
- Modify: `backend/src/flowsage_backend/api/auth.py` — `login` decorated
- Modify: `backend/src/flowsage_backend/api/events.py` — `ingest` decorated
- Test: `backend/tests/test_rate_limit.py`

**Interfaces:**
- Produces: `limiter: Limiter` (module-level, `key_func` dispatches per-route via the
  decorator picked — see below), `AUTH_RATE_LIMIT = "5/minute"`, `INGEST_RATE_LIMIT =
  "120/minute"`, `DEFAULT_RATE_LIMIT = "300/minute"`, `configure_rate_limiting(app: FastAPI,
  redis_url: str) -> None`.
- Consumes: `settings.redis_url` (existing field).

`slowapi.Limiter` takes one `key_func` for the whole instance, but this chunk needs three
different keying strategies (per-IP for auth, per-API-key for ingestion, per-user for
everything else). Rather than three `Limiter` instances (slowapi's exception handler is
registered per-app, and multiple limiters sharing one app is unsupported cleanly), use **one**
`Limiter` with a `key_func` that inspects the request and picks the right key per route:
`X-API-Key` header if present (ingestion), else the JWT cookie's `sub` claim if present and
decodable (authenticated routes), else remote address (auth routes, or any unauthenticated
request — same fallback slowapi uses by default). Per-route limits are still set individually
via `@limiter.limit(AUTH_RATE_LIMIT)` etc. on each decorated route.

- [ ] **Step 1: Write the failing rate-limit test**

```python
# backend/tests/test_rate_limit.py
from __future__ import annotations

import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flowsage_backend.seed import upsert_user


async def test_login_rate_limit_returns_429_after_threshold(
    app: FastAPI, db_session: AsyncSession
) -> None:
    email = f"ratelimit-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        statuses = []
        for _ in range(7):
            response = await client.post(
                "/auth/login", json={"email": email, "password": "wrong-password"}
            )
            statuses.append(response.status_code)

    assert 429 in statuses
    # Every request before the limit kicks in is a normal 401 (wrong password),
    # not something rate-limiting masks as a different error.
    assert statuses[0] == 401


async def test_non_auth_routes_are_not_rate_limited_at_auth_threshold(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """A sanity check that the tight 5/minute auth limit doesn't leak onto
    other routes -- /auth/me should tolerate more than 5 calls/minute."""
    from tests.conftest import login_to_default_workspace

    email = f"ratelimit-me-{uuid.uuid4().hex[:8]}@example.com"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login_to_default_workspace(client, db_session, email)
        statuses = [(await client.get("/auth/me")).status_code for _ in range(8)]

    assert all(s == 200 for s in statuses)
```

Add `from sqlalchemy.ext.asyncio import AsyncSession` to the imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_rate_limit.py -v`
Expected: FAIL — `429 in statuses` is `False` (no rate limiting wired up yet)

- [ ] **Step 3: Implement `rate_limit.py`**

```python
# backend/src/flowsage_backend/rate_limit.py
"""Redis-backed rate limiting (slowapi/`limits`). One `Limiter` instance shared by
the whole app; `_rate_limit_key` picks a per-request key so the same instance can
back per-IP (auth), per-API-key (ingestion), and per-user (everything else) tiers
depending on which decorator a route uses."""

from __future__ import annotations

from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

import jwt

AUTH_RATE_LIMIT = "5/minute"
INGEST_RATE_LIMIT = "120/minute"
DEFAULT_RATE_LIMIT = "300/minute"


def _rate_limit_key(request: Request) -> str:
    api_key = request.headers.get("X-API-Key")
    if api_key is not None:
        return f"apikey:{api_key}"

    settings = request.app.state.settings
    token = request.cookies.get(settings.cookie_name)
    if token is not None:
        try:
            payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            return f"user:{payload['sub']}"
        except jwt.PyJWTError:
            pass

    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_rate_limit_key, default_limits=[DEFAULT_RATE_LIMIT])


def configure_rate_limiting(app: FastAPI, redis_url: str) -> None:
    limiter._storage_uri = redis_url  # noqa: SLF001 - slowapi has no public setter;
    # setting this before the first `.limit()` call takes effect is the documented
    # way to point an already-constructed Limiter at a real backend (see slowapi's
    # own test suite for this exact pattern).
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
```

`SlowAPIMiddleware` (imported from `slowapi.middleware`) is what actually enforces
`default_limits` on every route that has no route-level `@limiter.limit(...)` of its own —
routes that *do* carry a decorator (`login`, `ingest`) keep their tighter decorator-level
limit instead; slowapi tracks each route's limit independently by endpoint name, so there's
no double-counting between the middleware-applied default and a route's own decorator.

- [ ] **Step 4: Wire into `main.py`**

```python
from flowsage_backend.rate_limit import configure_rate_limiting
```

In `create_app`, right after `app.state.settings = settings`:
```python
    app.state.settings = settings
    configure_rate_limiting(app, settings.redis_url)
```

- [ ] **Step 5: Decorate `/auth/login` and `POST /v1/events`**

Edit `backend/src/flowsage_backend/api/auth.py`:
```python
from flowsage_backend.rate_limit import AUTH_RATE_LIMIT, limiter
```
```python
@router.post("/login", response_model=MeOut)
@limiter.limit(AUTH_RATE_LIMIT)
async def login(
```

Edit `backend/src/flowsage_backend/api/events.py`:
```python
from flowsage_backend.rate_limit import INGEST_RATE_LIMIT, limiter
```
```python
@events_router.post("", response_model=IngestResult, status_code=201)
@limiter.limit(INGEST_RATE_LIMIT)
async def ingest(
    payload: list[EventIn],
    request: Request,
```

(slowapi's decorator requires the decorated function to accept a `request: Request`
parameter — both `login` and `ingest` already do. Routes with no `@limiter.limit(...)`
decorator of their own automatically fall under `DEFAULT_RATE_LIMIT`, enforced by the
`SlowAPIMiddleware` that `configure_rate_limiting` already registered in Step 4 — no
per-route wiring needed for the default tier.)

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_rate_limit.py -v`
Expected: 2 passed

Run the full suite — rate limiting now sits in front of every route, so this step exists
specifically to catch any pre-existing test that fires more than 300 requests/minute against
one identity (unlikely, but check):
Run: `cd backend && uv run pytest -q`
Expected: all pass

- [ ] **Step 7: mypy + commit**

Run: `cd backend && uv run mypy --strict src/`
Expected: no errors (add `# type: ignore[...]` only if slowapi's stubs force it — check the
actual mypy output before adding any ignore comment).

```bash
git add backend/src/flowsage_backend/rate_limit.py backend/src/flowsage_backend/main.py \
  backend/src/flowsage_backend/api/auth.py backend/src/flowsage_backend/api/events.py \
  backend/tests/test_rate_limit.py
git commit -m "feat: add Redis-backed rate limiting on auth, ingestion, and default routes"
```

---

### Task 8: Daily retention purge job

**Files:**
- Modify: `backend/src/flowsage_backend/worker.py`
- Test: `backend/tests/test_retention_purge.py`

**Interfaces:**
- Produces: `async def run_retention_purge_job(ctx: dict[str, Any]) -> None`, registered in
  `WorkerSettings.cron_jobs`.
- Consumes: `Workspace.retention_days` (existing field), `AuditLog` (Task 3), `Event` (existing).

- [ ] **Step 1: Write the failing purge test**

```python
# backend/tests/test_retention_purge.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models import AuditLog, Event
from flowsage_backend.worker import _purge_workspace_retention
from tests.conftest import create_workspace_and_admin


async def test_purge_deletes_audit_logs_and_events_older_than_retention(
    db_session: AsyncSession,
) -> None:
    user, membership = await create_workspace_and_admin(
        db_session, f"purge-{uuid.uuid4().hex[:8]}@example.com"
    )
    from flowsage_backend.models.workspace import Workspace

    workspace = await db_session.get(Workspace, membership.workspace_id)
    assert workspace is not None
    workspace.retention_days = 30
    await db_session.commit()

    now = datetime.now(timezone.utc)
    old_log = AuditLog(
        workspace_id=membership.workspace_id, action="old.event", created_at=now - timedelta(days=31)
    )
    recent_log = AuditLog(
        workspace_id=membership.workspace_id, action="recent.event", created_at=now - timedelta(days=1)
    )
    db_session.add_all([old_log, recent_log])
    await db_session.commit()

    old_event = Event(
        workspace_id=membership.workspace_id, session_id="s1", event="page_view", screen="landing",
        ts=now - timedelta(days=31), device="desktop", cohort="paid_users",
    )
    recent_event = Event(
        workspace_id=membership.workspace_id, session_id="s2", event="page_view", screen="landing",
        ts=now - timedelta(days=1), device="desktop", cohort="paid_users",
    )
    db_session.add_all([old_event, recent_event])
    await db_session.commit()

    await _purge_workspace_retention(db_session, membership.workspace_id, workspace.retention_days)

    remaining_logs = (await db_session.execute(
        select(AuditLog).where(AuditLog.workspace_id == membership.workspace_id)
    )).scalars().all()
    assert {log.action for log in remaining_logs} == {"recent.event"}

    remaining_events = (await db_session.execute(
        select(Event).where(Event.workspace_id == membership.workspace_id)
    )).scalars().all()
    assert {e.session_id for e in remaining_events} == {"s2"}
```

Check `backend/src/flowsage_backend/models/event.py` for the exact `Event` column names
before running — the test above assumes `session_id, event, screen, ts, device, cohort,
workspace_id` per the Phase 1 events model; adjust field names in the test if the actual
model differs (read the file first).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_retention_purge.py -v`
Expected: FAIL with `ImportError: cannot import name '_purge_workspace_retention'`

- [ ] **Step 3: Implement the purge job**

Edit `backend/src/flowsage_backend/worker.py`. Add imports:

```python
from sqlalchemy import delete

from flowsage_backend.models.audit_log import AuditLog
from flowsage_backend.models.event import Event
```

Add the functions (near `run_digest_job`, following its per-workspace-independent shape):

```python
async def run_retention_purge_job(ctx: dict[str, Any]) -> None:
    """Fires daily. Enforces each workspace's own `retention_days` against
    `AuditLog` and `Event` -- the two unbounded-growth tables this chunk's spec
    calls out. One workspace's failure (e.g. a lock contention) doesn't block
    the others, mirroring run_digest_job's loop."""
    session_factory = ctx["session_factory"]

    async with session_factory() as session:
        result = await session.execute(select(Workspace.id, Workspace.retention_days))
        workspaces = list(result.all())

    for workspace_id, retention_days in workspaces:
        try:
            async with session_factory() as session:
                await _purge_workspace_retention(session, workspace_id, retention_days)
        except Exception:  # noqa: BLE001 - one workspace's purge failure must not
            # stop the retention job from running for every other workspace.
            logger.warning("Retention purge failed for workspace %s", workspace_id, exc_info=True)


async def _purge_workspace_retention(
    session: AsyncSession, workspace_id: uuid.UUID, retention_days: int
) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    await session.execute(
        delete(AuditLog).where(AuditLog.workspace_id == workspace_id, AuditLog.created_at < cutoff)
    )
    await session.execute(
        delete(Event).where(Event.workspace_id == workspace_id, Event.ts < cutoff)
    )
    await session.commit()
```

(Check `Event`'s timestamp column name in `models/event.py` before writing this — the plan
assumes `ts`; adjust to match if it's actually named differently, e.g. `timestamp`.)

Add the cron entry:

```python
class WorkerSettings:
    functions = [run_simulation_job, run_retraining_job]
    cron_jobs = [
        cron(run_digest_job, hour=9, minute=0),
        cron(run_retention_purge_job, hour=3, minute=0),
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_retention_purge.py -v`
Expected: 1 passed

Run the full suite:
Run: `cd backend && uv run pytest -q`
Expected: all pass

- [ ] **Step 5: mypy + commit**

Run: `cd backend && uv run mypy --strict src/`
Expected: no errors

```bash
git add backend/src/flowsage_backend/worker.py backend/tests/test_retention_purge.py
git commit -m "feat: add daily retention purge job for audit logs and events"
```

---

### Task 9: Frontend — Security Logs page

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/routes/settings/SecurityLogsPage.tsx`
- Create: `frontend/src/routes/settings/SecurityLogsPage.test.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/e2e/security-logs.spec.ts`

**Interfaces:**
- Consumes: `GET /audit-logs` (Task 6).
- Produces: `AuditLogEntry`, `AuditLogPage` types; `api.getAuditLogs(params?: { action?: string;
  cursor?: string }): Promise<AuditLogPage>`; `SecurityLogsPage` component at `/settings/security`.

- [ ] **Step 1: Add types**

Append to `frontend/src/lib/types.ts`:

```typescript
export interface AuditLogEntry {
  id: string;
  actor_user_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  extra_data: Record<string, unknown>;
  ip_address: string | null;
  created_at: string;
}

export interface AuditLogPage {
  entries: AuditLogEntry[];
  next_cursor: string | null;
}
```

- [ ] **Step 2: Add the API client function**

In `frontend/src/lib/api.ts`, add `AuditLogPage` to the type import list, then add near
`getWebhooks`:

```typescript
  getAuditLogs: (params?: { action?: string; cursor?: string }): Promise<AuditLogPage> => {
    const query = new URLSearchParams();
    if (params?.action) query.set("action", params.action);
    if (params?.cursor) query.set("cursor", params.cursor);
    const qs = query.toString();
    return request<AuditLogPage>(`/audit-logs${qs ? `?${qs}` : ""}`);
  },
```

- [ ] **Step 3: Write the failing component test**

```typescript
// frontend/src/routes/settings/SecurityLogsPage.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { SecurityLogsPage } from "./SecurityLogsPage";

vi.mock("../../lib/api", () => ({
  api: { getAuditLogs: vi.fn() },
  ApiError: class ApiError extends Error {},
}));

const mockApi = vi.mocked(api);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("SecurityLogsPage", () => {
  it("renders audit log entries", async () => {
    mockApi.getAuditLogs.mockResolvedValue({
      entries: [
        {
          id: "log-1",
          actor_user_id: "user-1",
          action: "auth.login",
          target_type: null,
          target_id: null,
          extra_data: {},
          ip_address: "203.0.113.7",
          created_at: "2026-07-24T10:00:00Z",
        },
      ],
      next_cursor: null,
    });

    render(<SecurityLogsPage />);

    await waitFor(() => expect(screen.getByText("auth.login")).toBeInTheDocument());
    expect(screen.getByText("203.0.113.7")).toBeInTheDocument();
  });

  it("shows an empty state when there are no entries", async () => {
    mockApi.getAuditLogs.mockResolvedValue({ entries: [], next_cursor: null });
    render(<SecurityLogsPage />);
    await waitFor(() => expect(screen.getByText(/no security events/i)).toBeInTheDocument());
  });
});
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd frontend && npm run test -- SecurityLogsPage`
Expected: FAIL — module `./SecurityLogsPage` doesn't exist

- [ ] **Step 5: Implement `SecurityLogsPage.tsx`**

```tsx
// frontend/src/routes/settings/SecurityLogsPage.tsx
import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { AuditLogEntry } from "../../lib/types";

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export function SecurityLogsPage() {
  const [entries, setEntries] = useState<AuditLogEntry[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getAuditLogs()
      .then((page) => {
        setEntries(page.entries);
        setNextCursor(page.next_cursor);
      })
      .catch((err: unknown) => setError(errorMessage(err, "Failed to load security logs.")));
  }, []);

  async function loadMore() {
    if (nextCursor === null) return;
    try {
      const page = await api.getAuditLogs({ cursor: nextCursor });
      setEntries((prev) => [...(prev ?? []), ...page.entries]);
      setNextCursor(page.next_cursor);
    } catch (err) {
      setError(errorMessage(err, "Failed to load more security logs."));
    }
  }

  return (
    <div className="flex flex-col gap-6 p-8">
      <div>
        <h1 className="font-headline text-2xl">Security Logs</h1>
        <p className="text-sm text-on-surface-variant mt-1">
          Audit trail of authentication, membership, and integration changes in this workspace.
        </p>
      </div>
      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}
      {entries === null ? (
        <p className="text-on-surface-variant text-sm">Loading…</p>
      ) : entries.length === 0 ? (
        <p className="text-on-surface-variant text-sm">No security events yet.</p>
      ) : (
        <div className="bg-surface-container-lowest rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-on-surface-variant border-b border-outline-variant">
                <th className="p-3 font-medium">Action</th>
                <th className="p-3 font-medium">Target</th>
                <th className="p-3 font-medium">IP Address</th>
                <th className="p-3 font-medium">Time</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id} className="border-b border-outline-variant last:border-0">
                  <td className="p-3">{entry.action}</td>
                  <td className="p-3">
                    {entry.target_type ? `${entry.target_type}:${entry.target_id ?? ""}` : "—"}
                  </td>
                  <td className="p-3">{entry.ip_address ?? "—"}</td>
                  <td className="p-3">{new Date(entry.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {nextCursor !== null ? (
        <button
          type="button"
          onClick={() => void loadMore()}
          className="self-start rounded-lg ghost-border py-2 px-4 text-sm font-medium"
        >
          Load more
        </button>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npm run test -- SecurityLogsPage`
Expected: 2 passed

- [ ] **Step 7: Wire nav + route**

Edit `frontend/src/components/Sidebar.tsx`, add to `NAV_ITEMS` (after the Integrations entry):
```typescript
  { to: "/settings/security", label: "Security", icon: "shield" },
```

Edit `frontend/src/App.tsx`:
```typescript
import { SecurityLogsPage } from "./routes/settings/SecurityLogsPage";
```
```typescript
          <Route path="/settings/security" element={<SecurityLogsPage />} />
```

- [ ] **Step 8: e2e spec**

```typescript
// frontend/e2e/security-logs.spec.ts
import { test, expect } from "@playwright/test";

const EMAIL = "e2e@flowsage.dev";
const PASSWORD = "supersecret123";

test("Security Logs: login shows up in the audit trail", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);

  await page.getByRole("link", { name: "Security" }).click();
  await expect(page).toHaveURL(/\/settings\/security/);
  await expect(page.getByText("auth.login").first()).toBeVisible();
});
```

- [ ] **Step 9: Full frontend verification + commit**

Run: `cd frontend && npm run typecheck && npm run test && npm run lint`
Expected: all pass

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts \
  frontend/src/routes/settings/SecurityLogsPage.tsx frontend/src/routes/settings/SecurityLogsPage.test.tsx \
  frontend/src/components/Sidebar.tsx frontend/src/App.tsx frontend/e2e/security-logs.spec.ts
git commit -m "feat: add SecurityLogsPage (/settings/security)"
```

---

### Task 10: Full verification pass

**Files:** none (verification only — no new code).

- [ ] **Step 1: Backend full suite**

Run: `cd backend && uv run pytest -q && uv run mypy --strict src/ && uv run autoflake8 --check -r src/`
Expected: all green

- [ ] **Step 2: Frontend full suite**

Run: `cd frontend && npm run typecheck && npm run test && npm run lint`
Expected: all green

- [ ] **Step 3: Migration upgrade → downgrade → upgrade cycle**

Run against a live Postgres (e.g. `infra/docker-compose.yml`'s `postgres` service):
```bash
cd backend && uv run alembic upgrade head
uv run alembic downgrade -3
uv run alembic upgrade head
```
Expected: no errors at any step (3 = the number of new migrations this chunk added: encrypt
columns, audit logs — confirm the actual count from `git log` before running if a task above
ended up split differently).

- [ ] **Step 4: Full docker-compose pass**

```bash
docker compose -f infra/docker-compose.yml up -d --build
```
Then, against the running stack:
1. Create a user (`flowsage-backend create-user`), log in via the browser, confirm `auth.login`
   appears at `/settings/security`.
2. Invite a member, change their role, remove them — confirm 3 corresponding entries appear.
3. Create an API key, revoke it — confirm 2 entries.
4. Connect Slack (or attempt with a fake webhook URL — connect still succeeds, no live POST
   happens until an actual digest fires), disconnect — confirm entries.
5. `psql` into the running Postgres container and `SELECT api_token FROM jira_integrations;`
   / `SELECT secret FROM webhooks;` (after connecting Jira / creating a webhook first) —
   confirm neither is human-readable plaintext.
6. `curl` burst `/auth/login` 10x in a row — confirm a `429` appears with a `Retry-After`
   header.
7. Manually invoke the purge: `docker compose exec worker python -c "import asyncio; from
   flowsage_backend.worker import run_retention_purge_job; from flowsage_backend.config import
   get_settings; from flowsage_backend.db import create_engine, create_session_factory; engine =
   create_engine(get_settings()); asyncio.run(run_retention_purge_job({'session_factory':
   create_session_factory(engine)}))"` against a workspace with a hand-inserted old `AuditLog`
   row (`retention_days` set low, e.g. 1, via `PATCH /workspaces/current`) — confirm the old row
   is gone and a recent one survives.
8. `docker compose down`.

- [ ] **Step 5: Update project memory**

Update the `project-build-status` memory (or equivalent) noting Phase 3 chunk 3 complete —
this is a note-to-self step for whoever executes the plan, not a code change.

- [ ] **Step 6: Final commit (if Step 4 uncovered any fixes)**

```bash
git add -A
git commit -m "fix: address issues found in full docker-compose verification pass"
```

(Skip this commit if Step 4 found nothing to fix.)
