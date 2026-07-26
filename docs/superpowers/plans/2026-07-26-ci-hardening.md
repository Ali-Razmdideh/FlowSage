# CI Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new GitHub Actions jobs to `.github/workflows/ci.yml` — a fast `frontend` job (oxlint, `tsc`, vitest, production build) and a full-stack `e2e` job (docker-compose build + up, migrate/seed, Playwright against the real nginx-served build) — closing the gap where frontend and e2e checks have never run in CI, only manually. Closes Phase 4 item 4's first sub-bullet per `docs/superpowers/specs/2026-07-26-ci-hardening-design.md`.

**Architecture:** Both jobs are added as siblings of the existing `python` job in the same workflow file, triggered by the same `push`/`pull_request` events, running in parallel (no `needs:` gating between any of the three jobs). No test content changes — all 5 e2e specs, `playwright.config.ts`, and `infra/docker-compose.yml` already work correctly for this; this plan is CI wiring only.

**Tech Stack:** GitHub Actions, `actions/setup-node@v4`, npm, Playwright, Docker Compose (existing `infra/docker-compose.yml`, unmodified).

## Global Constraints

- No changes to `frontend/playwright.config.ts` or any `frontend/e2e/*.spec.ts` file — they are already correct.
- **Amended after Task 2:** `infra/docker-compose.yml` may receive exactly one narrow addition (an `AUTH_RATE_LIMIT_OVERRIDE` env passthrough, Task 2.5) — the original "no changes" constraint held through Tasks 1-2 and is now superseded for that single purpose only. No other changes to that file.
- No changes to the existing `python` or `frontend` jobs in `.github/workflows/ci.yml`.
- Node version: `22` (LTS), matching `actions/setup-node@v4`'s `node-version: "22"`.
- Work happens in git worktree `.claude/worktrees/phase4-ci-hardening` (branch `worktree-phase4-ci-hardening`), one task per commit, final task merges to `main` and removes the worktree.
- Every task must be verified by actually triggering GitHub Actions (push the worktree branch to origin) and confirming the relevant job is green in the Actions UI/`gh run view` — a workflow YAML change cannot be verified by local file inspection alone.

---

### Task 0: Create the worktree

**Files:** none (setup only)

- [ ] **Step 1: Create the worktree and branch**

```bash
cd /home/asus/Projects/personal/FlowSage
git worktree add .claude/worktrees/phase4-ci-hardening -b worktree-phase4-ci-hardening
```

- [ ] **Step 2: Verify the worktree builds**

```bash
cd .claude/worktrees/phase4-ci-hardening
uv sync --all-extras
cd backend && uv run pytest -q
```

Expected: all 208 existing backend tests pass. All subsequent steps in this plan run inside `.claude/worktrees/phase4-ci-hardening` unless noted otherwise.

---

### Task 1: `frontend` CI job

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `frontend/package.json`'s existing scripts (`lint`, `typecheck`, `test`, `build`) — unmodified, already used by developers locally.
- Produces: a `frontend` job in `.github/workflows/ci.yml` that later tasks don't depend on (independent of the `e2e` job).

- [ ] **Step 1: Add the job to the workflow file**

Modify `.github/workflows/ci.yml` — insert a new `frontend` job after the closing of the existing `python` job (i.e., as a new top-level entry under `jobs:`, sibling to `python`):

```yaml
  frontend:
    name: Frontend lint, typecheck, test, build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Node
        uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: frontend/package-lock.json

      - name: Install dependencies
        working-directory: frontend
        run: npm ci

      - name: oxlint
        working-directory: frontend
        run: npm run lint

      - name: typecheck
        working-directory: frontend
        run: npm run typecheck

      - name: vitest
        working-directory: frontend
        run: npm test

      - name: build
        working-directory: frontend
        run: npm run build
```

The full resulting file should have `jobs:` containing exactly `python` (unmodified) and `frontend` (new) at this point in the plan.

- [ ] **Step 2: Verify the YAML is well-formed**

```bash
cd /home/asus/Projects/personal/FlowSage/.claude/worktrees/phase4-ci-hardening
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "YAML OK"
```

Expected: prints `YAML OK` with no exception. (If `pyyaml` isn't installed, run `uv run --with pyyaml python3 -c "..."` instead.)

- [ ] **Step 3: Verify the referenced npm scripts actually exist and pass locally**

```bash
cd frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

Expected: all four commands exit 0 (these are the exact commands the new job runs, so a local failure here means the job will fail in CI too).

- [ ] **Step 4: Commit**

```bash
cd /home/asus/Projects/personal/FlowSage/.claude/worktrees/phase4-ci-hardening
git add .github/workflows/ci.yml
git commit -m "ci: add frontend lint/typecheck/test/build job"
```

- [ ] **Step 5: Push the branch and verify the job runs green on GitHub Actions**

```bash
git push -u origin worktree-phase4-ci-hardening
gh run list --branch worktree-phase4-ci-hardening --limit 1
# wait for the run to finish, then:
gh run view --branch worktree-phase4-ci-hardening --log-failed || true
gh run list --branch worktree-phase4-ci-hardening --limit 1
```

Expected: the latest run shows `python` and `frontend` both `completed`/`success`. If `frontend` fails, read the failed step's log via `gh run view <run-id> --log-failed`, fix, re-commit, re-push, re-check — do not proceed to Task 2 until this job is green.

---

### Task 2: `e2e` CI job

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `frontend/e2e/README.md`

**Interfaces:**
- Consumes: `infra/docker-compose.yml`'s existing 6 services (unmodified) and the exact migrate/seed/create-user sequence already documented in `frontend/e2e/README.md`; `flowsage-backend`'s `/healthz` endpoint (`backend/src/flowsage_backend/main.py:83-85`, returns `200` once the app is up) as the readiness signal, since the `backend` service has no `healthcheck:` block in `infra/docker-compose.yml` for Docker itself to key off of.
- Produces: an `e2e` job in `.github/workflows/ci.yml`, fully independent of `frontend` and `python` (no `needs:`).

- [ ] **Step 1: Add the job to the workflow file**

Modify `.github/workflows/ci.yml` — insert a new `e2e` job as a third sibling under `jobs:` (after `frontend`):

```yaml
  e2e:
    name: Playwright e2e (full stack)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Node
        uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: frontend/package-lock.json

      - name: Install frontend dependencies
        working-directory: frontend
        run: npm ci

      - name: Install Playwright browsers
        working-directory: frontend
        run: npx playwright install --with-deps chromium

      - name: Build and start the full stack
        run: docker compose -f infra/docker-compose.yml up -d --build

      - name: Wait for backend to be ready
        run: |
          for i in $(seq 1 30); do
            if curl -sf http://localhost:8000/healthz > /dev/null; then
              echo "backend is up"
              exit 0
            fi
            echo "waiting for backend ($i/30)..."
            sleep 2
          done
          echo "backend never became ready"
          docker compose -f infra/docker-compose.yml logs backend
          exit 1

      - name: Migrate database
        run: |
          docker compose -f infra/docker-compose.yml exec -T backend \
            python -m alembic -c /workspace/backend/alembic.ini upgrade head

      - name: Seed baseline personas
        run: |
          docker compose -f infra/docker-compose.yml exec -T backend \
            flowsage-backend seed-personas

      - name: Create e2e test user
        run: |
          docker compose -f infra/docker-compose.yml exec -T backend \
            flowsage-backend create-user e2e@flowsage.dev supersecret123

      - name: Run Playwright e2e tests
        working-directory: frontend
        run: npx playwright test

      - name: Upload Playwright report
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report
          path: frontend/playwright-report/
          retention-days: 7

      - name: Tear down the stack
        if: always()
        run: docker compose -f infra/docker-compose.yml down -v
```

Note the `-T` flag on every `docker compose exec` — required in non-interactive CI runners (no TTY allocated), otherwise `exec` fails with `the input device is not a TTY`.

- [ ] **Step 2: Verify the YAML is well-formed**

```bash
cd /home/asus/Projects/personal/FlowSage/.claude/worktrees/phase4-ci-hardening
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "YAML OK"
```

Expected: prints `YAML OK`.

- [ ] **Step 3: Dry-run the same sequence locally before trusting CI**

```bash
cd /home/asus/Projects/personal/FlowSage/.claude/worktrees/phase4-ci-hardening
docker compose -f infra/docker-compose.yml up -d --build
for i in $(seq 1 30); do curl -sf http://localhost:8000/healthz > /dev/null && break; sleep 2; done
docker compose -f infra/docker-compose.yml exec -T backend python -m alembic -c /workspace/backend/alembic.ini upgrade head
docker compose -f infra/docker-compose.yml exec -T backend flowsage-backend seed-personas
docker compose -f infra/docker-compose.yml exec -T backend flowsage-backend create-user e2e@flowsage.dev supersecret123
cd frontend
npx playwright install --with-deps chromium
npx playwright test
cd ..
docker compose -f infra/docker-compose.yml down -v
```

Expected: all 5 e2e spec files pass. If `create-user` fails because `e2e@flowsage.dev` already exists from a prior local run, that's pre-existing local volume state, not a bug in this plan — `docker compose down -v` before the run (already the case here on a fresh worktree) avoids it; note this for anyone re-running locally with a stale volume.

- [ ] **Step 4: Update the e2e README to mention CI**

Modify `frontend/e2e/README.md` — add a note after the existing intro paragraph (before the "## Setup" heading):

```markdown
As of this chunk, these run automatically in CI on every push and pull request
(see the `e2e` job in `.github/workflows/ci.yml`) using the built Docker images,
not `npm run dev`. The manual setup below is for local iteration.
```

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml frontend/e2e/README.md
git commit -m "ci: add full-stack Playwright e2e job"
```

- [ ] **Step 6: Push and verify the job runs green on GitHub Actions**

```bash
git push origin worktree-phase4-ci-hardening
gh run list --branch worktree-phase4-ci-hardening --limit 1
# wait for the run to finish, then:
gh run list --branch worktree-phase4-ci-hardening --limit 1
```

Expected: the latest run shows `python`, `frontend`, and `e2e` all `completed`/`success`. If `e2e` fails, download the uploaded `playwright-report` artifact (`gh run download <run-id> -n playwright-report`) or read `gh run view <run-id> --log-failed` first — do not proceed to Task 3 until this job is green.

- [ ] **Step 7 (validates the `if: always()` teardown, then reverts it): Deliberately break a test, confirm teardown still runs, then undo**

Modify `frontend/e2e/getting-started.spec.ts` temporarily — change one `expect(...)` assertion to something guaranteed false (e.g. append `.not` to an existing `toBeVisible()` call), commit, push, and confirm via `gh run view <run-id>` that the `Tear down the stack` step still shows as completed even though `Run Playwright e2e tests` failed. Then revert the temporary change:

```bash
git revert --no-edit HEAD
git push origin worktree-phase4-ci-hardening
```

Expected: the revert commit's CI run shows all 3 jobs green again, confirming the deliberate-break commit's failure was real (not a false negative) and the teardown step is unconditional.

---

### Task 2.5: Env-gated auth rate limit override (discovered during Task 2)

**Why this task exists:** Task 2's implementer discovered that the `e2e` job as wired cannot pass — the 5 e2e spec files run 13 tests, most of which independently call `/auth/login` as their first action, and the backend's `AUTH_RATE_LIMIT = "5/minute"` (per-IP) in `backend/src/flowsage_backend/rate_limit.py:22` trips after the 5th login within a rolling minute, deterministically failing ~7 of the remaining tests with a stuck-on-`/login` symptom. Confirmed via direct curl probing (requests 1-5: `200`/`401`, request 6+: `429`) and a full Playwright run (6 passed / 7 failed, all 7 failures being the same symptom). This reproduces identically on GitHub-hosted runners — it is not sandbox-specific.

The user chose an env-gated bypass: production behavior (`5/minute`, unconditionally) stays exactly as-is; only the `e2e` CI job's backend gets a higher effective limit, via one new optional environment variable the code already has no reason to reject.

**Files:**
- Modify: `backend/src/flowsage_backend/rate_limit.py:22`
- Modify: `infra/docker-compose.yml` (the `backend_env` anchor only — the one exception to the otherwise-unmodified-compose-file constraint)
- Modify: `.github/workflows/ci.yml` (the `e2e` job's stack-startup step only)
- Test: `backend/tests/test_rate_limit.py` (add one new test; the two existing tests must keep passing unmodified, since they rely on the unset-env-var default staying `"5/minute"`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `AUTH_RATE_LIMIT` in `rate_limit.py` becomes `os.environ.get("AUTH_RATE_LIMIT_OVERRIDE", "5/minute")` instead of a bare string literal — same name, same import site (`from flowsage_backend.rate_limit import AUTH_RATE_LIMIT, limiter, resolve_signature` in `backend/src/flowsage_backend/api/auth.py:19` needs no change, since `AUTH_RATE_LIMIT` is still a plain string evaluated at import time, just now env-sensitive).

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_rate_limit.py` (new test, appended after the existing two):

```python
async def test_auth_rate_limit_override_env_var_raises_the_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_RATE_LIMIT_OVERRIDE", "1000/minute")
    import importlib

    import flowsage_backend.rate_limit as rate_limit_module

    importlib.reload(rate_limit_module)
    try:
        assert rate_limit_module.AUTH_RATE_LIMIT == "1000/minute"
    finally:
        monkeypatch.delenv("AUTH_RATE_LIMIT_OVERRIDE", raising=False)
        importlib.reload(rate_limit_module)
```

Add `import pytest` at the top of `backend/tests/test_rate_limit.py` if not already present (it isn't, per the existing file — only `uuid`, `fastapi`, `httpx`, `sqlalchemy`, and two local imports are there today).

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_rate_limit.py -v
```

Expected: the new test FAILs with `AssertionError: assert '5/minute' == '1000/minute'` (module-level constant doesn't read the env var yet). The two existing tests still pass.

- [ ] **Step 3: Write the implementation**

Modify `backend/src/flowsage_backend/rate_limit.py` — add `import os` to the existing import block (alongside `inspect`, `typing`), and change line 22:

```python
AUTH_RATE_LIMIT = "5/minute"
```

to:

```python
AUTH_RATE_LIMIT = os.environ.get("AUTH_RATE_LIMIT_OVERRIDE", "5/minute")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_rate_limit.py -v
```

Expected: PASS, all 3 tests (2 existing + 1 new).

- [ ] **Step 5: Run the full backend suite (env-var reload must not leak into other tests)**

```bash
cd backend && uv run pytest -q
```

Expected: all 208 tests pass — the new test's `importlib.reload` + `finally`-block cleanup must leave `AUTH_RATE_LIMIT` back at `"5/minute"` for every other test in the session (this suite's Postgres fixture is session-scoped with no per-test DB isolation per this project's established gotcha, and `rate_limit.py`'s module-level constant is exactly as global — the `finally` block is not optional).

- [ ] **Step 6: Type-check and format**

```bash
cd backend
uv run autoflake8 --in-place --remove-all-unused-imports src/flowsage_backend/rate_limit.py tests/test_rate_limit.py
uv run black src/flowsage_backend/rate_limit.py tests/test_rate_limit.py
uv run mypy --strict src
```

Expected: mypy reports no issues.

- [ ] **Step 7: Pass the override through docker-compose's shared backend environment**

Modify `infra/docker-compose.yml` — add one line to the `backend_env` YAML anchor (the block starting `environment: &backend_env` under the `backend` service), immediately after the existing `ANTHROPIC_API_KEY` line:

```yaml
      ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY:-}"
      AUTH_RATE_LIMIT_OVERRIDE: "${AUTH_RATE_LIMIT_OVERRIDE:-}"
```

An unset/empty `AUTH_RATE_LIMIT_OVERRIDE` on the host means the container also sees it unset (Docker Compose does not pass an empty-string env var through as literally empty in a way that changes `os.environ.get`'s behavior here — an empty string is still "set", so to keep local `docker compose up` with no env var exported behaving exactly as today, `rate_limit.py`'s `os.environ.get(..., "5/minute")` must treat an empty string the same as unset). Revise Step 3's implementation to guard against that:

```python
AUTH_RATE_LIMIT = os.environ.get("AUTH_RATE_LIMIT_OVERRIDE") or "5/minute"
```

(`or` treats both "unset" and `""` as "use the default" — re-run Steps 2 and 4's tests after this revision to confirm they still pass; the test already asserts the non-empty-override case, so this change is safe.)

- [ ] **Step 8: Verify docker-compose still parses and the default behavior is unchanged**

```bash
cd /home/asus/Projects/personal/FlowSage/.claude/worktrees/phase4-ci-hardening
docker compose -f infra/docker-compose.yml config > /dev/null && echo "compose config OK"
```

Expected: prints `compose config OK` with no error (validates YAML + variable interpolation syntax without starting anything).

- [ ] **Step 9: Set the override in the `e2e` CI job only**

Modify `.github/workflows/ci.yml` — the `e2e` job's `Build and start the full stack` step (added in Task 2) gets an `env:` key:

```yaml
      - name: Build and start the full stack
        env:
          AUTH_RATE_LIMIT_OVERRIDE: "1000/minute"
        run: docker compose -f infra/docker-compose.yml up -d --build
```

This exports `AUTH_RATE_LIMIT_OVERRIDE` into that step's shell environment, which Docker Compose then substitutes into `${AUTH_RATE_LIMIT_OVERRIDE:-}` when it renders `infra/docker-compose.yml`, which becomes the `backend` container's actual env var, which `rate_limit.py` reads at import time inside that container. No other step or job sets this variable, so `python`, `frontend`, and any local `docker compose up` without it exported all keep the real `5/minute` production behavior.

- [ ] **Step 10: Re-run the local e2e dry-run with the override set, confirm the full suite passes**

```bash
cd /home/asus/Projects/personal/FlowSage/.claude/worktrees/phase4-ci-hardening
AUTH_RATE_LIMIT_OVERRIDE=1000/minute docker compose -f infra/docker-compose.yml up -d --build
for i in $(seq 1 30); do curl -sf http://localhost:8000/healthz > /dev/null && break; sleep 2; done
docker compose -f infra/docker-compose.yml exec -T backend /workspace/.venv/bin/python -m alembic -c /workspace/backend/alembic.ini upgrade head
docker compose -f infra/docker-compose.yml exec -T backend /workspace/.venv/bin/flowsage-backend seed-personas
docker compose -f infra/docker-compose.yml exec -T backend /workspace/.venv/bin/flowsage-backend create-user e2e@flowsage.dev supersecret123
cd frontend && npx playwright test
cd .. && docker compose -f infra/docker-compose.yml down -v
```

Expected: all 5 e2e spec files pass (13/13), no `429`s. If this sandbox still can't reach `fonts.googleapis.com` (a network quirk Task 2 already documented as sandbox-local, not a real bug), that specific symptom is a known non-issue — confirm login/navigation succeeds up to that point instead of insisting on a literal 13/13 in a sandbox with that quirk, and say so explicitly in the report.

- [ ] **Step 11: Commit**

```bash
git add backend/src/flowsage_backend/rate_limit.py backend/tests/test_rate_limit.py infra/docker-compose.yml .github/workflows/ci.yml
git commit -m "fix: allow AUTH_RATE_LIMIT_OVERRIDE so CI's e2e job doesn't trip the login rate limit"
```

- [ ] **Step 12: Push**

```bash
git push origin worktree-phase4-ci-hardening
```

(No `gh run list` step here either, per the same no-`gh`-in-this-sandbox situation as Tasks 1-2 — the human verifies this push's Actions run the same way as before.)

---

### Task 2.6: Fix `seed-personas` workspace targeting + missing teammate user (discovered from the first real GitHub Actions run on `main`)

**Why this task exists:** The merge to `main` gave the first-ever live GitHub Actions run of this workflow. It failed on 3 of 13 e2e tests, on a genuinely fresh database — something no local dry-run in this branch's history had (every local sandbox's Postgres volume had leftover state from many prior manual sessions, masking these bugs). Root causes, confirmed by reading the actual code:

1. Migration `e463496b1d0f_backfill_default_workspace` unconditionally inserts one empty "Default" (`fs-default`) workspace into every database it runs against, including a brand-new one with no pre-multi-tenant data to backfill — that workspace has zero members, forever, on a fresh install.
2. `_seed_personas()` in `backend/src/flowsage_backend/__main__.py:29-38` picks "the first workspace ordered by `created_at`, limit 1" and seeds personas there. Since the migration always runs before any CLI command, that memberless `fs-default` workspace is always the oldest workspace on a fresh install, so `seed-personas` always seeds the wrong workspace.
3. `create-user` (`backend/src/flowsage_backend/seed.py:24-38`, `upsert_user`) always creates a brand-new, separate workspace for a new user — never the migration's phantom one. So the e2e test user's real workspace never gets personas, and `e2e/critical-flows.spec.ts`'s "running a simulation reaches a terminal state" test times out waiting for the "Run Simulation" button to enable, and `e2e/workspace-settings.spec.ts`'s "workspace switcher" test fails on its very first assertion (`await expect(page.getByText("No personas loaded yet.")).not.toBeVisible();` at line 73), both for the same reason.
4. Separately: `backend/src/flowsage_backend/api/workspaces.py:203`'s `add_member` 404s with "No account with that email" if the invitee isn't an existing registered user. The CI job (and the manual `frontend/e2e/README.md` setup) never creates an `e2e-teammate@flowsage.dev` account, so `e2e/workspace-settings.spec.ts`'s "Team Settings: invite an existing user" test's invite 404s and the row never appears.

The user chose: fix `seed_personas`'s workspace-selection query to target a real (member-having) workspace rather than guarding/changing the migration; and add the missing teammate-user creation step to CI. No changes to any `frontend/e2e/*.spec.ts` file — all 3 failing tests' own expectations are correct as written per this diagnosis; re-read `e2e/workspace-settings.spec.ts`'s own comments ("Default workspace was seeded with 5 baseline personas -- the dashboard's 'Persona Insights' section should show them") confirming this.

**Files:**
- Modify: `backend/src/flowsage_backend/__main__.py` (the `_seed_personas` function only)
- Test: `backend/tests/test_seed.py` if it exists, else add a focused test file `backend/tests/test_seed_personas_cli.py` — check first with a Read/grep before deciding which
- Modify: `.github/workflows/ci.yml` (the `e2e` job: one new step, creating the teammate user)
- Modify: `frontend/e2e/README.md` (add the same missing manual step, for consistency with the CI job)

**Interfaces:**
- Consumes: `flowsage_backend.models.workspace.Workspace`, `flowsage_backend.models.membership.Membership` (check exact import path/model name via `grep -n "class Membership" backend/src/flowsage_backend/models/*.py` before writing the query — do not guess the module path).
- Produces: `_seed_personas()`'s workspace-selection query changes from `select(Workspace).order_by(Workspace.created_at).limit(1)` to one that joins/filters to workspaces having at least one `Membership` row, still ordered by `Workspace.created_at` ascending, limit 1 (i.e., "the oldest *real* workspace", not "the oldest workspace period"). The `SystemExit` message and overall function signature are unchanged.

- [ ] **Step 1: Confirm the exact Membership model/import path**

```bash
grep -n "class Membership" backend/src/flowsage_backend/models/*.py
```

Use whatever module path this prints (do not assume — earlier code in this same file already imports `Workspace` from `flowsage_backend.models.workspace`, so `Membership` is very likely `flowsage_backend.models.membership.Membership`, but confirm before writing the import).

- [ ] **Step 2: Write the failing test**

Check whether `backend/tests/test_seed.py` already exists and covers `seed_baseline_personas`/`upsert_user`:

```bash
ls backend/tests/ | grep -i seed
```

If it exists, add the new test there, following its existing fixture conventions (likely `db_session`, and probably a `create_workspace_and_admin` helper from `tests/conftest.py` — check `tests/conftest.py` for available helpers before writing). If no such file exists, create `backend/tests/test_seed_personas_cli.py`.

The new test must reproduce the exact bug: create a workspace with NO membership (simulating the migration's phantom workspace) with an EARLIER `created_at` than a second workspace that DOES have a membership, then call the actual function `_seed_personas` targets (whatever the fixed query logic becomes — since `_seed_personas` itself is a CLI-only async function that also opens its own DB engine via `get_settings()`/`create_engine`, it is not directly testable against the test DB session; **extract the workspace-selection query into a small, separately-testable function** as part of this fix, e.g. `async def _find_seedable_workspace(session: AsyncSession) -> Workspace | None`, and have `_seed_personas` call it). Write the test against that extracted function directly:

```python
async def test_find_seedable_workspace_skips_memberless_workspaces(
    db_session: AsyncSession,
) -> None:
    from flowsage_backend.__main__ import _find_seedable_workspace
    from flowsage_backend.models.workspace import Workspace
    # (import Membership from wherever Step 1 confirmed, and User if constructing
    # a membership requires a real user row -- check the Membership model's
    # required columns first)

    phantom = Workspace(name="Default", slug="fs-default-test")
    db_session.add(phantom)
    await db_session.flush()

    # a real workspace, created later, but WITH a membership
    real_workspace, membership = await create_workspace_and_admin(
        db_session, "seed-personas-test@example.com"
    )

    found = await _find_seedable_workspace(db_session)
    assert found is not None
    assert found.id == real_workspace.id
```

(Adjust to match whatever helper `tests/conftest.py` actually provides for "create a workspace with a real admin user/membership" — grep for `create_workspace_and_admin` first, since `backend/tests/test_insights.py` already uses exactly this helper per this same branch's earlier work.)

- [ ] **Step 3: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_seed_personas_cli.py -v  # or whichever file Step 2 used
```

Expected: FAIL — either `ImportError`/`AttributeError` (function doesn't exist yet) or an assertion failure (old query returns the phantom workspace instead).

- [ ] **Step 4: Write the implementation**

In `backend/src/flowsage_backend/__main__.py`, extract and fix the query:

```python
async def _find_seedable_workspace(session: AsyncSession) -> Workspace | None:
    """The oldest workspace that actually has at least one member -- skips
    over the empty 'Default' workspace `e463496b1d0f_backfill_default_workspace`
    unconditionally creates on every fresh install, which otherwise always wins
    a naive "oldest workspace" query since it's created before any real user."""
    result = await session.execute(
        select(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .order_by(Workspace.created_at)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _seed_personas() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        workspace = await _find_seedable_workspace(session)
        if workspace is None:
            raise SystemExit("No workspace exists yet -- run `create-user` first.")
        personas = await seed_baseline_personas(session, workspace.id)
    await engine.dispose()
    print(f"{len(personas)} baseline persona(s) ready: {', '.join(p.slug for p in personas)}")
```

Add the `Membership` import (whatever Step 1 confirmed) and `AsyncSession` (from `sqlalchemy.ext.asyncio`) to the top of the file if not already imported.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_seed_personas_cli.py -v
```

Expected: PASS.

- [ ] **Step 6: Run the full backend suite**

```bash
cd backend && uv run pytest -q
```

Expected: all tests pass (210 + however many this task added).

- [ ] **Step 7: Type-check and format**

```bash
cd backend
uv run autoflake8 --in-place --remove-all-unused-imports src/flowsage_backend/__main__.py tests/test_seed_personas_cli.py
uv run black src/flowsage_backend/__main__.py tests/test_seed_personas_cli.py
uv run mypy --strict src
```

(If `--remove-all-unused-imports` doesn't exist in this repo's pinned autoflake8, per an earlier task's finding in this same branch, run it without that flag — same resulting behavior.)

- [ ] **Step 8: Add the missing teammate-user CI step**

Modify `.github/workflows/ci.yml`'s `e2e` job — add a new step immediately after the existing `Create e2e test user` step:

```yaml
      - name: Create e2e teammate user
        run: |
          docker compose -f infra/docker-compose.yml exec -T backend \
            /workspace/.venv/bin/flowsage-backend create-user e2e-teammate@flowsage.dev supersecret123
```

- [ ] **Step 9: Add the same step to the manual README for consistency**

Modify `frontend/e2e/README.md`'s "## Setup" section — add a line after the existing `create-user e2e@flowsage.dev ...` line:

```bash
docker compose -f ../infra/docker-compose.yml exec backend \
  flowsage-backend create-user e2e-teammate@flowsage.dev supersecret123
```

- [ ] **Step 10: Verify the YAML is still well-formed**

```bash
cd /home/asus/Projects/personal/FlowSage/.claude/worktrees/phase4-ci-hardening
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "YAML OK"
```

- [ ] **Step 11: Commit**

```bash
git add backend/src/flowsage_backend/__main__.py backend/tests/test_seed_personas_cli.py .github/workflows/ci.yml frontend/e2e/README.md
git commit -m "fix: seed-personas targets the real (member-having) workspace, add missing e2e teammate user"
```

(Adjust the `git add` test-file path if Step 2 used a different existing file instead.)

- [ ] **Step 12: Push**

```bash
git push origin worktree-phase4-ci-hardening
```

---

### Task 3: Full verification and merge

**Files:** none (verification + merge only)

- [ ] **Step 1: Confirm the worktree is clean and all three jobs are green on the latest commit**

```bash
cd /home/asus/Projects/personal/FlowSage/.claude/worktrees/phase4-ci-hardening
git status
gh run list --branch worktree-phase4-ci-hardening --limit 1
```

Expected: clean working tree, latest run shows `python`, `frontend`, `e2e` all `success`.

- [ ] **Step 2: Merge to main**

```bash
cd /home/asus/Projects/personal/FlowSage
git checkout main
git pull --ff-only
git merge --no-ff worktree-phase4-ci-hardening -m "ci: add frontend and e2e jobs to GitHub Actions (Phase 4 hardening sub-chunk 1)"
git push origin main
```

- [ ] **Step 3: Confirm CI is green on `main` itself**

```bash
gh run list --branch main --limit 1
```

Expected: the merge commit's run shows all 3 jobs `success`. This is the first time `frontend`/`e2e` have ever run against `main` directly — a genuinely new signal, not a re-run of something already proven, since prior verification was all on the worktree branch.

- [ ] **Step 4: Remove the worktree**

```bash
git worktree remove .claude/worktrees/phase4-ci-hardening
git branch -d worktree-phase4-ci-hardening
git push origin --delete worktree-phase4-ci-hardening
```
