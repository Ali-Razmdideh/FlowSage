# CI Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new GitHub Actions jobs to `.github/workflows/ci.yml` — a fast `frontend` job (oxlint, `tsc`, vitest, production build) and a full-stack `e2e` job (docker-compose build + up, migrate/seed, Playwright against the real nginx-served build) — closing the gap where frontend and e2e checks have never run in CI, only manually. Closes Phase 4 item 4's first sub-bullet per `docs/superpowers/specs/2026-07-26-ci-hardening-design.md`.

**Architecture:** Both jobs are added as siblings of the existing `python` job in the same workflow file, triggered by the same `push`/`pull_request` events, running in parallel (no `needs:` gating between any of the three jobs). No test content changes — all 5 e2e specs, `playwright.config.ts`, and `infra/docker-compose.yml` already work correctly for this; this plan is CI wiring only.

**Tech Stack:** GitHub Actions, `actions/setup-node@v4`, npm, Playwright, Docker Compose (existing `infra/docker-compose.yml`, unmodified).

## Global Constraints

- No changes to `frontend/playwright.config.ts`, `infra/docker-compose.yml`, or any `frontend/e2e/*.spec.ts` file — they are already correct.
- No changes to the existing `python` job in `.github/workflows/ci.yml`.
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
