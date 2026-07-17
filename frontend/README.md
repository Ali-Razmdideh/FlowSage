# flowsage-frontend

React + TypeScript (strict) + Vite frontend for FlowSage. Talks to `backend/`
exclusively through cookie-based session auth (`credentials: "include"`), matching
its single-tenant Phase 1 auth model — see `src/lib/api.ts`.

Visual language is the Alexandria design system extracted from
`design-hifi-prototypes/` (Noto Serif headlines, Inter body, Public Sans labels,
primary `#094cb2`, tonal surfaces instead of 1px borders) — see `src/index.css`.

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

Needs the backend stack running — see `../backend/README.md` and
`../infra/docker-compose.yml`. Quickest path:

```bash
docker compose -f ../infra/docker-compose.yml up -d postgres redis neo4j backend worker
docker compose -f ../infra/docker-compose.yml exec backend \
  python -m alembic -c /workspace/backend/alembic.ini upgrade head
docker compose -f ../infra/docker-compose.yml exec backend flowsage-backend seed-personas
docker compose -f ../infra/docker-compose.yml exec backend \
  flowsage-backend create-user admin@example.com supersecret123
```

## Development

```bash
npm run typecheck   # tsc -b, strict mode (see tsconfig.app.json)
npm run lint        # oxlint
npm run test         # vitest, jsdom + @testing-library/react
npm run test:e2e     # playwright, against a real running stack -- see e2e/README.md
npm run build        # tsc -b && vite build
```

Unit tests mock `fetch`/`api` and never touch a network — see `src/lib/api.test.ts`
for the pattern. e2e tests deliberately don't mock anything (real backend, real
Postgres/Redis/Neo4j) to catch the class of bug unit tests can't: wrong API paths,
cookie handling, the SSE stream actually working.

## Routes

| Route | Screen |
|---|---|
| `/login` | Email+password login (sets the session cookie) |
| `/dashboard` | Executive summary: KPIs, top friction nodes, persona list |
| `/predictive` | Persona library + new-simulation upload form |
| `/predictive/runs/:runId` | Live agentic log (SSE) + friction report once it finishes |
| `/journey` | Discovered funnel + friction nodes (empty state if no events yet) |

`/predictive` and `/journey` correspond to the plan's Predictive Engine and Journey
Graph route groups; Calibration and Settings aren't built yet (Phase 2/3 backend
work), so there's intentionally no dead nav for them yet.

## Module map

| Path | Responsibility |
|---|---|
| `src/lib/api.ts` | Typed fetch client (`ApiError`, cookie auth, multipart uploads) |
| `src/lib/types.ts` | TS mirrors of the backend's Pydantic response schemas |
| `src/auth/` | `AuthContext`/`AuthProvider` (session state from `GET /auth/me`), `RequireAuth` route guard |
| `src/components/Shell.tsx`, `Sidebar.tsx` | The sidebar + content layout wrapping authenticated routes |
| `src/routes/` | One file per screen (see Routes table above) |
| `src/index.css` | Alexandria design tokens as a Tailwind v4 `@theme` block |
