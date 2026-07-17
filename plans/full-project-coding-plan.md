# FlowSage — Full Project Coding Plan

## Context

FlowSage is a greenfield "Predictive & Observed UX Intelligence Platform" (see [README.md](../README.md)). The repo currently contains only the README and 15 hi-fi HTML prototypes under `design-hifi-prototypes/` (Alexandria design system: Material-3-style tokens, Noto Serif headlines, Inter body, Public Sans labels, primary `#094cb2`, no 1px borders, tonal surfaces). This plan turns the README roadmap (Phases 0–4, Jul 16–31 2026) and the prototypes into an executable build sequence.

Three product engines, per README:
1. **Predictive engine** — LangGraph LLM persona agents walk UI (screenshots/Figma/staging URL) → friction reports.
2. **Observational engine** — event streams → Neo4j temporal journey graph → friction-node detection.
3. **Calibration loop** — predicted vs observed friction scoring → persona retraining → convergence.

## Architecture & Stack

- **Monorepo** at repo root:
  ```
  backend/          FastAPI (Python 3.12, uv), SQLAlchemy + Alembic
  frontend/         Vite + React 18 + TypeScript + Tailwind (Alexandria tokens) + React Router
  scripts/          Phase 0 standalone CLIs (flowsage-predict, flowsage-graph)
  infra/            docker-compose: Neo4j 5, Postgres 16, Redis 7
  design-hifi-prototypes/  (existing, reference only)
  ```
- **Databases:** Postgres = app data (workspaces, users, personas, runs, issues, calibration records, keys, webhooks). Neo4j = temporal journey graph only (nodes = screens/states, edges = transitions with timestamps, weights, drop-off).
- **Jobs/realtime:** arq (Redis) worker for simulation + retraining jobs; SSE endpoints for live simulation log and retraining progress (prototypes show live agentic log at 65% and retraining shimmer at 68%).
- **LLM:** Anthropic Claude multimodal via LangGraph (`langchain-anthropic`). Persona agent graph: `load_persona → perceive_screen (vision) → decide_action → detect_friction → advance/abandon → report`. Use latest Sonnet-class model for vision steps; make model configurable per persona.
- **Frontend charts/canvas:** React Flow for the two node canvases (Predictive Engine flow canvas, Journey Graph). Hand-built SVG (matching prototypes) for bar/line/scatter charts — no heavy chart lib.
- **Auth:** Phase 1 = single-tenant email+password (JWT httpOnly cookie). Phase 3 = multi-tenant workspaces + roles (Admin / Researcher / Viewer, per team-access prototype).

## Core Data Model (Postgres)

- `Workspace` (name, slug `fs-…`, description, avatar, privacy, region, retention_days, archived)
- `User`, `Membership(user, workspace, role)`
- `Persona` (name, baseline flag, description, demographic anchors: tech_affinity/device/discovery_mode, contextual trigger tags, sliders: technical_literacy/anxiety/patience/curiosity 0–1, model_id) + `PersonaMemory` (title, note, kind, created_at)
- `Project/Flow` (name, source_type: screenshots|figma|staging_url, artifacts)
- `SimulationRun` (flow, persona, goal_path, status, progress, started/finished) + `SimulationStep` (label, detail, duration, status, tags) — feeds Agentic Orchestration log
- `FrictionIssue` (run, screen_ref, severity, title, heuristic_violated, persona_impact, description, drop_off_pct, remediation_code, status)
- `CalibrationRecord` (flow_step, predicted_score, observed_score, delta) ; `PersonaAccuracy` (persona, cohort, match_pct, complexity) ; `RetrainingJob` (persona, status, epoch, loss, progress)
- `ApiKey` (prefix `fs_prod_`/`fs_stg_`, hashed, last_used) ; `WebhookEndpoint` (url, events[], status) ; `Integration` (slack|jira|figma, config)
- Raw events land via `POST /v1/events` (API-key auth) → queued → Neo4j upsert. Neo4j schema: `(:Screen {name})-[:TRANSITION {ts, session_id, cohort, device, count, avg_dwell}]->(:Screen)`, plus `(:Session)`, friction annotations as node properties.

## Screen → Route Map (frontend)

Shell: 288px sidebar (New Simulation CTA, Predictive Engine / Journey Graph / Calibration / Settings, Support/Archive, profile chip) + sticky top bar (search, Sync Events, notifications). Journey Graph uses full-canvas variant with bottom stats bar.

| Route | Prototype |
|---|---|
| `/dashboard` | project_dashboard (KPIs, friction trends chart, top friction nodes, persona insights) |
| `/predictive` | predictive_engine (flow canvas + friction report sheet + simulation settings aside) |
| `/predictive/personas/:id` | persona_configuration (anchors, sliders, memory bank) |
| `/predictive/runs/:id` | running_simulation (live viewport + agentic log, SSE) |
| `/predictive/issues/:id` | friction_detail (artifact magnifier, heuristic analysis, remediation code, Apply-to-Figma/Copy) |
| `/journey` | journey_graph (React Flow canvas, cohort/date/device filters, Node Intelligence aside) + empty state (Awaiting Event Ingestion, Import Sample Data) |
| `/journey/nodes/:id` | rage_click_detail (timeline, heatmap, metrics, Export to Jira) |
| `/calibration` | calibration_insights — three states: anomaly (prediction-vs-reality table + digital twin training + accuracy scatter), retraining (progress + inference scoring), optimized (convergence 98.4% + log) |
| `/settings/{general,integrations,team,model-calibration}` | four settings prototypes |

## Build Phases (per README roadmap)

### Phase 0 — Hobby scripts (Jul 16–18)
1. Scaffold monorepo, `infra/docker-compose.yml` (Neo4j+Postgres+Redis), CI lint/test.
2. `scripts/flowsage-predict/`: CLI — screenshot dir + persona YAML → LangGraph walkthrough → `friction_report.md` (severity, heuristic, per-screen annotations, suggested fixes). Ship 5 baseline persona YAMLs from README (novice, power user, accessibility-constrained, low-patience mobile, non-native speaker).
3. `scripts/flowsage-graph/`: CLI — event log (CSV/JSONL: session_id, event, screen, ts, device, cohort) → Neo4j ingest → auto funnel discovery (Cypher path aggregation) → static HTML funnel viz + friction-node list (abnormal drop-off, rage-loops via rapid repeat events, backtracking).
4. Sample dataset in `scripts/sample_data/` (powers "Import Sample Data" later). Open-source polish: LICENSE, script READMEs.

### Phase 1 — MVP web app (Jul 19–21)
1. **Backend:** FastAPI app factory, auth, uploads (screenshot sequences), `POST /simulations` → arq job wrapping Phase 0 predict logic → persists `SimulationRun/Step/FrictionIssue`; SSE `/simulations/{id}/stream`; `POST /v1/events` ingestion → Neo4j; graph query endpoints (funnel, node detail, filters cohort/date/device); LLM node-explanation endpoint (grounded in session context).
2. **Frontend:** Tailwind config = Alexandria tokens extracted from prototypes; shell layout; Dashboard; Predictive Engine canvas + run + live log + friction detail; Journey Graph (empty state → populated) + Node Intelligence aside.
3. Single-tenant seed user, manual onboarding.

### Phase 2 — MLP: calibration + workflow (Jul 22–24)
1. **Calibration engine:** matcher joins predicted `FrictionIssue` ↔ observed Neo4j drop-off per flow step → `CalibrationRecord` deltas; anomaly detection (|delta| threshold → alert banner); persona accuracy scatter data; retraining job = re-prompt persona with recent Neo4j behavioral evidence appended to `PersonaMemory` + slider re-fit; three calibration UI states.
2. Cohort path comparison, churn-risk scoring per segment + ranked re-engagement recommendations (Node Intelligence panel).
3. Trend tracking + alert rules; Slack webhook + Jira issue auto-filing (annotated tickets, "Export to Engineering Ticket"/"Export to Jira" buttons); weekly digest job.
4. Persona library CRUD + `/settings/model-calibration` (inference confidence, simulation frequency, retraining triggers).

### Phase 3 — Beta: multi-tenant (Jul 25–27)
1. Workspace model everywhere (row-level scoping), invites, roles enforcement (Admin/Researcher/Viewer), `/settings/team` + `/settings/general` (archive workspace, retention policy, region).
2. `/settings/integrations`: API key issue/revoke (hashed, prefix display), webhook endpoints + delivery log, marketplace cards.
3. SOC2-track: audit log table + Security Logs view, rate limiting, secrets hygiene, per-workspace Neo4j label isolation.
4. Pilot onboarding tooling: sample-data import, setup guide page.

### Phase 4 — Release/monetization (Jul 28–31)
1. Stripe subscription tiers + freemium limits (runs/month, events/month, seats), Upgrade Plan CTA.
2. Public Insights API (`/v1/insights/...`) documented via OpenAPI.
3. Figma plugin (separate `figma-plugin/` package): select frames → call FlowSage API → inline friction annotations ("Apply to Figma" round-trip).
4. Hardening: e2e suite, load test ingestion, deploy (Fly.io/Render or VPS docker-compose), landing/docs.

## Verification

- Backend: pytest per module; calibration matcher unit tests with fixture predicted/observed pairs.
- Graph: Cypher tests against ephemeral Neo4j container (testcontainers).
- Frontend: Vitest component tests; Playwright e2e per phase gate — Phase 1 gate: upload screenshots → run sim → see live log → friction report renders; ingest sample events → journey graph renders with drop-off badges.
- Each phase ends with `docker compose up` + seeded demo walkthrough matching its prototype screens.
