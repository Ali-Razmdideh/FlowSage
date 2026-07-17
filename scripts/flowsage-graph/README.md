# flowsage-graph

Event log → Neo4j journey graph → automatic funnel discovery → static HTML report.
The Phase 0 "observational engine" script described in the [project plan](../../plans/full-project-coding-plan.md).

Reads a raw event log (no manually defined funnel), ingests each session's
screen-to-screen transitions into Neo4j as `(:Screen)-[:TRANSITION]->(:Screen)`
edges, then — purely from the in-memory event list, so this works even without
Neo4j running — discovers the most-traveled path through the product and flags
three friction patterns along it:

- **Abnormal drop-off** — a funnel step where too many sessions never continue.
- **Rage loops** — a session repeating actions on the same screen without progressing.
- **Backtracking** — a session revisiting an earlier funnel screen after moving past it.

## Setup

```bash
cd scripts/flowsage-graph
uv sync

# optional: a local Neo4j to ingest into (see ../../infra/docker-compose.yml)
docker compose -f ../../infra/docker-compose.yml up -d neo4j
```

## Usage

```bash
uv run flowsage-graph run --events ../sample_data/events.jsonl --out funnel_report.html
```

Event log format (`.jsonl` or `.csv`), one event per row:

```json
{"session_id": "s1", "screen": "cart", "event": "screen_view", "timestamp": "2026-07-17T12:00:00Z", "device": "mobile", "cohort": "paid"}
```

`--skip-neo4j` builds the HTML report without touching Neo4j at all. Otherwise
connection details come from `--neo4j-uri`/`--neo4j-user`/`--neo4j-password` or the
`NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD` env vars (defaults match
`infra/docker-compose.yml`). If Neo4j is unreachable, ingestion is skipped with a
warning — it never blocks the HTML report, which is the point of this script.

## Development

```bash
uv sync --all-extras
uv run autoflake8 --recursive --in-place src tests   # remove unused imports/vars
uv run black src tests                               # format
uv run mypy --strict src                             # strict typing
uv run pytest                                         # unit tests (no live Neo4j needed)
```

Funnel discovery and friction detection are pure functions over the parsed event
list, so they're fully unit-tested without any database. Only `Neo4jGraphSink`
talks to Neo4j; tests inject a `NullGraphSink`/fake `GraphSink` instead.

## Module map

| Module | Responsibility |
|---|---|
| `models.py` | `Event`, `FunnelStep`, `FrictionNode`, `FunnelReport` |
| `ingest.py` | Parse `.jsonl`/`.csv` logs, group into session transitions, `Neo4jGraphSink` upsert |
| `funnel.py` | `discover_funnel` (heaviest-path search) + `detect_friction` (drop-off/rage-loop/backtrack) |
| `viz.py` | Renders a `FunnelReport` as a self-contained HTML page |
| `cli.py` | `flowsage-graph` command-line entry point |
