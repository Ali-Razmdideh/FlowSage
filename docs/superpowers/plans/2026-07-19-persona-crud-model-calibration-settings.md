# Phase 2 chunk 4/4 — Persona library CRUD + /settings/model-calibration

## Scope decision (deviates from the prototype on purpose)

`design-hifi-prototypes/settings_model_calibration_flowsage` shows a multi-tenant
"FlowSage Workspace" shell (Analytics/Reports nav, notifications bell, "Upgrade
Plan") and several controls with no real backend concept behind them (Kalman
Filter smoothing factor, latency budget, "Anomalous Spike Shield", drift/health
gauges). Multi-tenant workspace chrome is Phase 3 scope, and per the standing
build rule ("don't build fantasy" — see `calibration.py`'s deliberately
deterministic "AI Insight" text, and the dropped "Digital Twin Training"
flourishes from the Journey Graph build), this chunk only ships controls that
actually change backend behavior:

- **Global Inference Confidence** → `CalibrationSettings.anomaly_threshold`,
  replaces the hardcoded `calibration.ANOMALY_THRESHOLD` (0.35). Relabeled
  "Calibration Anomaly Threshold" since it's a delta tolerance, not a
  confidence score.
- **Retraining Triggers** → one real toggle, `auto_retrain_on_anomaly`. When
  on, the digest job enqueues a retraining job for every persona with an
  open calibration anomaly instead of requiring a manual click in
  `/calibration`. ("Anomalous Spike Shield" / "Historical Weighting" have no
  backing logic — dropped.)
- **Simulation Frequency** → repurposed as **Digest Frequency**
  (daily/weekly), because the only recurring job in the system is the alerts
  digest (`run_weekly_digest_job`); there's no stored "scenario" concept to
  attach a simulation-rerun cadence to yet. Real wiring: cron now fires
  daily, and the job checks `digest_frequency` + `digest_last_sent_at`
  before actually sending.
- Churn-risk alert threshold (`alerts.CHURN_RISK_ALERT_THRESHOLD`, currently
  hardcoded 0.5) is folded into the same settings row since it's the other
  fixed threshold called out in `alerts.py`'s own docstring as a deliberate
  simplification.

Persona Configuration screen
(`design-hifi-prototypes/predictive_engine_persona_configuration`) maps
directly onto the existing `Persona`/`PersonaMemory` models — built as
designed, including "Reset Default" (baseline personas only, re-reads
`flowsage_predict.personas.find_baseline_persona`) and Persona Memory Bank
(existing `PersonaMemory` rows, already populated by retraining jobs).

## Backend

1. `CalibrationSettings` model (singleton row, `get_or_create` accessor) +
   migration. Fields: `anomaly_threshold`, `churn_risk_alert_threshold`,
   `auto_retrain_on_anomaly`, `digest_frequency` (enum daily/weekly),
   `digest_last_sent_at`, `updated_at`.
2. `calibration.py` / `alerts.py`: thresholds become parameters (default =
   today's hardcoded constants, so existing tests/behavior are unchanged
   unless a settings row overrides them); `build_calibration_report` /
   `build_alerts_report` read the singleton.
3. `api/settings.py`: `GET/PATCH /settings/model-calibration`, auth-gated.
4. `worker.py`: `run_weekly_digest_job` → `run_digest_job`, cron daily 9am;
   no-ops (no send, no `digest_last_sent_at` bump) unless due per
   `digest_frequency`. When `auto_retrain_on_anomaly` is set, enqueues
   `run_retraining_job` for each anomalous persona lacking an in-flight job.
5. Persona CRUD on the existing `personas` router: `GET/POST/PATCH/DELETE
   /personas/{id}`, `POST /personas/{id}/reset` (baseline-only, 409
   otherwise). Delete: 409 if the persona has `SimulationRun` history (FK is
   RESTRICT, catch `IntegrityError`); baseline personas can't be deleted
   (only reset).
6. Tests for all of the above; full existing suite must still pass (touching
   shared threshold/cron code — same lesson as the chunk 3 test-regression
   gotcha).

## Frontend

7. `lib/types.ts` / `lib/api.ts`: `CalibrationSettings`, persona
   create/update payload types, matching API client methods.
8. `routes/settings/ModelCalibrationSettingsPage.tsx` at
   `/settings/model-calibration`; add a "Settings" sidebar nav item.
9. `routes/predictive/PersonaConfigurationPage.tsx` at
   `/predictive/personas/:id` (and `/predictive/personas/new`): behavioral
   sliders (read/write), demographic anchors, contextual triggers, memory
   bank list, Reset Default / Delete / Save.
10. `PredictiveEnginePage.tsx`: persona cards link to the configuration
    page; add "New Persona".
11. Component tests for the two new pages.

## Verification (per standing build rule)

autoflake8 → black → mypy --strict; oxlint + tsc --strict; full backend +
frontend test suites; safety/bug/style review pass; `docker compose up
--build` smoke test (seed, create/edit/delete a persona, toggle settings,
confirm digest job reads them); commit + push to `main`.
