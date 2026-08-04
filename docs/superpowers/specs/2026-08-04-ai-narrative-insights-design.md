# AI Narrative Insights — Design Spec

**Date:** 2026-08-04
**Status:** Approved for planning

**Note (implementation):** Node Intelligence and calibration-anomaly narrative generation ended up running via background arq jobs triggered from the GET endpoints (cache-miss enqueues, next GET picks up the result), not inline via `asyncio.to_thread` as originally drafted above -- this codebase never makes a live Claude call from a synchronous FastAPI handler. Retraining rationale is unchanged from the original design (already an async job). See `docs/superpowers/plans/2026-08-04-ai-narrative-insights.md` for the as-built architecture.

## Problem

Three surfaces in FlowSage present text as if it were AI-generated analysis, but it's actually deterministic template interpolation:

1. **Node Intelligence** (`churn.py::build_node_intelligence`) — `ai_insight` string and `recommendations` list are picked from fixed `_INSIGHT_TEMPLATES`/`_RECOMMENDATIONS` dicts keyed by `FrictionKind`.
2. **Calibration anomaly report** (`calibration.py::build_calibration_report`) — shows a predicted-vs-observed numbers table with no narrative explanation of *why* they diverged at all today (no text field exists yet).
3. **Retraining rationale** (`retraining.py::execute_retraining`) — the `PersonaMemory.note` written after a retrain is an f-string built from the anomaly list, not a real explanation.

This was a deliberate cost/latency tradeoff when each was built (see docstrings in `calibration.py`/`churn.py`/`retraining.py`). This spec replaces the deterministic text with real Claude-generated narrative, while keeping the existing templates as a fallback so these endpoints never hard-fail on an LLM error or an exhausted usage budget.

## Scope

In scope: the three surfaces above. Out of scope: churn-risk scoring, cohort comparison, calibration numbers/matching logic, retraining's slider-nudge math — all of that stays exactly as-is; only the *text* generation changes.

## Architecture

### `flowsage_predict.narrative` (new module)

Sibling to `vision.py`, same conventions: a `Protocol` (`NarrativeClient`) with one method per surface, a production `AnthropicNarrativeClient` implementation using forced tool-call JSON output (typed `ToolParam`/`MessageParam` from `anthropic.types`, matching `vision.py`'s `mypy --strict` pattern), and tests inject a fake implementing the same `Protocol` — no live Claude calls anywhere in the test suite, consistent with `slack.py`/`jira.py`'s `httpx.MockTransport` convention.

```python
class NarrativeClient(Protocol):
    def generate_node_insight(
        self, screen: str, drop_off_rate: float, friction_nodes: list[FrictionNode]
    ) -> NodeInsightResult: ...

    def generate_calibration_narrative(
        self, persona_name: str, anomalous_screens: list[ScreenCalibration]
    ) -> str: ...

    def generate_retraining_rationale(
        self, persona_name: str, anomalies: list[ScreenCalibration],
        new_sliders: tuple[float, float, float],
    ) -> str: ...
```

`NodeInsightResult` = `{insight: str, recommendations: list[{title, description, expected_lift_pct: float | None}]}`, forced via tool schema so it parses the same shape `churn.py` already returns to the frontend — no frontend changes needed for this surface.

Model: a fixed cheap default (`claude-haiku-4-5-20251001`), not persona-configurable — these calls aren't persona simulation, they're report narration, so there's no reason to route them through `Persona.model`.

### Caching: `GeneratedInsight` table (new)

```python
class GeneratedInsight(Base):
    __tablename__ = "generated_insights"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # "node_intelligence" | "calibration_anomaly" | "retraining_rationale"
    cache_key: Mapped[str] = mapped_column(String(200))
    input_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("workspace_id", "kind", "cache_key"),)
```

`kind` is a plain `String`, not a Postgres `Enum` — deliberately, to sidestep the enum-drop-on-downgrade migration gotcha already documented in this project's history (`run_status`/`retraining_status` both needed an explicit downgrade fix; a plain string column has no such issue).

- `cache_key`: `screen` for node intelligence, `persona_id` for calibration anomaly, `str(job_id)` for retraining rationale (always unique — a retrain job never repeats, so this path is always a cache-miss by construction, which is correct: each retrain deserves its own explanation).
- `input_hash`: `sha256` of the JSON-serialized signal that would change the narrative (drop-off rate + friction kinds/counts for node intelligence; the anomalous `(screen, predicted, observed, delta)` tuples for calibration/retraining). A GET computes the hash first and compares against the cached row before ever considering a Claude call.
- A `GeneratedInsight` row is only written **after** a successful Claude call — cache hits and template-fallback paths never write one. This makes "rows created this month" exactly equal to "Claude narrative calls made this month," which is also the usage-cap counter (see below). No separate accounting needed.

### Usage cap

New `TierLimits` field `insight_generations_per_month`, e.g. `FREE=20, PRO=1_000, TEAM=-1` (unlimited) — a separate, more generous budget than `runs_per_month`, since these are cheap text-only calls, not vision simulation runs.

Unlike `check_within_limits` (which raises 402), this is a **soft gate**: `billing.has_narrative_budget(session, workspace_id) -> bool`, computed the same way as `get_usage` (count `GeneratedInsight` rows this month, compare to the tier limit). Called before attempting a Claude call at each of the three sites; if `False`, skip straight to the deterministic fallback — no exception, no user-visible error. A report page must never break because a text budget ran out.

### Error handling (all three sites, same pattern)

```python
if cache_hit_matches_input_hash:
    use cached payload
elif await billing.has_narrative_budget(session, workspace_id):
    try:
        result = await asyncio.to_thread(narrative_client.generate_...)
        upsert GeneratedInsight row
        use result
    except Exception:
        use deterministic fallback  # never raises out of the endpoint/job
else:
    use deterministic fallback
```

This mirrors the existing best-effort philosophy already used for the Neo4j mirror (`Event` ingestion doesn't fail the CLI if Neo4j is down) and audit logging (`record_audit_event` never raises) — a narrative-generation failure is never allowed to break the feature it's decorating.

### Per-surface data flow

**Node Intelligence** (`churn.py::build_node_intelligence` → becomes `async`, called from `get_node_intelligence`): compute `drop_off_rate`/`friction_nodes` exactly as today (unchanged, deterministic), then run the cache/budget/fallback flow above to fill `ai_insight`/`recommendations`. Fallback = today's `_INSIGHT_TEMPLATES`/`_RECOMMENDATIONS` logic, kept verbatim as the `except`/no-budget path.

**Calibration anomaly** (`calibration.py::build_calibration_report`): add `narrative: str | None` to `PersonaCalibration`. Only attempted when `any(s.anomaly for s in screens)` for that persona — one Claude call per anomalous persona per report, not per screen. Fallback = `None` (frontend's existing anomaly banner + table render fine without it today; no new template text needed since there was never one before).

**Retraining rationale** (`retraining.py::execute_retraining`): keep the existing deterministic `note` f-string computed exactly as today, unconditionally, as the fallback value. When `anomalies` is non-empty, attempt `generate_retraining_rationale`; on success, that replaces `note` before the `PersonaMemory` row is built. The narrative attempt is wrapped in its own nested `try/except` distinct from the job's outer one — a narrative failure must not flip the whole `RetrainingJob` to `FAILED`.

## Testing

- `flowsage_predict/tests/test_narrative.py`: forced tool-call schema round-trips correctly for each of the 3 result shapes, using a fake `httpx` transport (mirrors `vision.py`'s existing test pattern).
- `backend/tests/test_churn_api.py` / `test_calibration_api.py` / `test_retraining.py`: inject a fake `NarrativeClient` via dependency override (same DI pattern the app already uses for other test doubles). Cases per surface: cache miss → generates + persists a `GeneratedInsight` row; cache hit with matching `input_hash` → no Claude call, reuses cached payload; cache hit with stale `input_hash` (underlying data changed) → regenerates; budget exhausted → falls back to deterministic text, no `GeneratedInsight` row written; Claude call raises → falls back to deterministic text, no row written, endpoint/job still succeeds.
- `backend/tests/test_billing.py`: `has_narrative_budget` true/false at the tier boundary, unlimited (`-1`) tier never gates.

## Migration

One new Alembic migration for `generated_insights` (no native `Enum` column, so no downgrade special-casing needed — first table in this project able to skip that particular gotcha). Full upgrade→downgrade→upgrade cycle verified against a live Postgres, per this project's standing practice.

## Not doing

- No frontend changes — `NodeIntelligence`'s response shape is unchanged (`ai_insight`/`recommendations` were already there), `PersonaCalibration` only gains one new optional field the existing UI can render or ignore, and retraining's `PersonaMemory.note` was always freeform text.
- No per-screen calibration narrative (persona-level only) — keeps Claude call count down.
- No admin/settings UI to configure `insight_generations_per_month` — it's a `TIER_LIMITS` constant like every other cap in `billing.py` today.
