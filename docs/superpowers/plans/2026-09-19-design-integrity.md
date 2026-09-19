# Design-integrity fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make calibration, rage-loop detection, and retention controls accurately reflect evidence and protect in-flight work.

**Architecture:** Calibration gains explicit evidence state at its existing API boundary and only uses supported comparisons for anomaly and accuracy calculations. The graph detector narrows rage-loop classification using event identity and elapsed time. The worker extends its existing upload-directory cleanup with a path-confined lifecycle-aware purge, while frontend copy renders the new evidence semantics honestly.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Pydantic, pytest, React, TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-19-design-integrity-design.md`

## Global Constraints

- Missing funnel rows and rows with fewer than ten entered sessions are insufficient evidence, never zero observed friction.
- Only evidence-backed comparisons may create anomalies, accuracy points, cached narratives, or retraining inputs.
- A rage loop requires three identical events on one screen within ten seconds in one session.
- Retention may delete only completed or failed expired simulation directories below the configured upload root; queued, running, and pending directories remain protected.
- Region wording must not claim compliance guarantees; retention wording must name raw events, audit entries, and completed simulation images.

## Review Focus

- A screen absent from the funnel remains visible as unknown evidence and cannot trigger retraining.
- A screen with nine sessions cannot produce an anomaly or false 100% accuracy.
- A simulated screen with no predicted issue can still reveal observed friction when its sample is sufficient.
- Three different events on checkout never classify as rage, while three matching clicks inside ten seconds do.
- A database screenshot path outside the upload root is never removed.

---

### Task 1: Evidence-aware calibration API

**Files:**
- Modify: `backend/src/flowsage_backend/calibration.py`
- Modify: `backend/src/flowsage_backend/retraining.py`
- Test: `backend/tests/test_calibration.py`
- Test: `backend/tests/test_retraining.py`

**Interfaces:**
- Consumes: `FunnelStep(screen, sessions_entered, sessions_continued)` and `SimulationRun.steps`.
- Produces: `ScreenCalibration(screen, predicted_score, observed_score: float | None, delta: float | None, sessions_entered, has_evidence, anomaly)` and `build_screen_calibrations(predicted, walked_screens, funnel, anomaly_threshold)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_missing_funnel_screen_is_unknown_evidence() -> None:
    result = build_screen_calibrations({"checkout": 0.7}, {"checkout"}, [])
    assert result[0].has_evidence is False
    assert result[0].observed_score is None
    assert result[0].delta is None
    assert result[0].anomaly is False

def test_unpredicted_walked_screen_can_be_anomalous_with_evidence() -> None:
    result = build_screen_calibrations({}, {"checkout"}, [FunnelStep(screen="checkout", sessions_entered=10, sessions_continued=2)])
    assert result[0].predicted_score == 0
    assert result[0].anomaly is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../.venv/bin/pytest tests/test_calibration.py -q`

Expected: FAIL because evidence fields and walked-screen comparison do not exist.

- [ ] **Step 3: Write minimal implementation**

Add `MIN_CALIBRATION_SESSIONS = 10`, make observed score and delta nullable, and build results from the union of predicted and walked screens. A row has evidence only when a matching funnel row has at least ten entered sessions. Load `SimulationRun.steps` in both latest-run queries. Compute report accuracy from evidence-backed rows only and pass walked screens through narrative and retraining paths. Anomaly-only code may use non-null observed score and delta because `anomaly` implies evidence.

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/bin/pytest tests/test_calibration.py tests/test_retraining.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/flowsage_backend/calibration.py backend/src/flowsage_backend/retraining.py backend/tests/test_calibration.py backend/tests/test_retraining.py
git commit -m "fix(calibration): require observed evidence"
```

### Task 2: Time-bounded, action-specific rage loops

**Files:**
- Modify: `scripts/flowsage-graph/src/flowsage_graph/funnel.py`
- Test: `scripts/flowsage-graph/tests/test_funnel.py`

**Interfaces:**
- Consumes: ordered `Event(screen, event, timestamp)` values.
- Produces: `detect_friction(..., rage_loop_window_seconds: float = 10)` where rage-loop nodes represent matching event runs only.

- [ ] **Step 1: Write the failing tests**

```python
def test_rage_loop_ignores_distinct_actions_on_one_screen() -> None:
    events = [_event("s1", "checkout", 0, "page_view"), _event("s1", "checkout", 1, "focus"), _event("s1", "checkout", 2, "type")]
    assert not [node for node in detect_friction(events, discover_funnel(events)) if node.kind == FrictionKind.RAGE_LOOP]

def test_rage_loop_expires_after_window() -> None:
    events = [_event("s1", "checkout", 0, "click"), _event("s1", "checkout", 6, "click"), _event("s1", "checkout", 12, "click")]
    assert not [node for node in detect_friction(events, discover_funnel(events)) if node.kind == FrictionKind.RAGE_LOOP]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../.venv/bin/pytest scripts/flowsage-graph/tests/test_funnel.py -q`

Expected: FAIL because the existing counter only compares screen names.

- [ ] **Step 3: Write minimal implementation**

Add `DEFAULT_RAGE_LOOP_WINDOW_SECONDS = 10`. Track the run key as `(event.screen, event.event)` and reset it if the event key changes or the elapsed time since the run began exceeds the window. Preserve one affected-session count per screen and include the window in the friction detail.

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/bin/pytest scripts/flowsage-graph/tests/test_funnel.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/flowsage-graph/src/flowsage_graph/funnel.py scripts/flowsage-graph/tests/test_funnel.py
git commit -m "fix(graph): narrow rage-loop detection"
```

### Task 3: Lifecycle-aware screenshot retention

**Files:**
- Modify: `backend/src/flowsage_backend/worker.py`
- Test: `backend/tests/test_retention_purge.py`

**Interfaces:**
- Consumes: `SimulationRun(status, screenshots_dir, finished_at, created_at)` and configured `upload_dir`.
- Produces: `_purge_expired_simulation_screenshots(session, workspace_id, retention_days, upload_dir) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_purge_removes_expired_completed_run_directory(...):
    run = SimulationRun(status=RunStatus.COMPLETED, screenshots_dir=str(expired_dir), finished_at=old_time, ...)
    await _purge_expired_simulation_screenshots(session, workspace_id, 30, upload_dir)
    assert not expired_dir.exists()

async def test_purge_keeps_queued_run_and_path_outside_upload_root(...):
    ...
    assert queued_dir.exists()
    assert outside_dir.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../.venv/bin/pytest tests/test_retention_purge.py -q`

Expected: FAIL because only scheduled push directories are currently purged.

- [ ] **Step 3: Write minimal implementation**

Query only completed and failed runs whose `finished_at` (falling back to `created_at`) predates the retention cutoff. Resolve both root and candidate path; remove only candidates below the resolved upload root. Call the helper beside scheduled cleanup, preserving scheduled pending/in-flight protection. Skip malformed or inaccessible records while logging a sanitized warning.

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/bin/pytest tests/test_retention_purge.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/flowsage_backend/worker.py backend/tests/test_retention_purge.py
git commit -m "fix(retention): purge expired simulation files"
```

### Task 4: Honest calibration and settings UI

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/routes/calibration/CalibrationPage.tsx`
- Modify: `frontend/src/routes/calibration/CalibrationPage.test.tsx`
- Modify: `frontend/src/routes/settings/GeneralSettingsPage.tsx`
- Modify: `frontend/src/routes/settings/GeneralSettingsPage.test.tsx`

**Interfaces:**
- Consumes: nullable `ScreenCalibration.observed_score`/ `delta` and `has_evidence` from the calibration API.
- Produces: an evidence-awaiting state when `accuracy_points` is empty and row-level evidence messaging.

- [ ] **Step 1: Write the failing tests**

```tsx
it("does not call absent evidence optimized", async () => {
  mockReport({ personas: [], accuracy_points: [], has_anomaly: false });
  render(<CalibrationPage />);
  expect(await screen.findByText("Calibration awaiting evidence")).toBeInTheDocument();
  expect(screen.queryByText("System Optimized")).not.toBeInTheDocument();
});

it("labels region and retention accurately", async () => {
  render(<GeneralSettingsPage />);
  expect(await screen.findByText("Workspace region")).toBeInTheDocument();
  expect(screen.getByText(/raw events, audit entries, and completed simulation images/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --run src/routes/calibration/CalibrationPage.test.tsx src/routes/settings/GeneralSettingsPage.test.tsx`

Expected: FAIL because an empty report renders “System Optimized”.

- [ ] **Step 3: Write minimal implementation**

Update frontend calibration types with nullable measurement fields and evidence metadata. Render “Insufficient evidence” and session count for unsupported rows. When no anomaly and no accuracy points exist, display “Calibration awaiting evidence” and explain that matching observed events are needed; otherwise retain the optimized state. Rename the settings label to “Workspace region” and state that the policy deletes raw events, audit entries, and completed simulation images.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test -- --run src/routes/calibration/CalibrationPage.test.tsx src/routes/settings/GeneralSettingsPage.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/routes/calibration/CalibrationPage.tsx frontend/src/routes/calibration/CalibrationPage.test.tsx frontend/src/routes/settings/GeneralSettingsPage.tsx frontend/src/routes/settings/GeneralSettingsPage.test.tsx
git commit -m "fix(ui): report calibration evidence honestly"
```

### Task 5: Whole-change verification and delivery

**Files:**
- Modify: none unless verification identifies a defect.

**Interfaces:**
- Consumes: all completed tasks.
- Produces: a reviewed commit pushed to `origin/main`.

- [ ] **Step 1: Run backend and graph suites**

Run: `../.venv/bin/pytest -q` from `backend/`, then the graph package test command from its project directory.

Expected: PASS.

- [ ] **Step 2: Run frontend validation**

Run: `npm run lint && npm run typecheck && npm test -- --run && npm run build`

Expected: PASS with no TypeScript errors.

- [ ] **Step 3: Review delivery diff**

Run: `git diff origin/main...HEAD --check && git status --short`

Expected: no whitespace errors and only intended files.

- [ ] **Step 4: Integrate remote updates and push**

```bash
git fetch origin
git rebase origin/main
git push origin main
git ls-remote origin refs/heads/main
```

Expected: remote `main` resolves to the final local commit without force-pushing.
