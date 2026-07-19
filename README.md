# FlowSage

**Predictive & Observed UX Intelligence Platform**

FlowSage merges two halves of UX analytics that today live in separate tools: predicting friction before launch, and measuring it after.

- **Predictive engine** — multimodal LLM persona agents walk through a product's UI (Figma exports, screenshots, or a live staging URL) and produce a structured friction report before a single real user touches the flow.
- **Observational engine** — real user event streams are modeled as a temporal graph in Neo4j; every screen, tap, and hesitation becomes a node or edge, surfacing where journeys stall, loop, or die.
- **Calibration loop** — FlowSage compares synthetic persona predictions against real user behavior, scores its own accuracy, and refines the personas over time, converging toward calibrated digital twins of the real user base.

Together these answer three questions no current tool answers in one place: where users *will* struggle, where they *are* struggling, and how good the platform is getting at predicting the difference.

## Features

**Predictive engine (synthetic users)**

- Configurable LLM personas (novice, power user, accessibility-constrained, low-patience mobile, non-native speaker)
- Traverses Figma files, screenshot sequences, or crawled staging environments
- Evaluates against Nielsen heuristics plus custom rubrics
- Friction reports with severity scores, screen-level annotations, suggested fixes
- Multimodal vision catches visual issues (contrast, tap-target size, misleading affordances), not just flow logic

**Observational engine (journey graph)**

- SDK/webhook ingestion of product event streams into a temporal user-journey graph
- Automatic funnel discovery, no manual funnel definitions
- Friction-node detection: abnormal drop-off, rage-loops, backtracking
- Cohort path comparison
- LLM-generated plain-language explanations of *why* a drop-off is likely failing, grounded in session context
- Churn prediction per segment with ranked re-engagement recommendations

**Calibration loop (the differentiator)**

- Prediction-vs-reality scoring: every pre-launch persona prediction matched against post-launch graph evidence
- Persona accuracy dashboards over time
- Miscalibrated personas auto-retrained on real behavioral data

**Workflow layer**

- Trend tracking and alerting on emerging friction
- Slack/Jira integration that auto-files annotated tickets
- Weekly digest reports
- Insights API for downstream tooling



## Tech Stack

- **Agentic orchestration:** LangGraph for persona simulation
- **Graph modeling:** Neo4j for temporal user-journey graphs
- **Vision/LLM:** multimodal models for reading UI screenshots and generating friction reports
- **API:** FastAPI



## Development

The full build sequence lives in [plans/full-project-coding-plan.md](plans/full-project-coding-plan.md).

- [scripts/flowsage-predict](scripts/flowsage-predict) — Phase 0 CLI: LLM persona walkthrough of a screenshot sequence -> Markdown friction report.
- [scripts/flowsage-graph](scripts/flowsage-graph) — Phase 0 CLI: event log -> Neo4j journey graph -> automatic funnel discovery -> HTML report.
- [backend](backend) — Phase 1 FastAPI app: auth, the simulations API (wraps flowsage-predict in an arq job), and the events/journey-graph API (wraps flowsage-graph).
- [frontend](frontend) — Phase 1 React app: login, dashboard, Predictive Engine, Journey Graph.

The two scripts and the backend are all `uv` workspace members (single lockfile at
the repo root); the backend depends on both scripts as libraries rather than
reimplementing their logic. Every component ships a `Dockerfile`, and
`infra/docker-compose.yml` brings up the whole stack (Postgres, Redis, Neo4j,
backend, worker, frontend) with one command. Copy `.env.example` to `.env` and fill
in `ANTHROPIC_API_KEY` to get started.

```bash
docker compose -f infra/docker-compose.yml up -d
docker compose -f infra/docker-compose.yml exec backend \
  python -m alembic -c /workspace/backend/alembic.ini upgrade head
docker compose -f infra/docker-compose.yml exec backend flowsage-backend seed-personas
docker compose -f infra/docker-compose.yml exec backend \
  flowsage-backend create-user admin@example.com supersecret123
# -> http://localhost:5173
```



## Roadmap


| Phase                            | Timeline        | Goal                                                                                                                                                                                     |
| -------------------------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0 — Hobby / Soft launch          | Jul 16–18, 2026 | Two standalone scripts: screenshot-sequence persona walkthrough → markdown friction report, and event-log → Neo4j journey graph with funnel visualization. Open-sourced.                 |
| 1 — MVP                          | Jul 19–21, 2026 | Single web app: Figma/screenshot upload → persona panel → friction report; simple event stream → funnel view with friction nodes and LLM explanations. Single-tenant, manual onboarding. |
| 2 — MLP                          | Jul 22–24, 2026 | Prediction-vs-reality dashboard, cohort comparison, churn-risk scoring, trend alerts, Slack/Jira auto-ticketing, configurable/savable personas.                                          |
| 3 — Beta                         | Jul 25–27, 2026 | Multi-tenant architecture, workspace roles, SOC2-track security, 10–20 pilot companies (Malaysian startup ecosystem beachhead), case studies.                                            |
| 4 — Final release / Monetization | Jul 28–31, 2026 | Tiered SaaS subscription, Figma plugin (self-serve persona audits), enterprise digital-twin engagements, freemium floor.                                                                 |


