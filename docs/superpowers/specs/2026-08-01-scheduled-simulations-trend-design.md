# Scheduled Simulations + Friction Trend — Design

## Problem

The predictive engine (`POST /simulations`) only runs one-shot: a user uploads a
screenshot sequence, gets a friction report, done. There's no way to catch
regressions across releases without a human re-triggering it by hand. The
Journey Graph and Calibration Loop already give the observational side
continuity over time; the predictive side has none.

## Scope

In scope:
- Recurring simulation configs per workspace, triggered on a schedule or on
  a fresh screenshot-set push.
- A friction-score trend per config, computed from existing severity data.
- Regression alerts riding the existing alerts/digest pipeline.

Out of scope (explicitly deferred):
- Live-URL browser capture (Playwright screenshot pipeline). Today's
  `POST /simulations` only accepts uploaded files (`simulations.py`); no
  code anywhere in the repo captures screenshots from a live URL, despite
  the top-level README describing that as an input mode. Building that
  capture pipeline is its own feature. This design instead adds an
  API-push trigger: an external caller (CI, a script, a person) uploads a
  fresh screenshot set for a config, and the schedule picks it up on its
  next due check.

## Data model

New table `scheduled_simulations`:

| column | type | notes |
|---|---|---|
| `id` | uuid pk | |
| `workspace_id` | uuid fk → workspaces, cascade delete | indexed |
| `flow_name` | str(200) | mirrors `SimulationRun.flow_name` |
| `goal` | str(500) | mirrors `SimulationRun.goal` |
| `persona_id` | uuid fk → personas | |
| `interval` | enum: `daily` / `weekly` / `on_push` | |
| `active` | bool, default true | pause without deleting |
| `pending_screenshots_dir` | str, nullable | set by the push endpoint, cleared once consumed |
| `last_fired_at` | datetime, nullable | |
| `last_run_id` | uuid fk → simulation_runs, nullable | |
| `created_by` | uuid fk → users | |
| `created_at` | datetime | |

No new score column anywhere — friction score is always computed on read
(see Scoring below) so it can't drift out of sync if severity weights ever
change.

## API

- `POST /scheduled-simulations` — create a config (flow_name, goal,
  persona_id, interval).
- `GET /scheduled-simulations` — list configs for the workspace.
- `PATCH /scheduled-simulations/{id}` — edit interval/active/goal.
- `DELETE /scheduled-simulations/{id}`
- `POST /scheduled-simulations/{id}/screenshots` — stage a fresh screenshot
  set (multipart upload, same shape as `POST /simulations`'s `files`
  field). Replaces any not-yet-consumed pending set.
- `GET /scheduled-simulations/{id}/trend` — `[{run_id, created_at, score,
  issue_count}]`, ordered oldest→newest, one entry per run that config has
  fired.

All routes follow the existing workspace-scoping and role-check pattern
used across `api/*.py` (membership dependency, workspace_id filter on every
query).

## Scheduling mechanism

Reuse the pattern already in `worker.py`'s `cron_jobs` list (`run_digest_job`,
`run_retention_purge_job`): a single arq cron job fixed at process start
(`run_scheduled_simulations_job`, e.g. hourly) that iterates all active
`ScheduledSimulation` rows across workspaces and decides per-row whether
it's due — the per-row "due" check is what actually encodes daily/weekly/
on_push cadence, not the cron spec itself (same reason the digest job
already works this way: arq's cron timing is fixed, so cadence variance
has to live in application logic).

Due check:
- `daily`: `pending_screenshots_dir is not None` and (`last_fired_at is None`
  or `now - last_fired_at >= 24h`)
- `weekly`: same but `>= 7d`
- `on_push`: `pending_screenshots_dir is not None` (fires as soon as a
  fresh set lands, no time gate)

On fire: call the existing `create_run()` helper from `flowsage_backend.simulations`
(same function `simulations.py`'s `POST /simulations` uses) with the
config's `flow_name`/`goal`/`persona_id` and the pending screenshots dir,
then set `last_run_id`, `last_fired_at = now`, and clear
`pending_screenshots_dir`.

## Scoring & trend

No new scoring model. Reuse `calibration.py`'s `_SEVERITY_SCORES` map and
`predicted_scores_by_screen()` (max severity score per screen, 0–1 scale).
A run's trend-point score is the mean of `predicted_scores_by_screen(run.issues)`
across all screens touched in that run. Computed on read in the `/trend`
endpoint handler — never stored.

## Regression alerts

Extend `alerts.py` following its existing `CalibrationAlert`/`ChurnAlert`
shape:

```python
class FrictionRegressionAlert(BaseModel):
    scheduled_simulation_id: uuid.UUID
    flow_name: str
    previous_score: float
    current_score: float
    delta: float
```

`check_friction_regression_alerts()` compares a scheduled config's latest
fired run against the one before it; fires when `delta >= 0.15`. Folded
into `AlertsReport` and `build_alerts_report()`, so it flows through the
existing Slack/Jira digest text/blocks builders (`build_digest_text`,
`build_digest_blocks`) with no new notification infrastructure.

## Frontend

New "Scheduled runs" tab inside `frontend/src/routes/predictive`:
- Config list + create/edit form (flow_name, goal, persona picker,
  interval select, active toggle).
- A push-screenshots affordance per config (same file-picker UX as the
  existing one-off run form).
- Trend line per config, rendered as a hand-rolled inline SVG polyline —
  same approach as `AccuracyScatter` in `CalibrationPage.tsx`, no charting
  library added.

## Testing

Backend (pytest):
- Due-check boundary cases for all three intervals (just-under vs
  just-over the threshold, `on_push` firing immediately, no pending set
  never fires).
- Regression-alert threshold (`delta` just under/over 0.15).
- `/trend` endpoint scoring math against a known set of `FrictionIssue`
  fixtures.
- Workspace isolation on every new route (existing project convention —
  see e.g. `test_insights_funnel_isolates_by_workspace`).

Frontend (Vitest + Testing Library):
- New tab renders config list, create/edit form validation, trend SVG
  renders expected point count, following the existing route test
  patterns (`CalibrationPage.test.tsx`).
