# flowsage-frontend

React 19 + TypeScript (strict) + Vite frontend for FlowSage. Talks to `backend/` through cookie-based session auth (`credentials: "include"`) — see `src/lib/api.ts`.

Visual language is the "Alexandria" design system: Noto Serif headlines, Inter body, Public Sans labels, primary `#094cb2`, tonal surfaces instead of 1px borders — see the `@theme` block in `src/index.css`.

## Setup

```bash
cd frontend
npm install
cp .env.example .env   # only needed if VITE_API_BASE_URL should differ from /api
```

## Run

```bash
npm run dev   # http://localhost:5173, proxies /api -> http://localhost:8000 (see vite.config.ts)
```

Needs the backend stack running — see [`../backend/README.md`](../backend/README.md) and `../infra/docker-compose.yml`. Quickest path:

```bash
docker compose -f ../infra/docker-compose.yml up -d postgres redis neo4j backend worker
docker compose -f ../infra/docker-compose.yml exec backend \
  /workspace/.venv/bin/python -m alembic -c /workspace/backend/alembic.ini upgrade head
docker compose -f ../infra/docker-compose.yml exec backend \
  /workspace/.venv/bin/flowsage-backend seed-personas
docker compose -f ../infra/docker-compose.yml exec backend \
  /workspace/.venv/bin/flowsage-backend create-user admin@example.com supersecret123
```

## Development

```bash
npm run typecheck   # tsc -b, strict mode (see tsconfig.app.json)
npm run lint        # oxlint
npm run test         # vitest, jsdom + @testing-library/react
npm run test:e2e     # playwright, against a real running stack -- see e2e/README.md
npm run build        # tsc -b && vite build
```

Unit tests mock `fetch`/`api` and never touch a network — see `src/lib/api.test.ts` for the pattern. e2e tests deliberately don't mock anything (real backend, real Postgres/Redis/Neo4j) to catch the class of bug unit tests can't: wrong API paths, cookie handling, SSE actually working.

## Routes

| Route | Screen | Auth |
|---|---|---|
| `/` | Public landing page | none |
| `/docs` | Public developer docs (quickstart, events, webhooks, API reference) | none |
| `/login` | Email+password login | none |
| `/dashboard` | Executive summary: KPIs, top friction nodes, persona insights | required |
| `/predictive` | Persona library + new-simulation upload form | required |
| `/predictive/runs/:runId` | Live agentic log (SSE) + friction report once it finishes | required |
| `/predictive/personas/new`, `/predictive/personas/:personaId` | Persona configuration (sliders, triggers, memory bank) | required |
| `/journey` | Discovered funnel, friction nodes, cohort comparison, churn risk, node intelligence | required |
| `/calibration` | Predicted-vs-observed accuracy, retraining progress | required |
| `/getting-started` | Setup checklist + one-click sample-data import | required |
| `/settings/general` | Workspace name, retention, archive | required |
| `/settings/team` | Members, roles, invites | required |
| `/settings/billing` | Usage bars, upgrade CTA, Stripe portal | required |
| `/settings/integrations` | Slack/Jira connection, API keys, webhooks | required |
| `/settings/model-calibration` | Anomaly threshold, auto-retrain toggle, digest frequency | required |
| `/settings/security` | Audit log | required |

## Module map

| Path | Responsibility |
|---|---|
| `src/lib/api.ts` | Typed fetch client (`ApiError`, cookie auth, multipart uploads) |
| `src/lib/types.ts` | TS mirrors of the backend's Pydantic response schemas |
| `src/auth/` | `AuthContext`/`AuthProvider` (session state from `GET /auth/me`), `RequireAuth` route guard |
| `src/components/Shell.tsx`, `Sidebar.tsx` | The sidebar + content layout wrapping authenticated routes |
| `src/components/ImportSampleDataButton.tsx` | Shared "Import Sample Data" action (Journey Graph empty state + Getting Started) |
| `src/components/UsageLimitBanner.tsx` | Renders a 402's usage-limit message inline (Predictive Engine, Team Settings) |
| `src/routes/` | One file per screen (see Routes table above), grouped into `predictive/`, `journey/`, `calibration/`, `settings/` subfolders where a screen has sub-components |
| `src/index.css` | Alexandria design tokens as a Tailwind v4 `@theme` block |
