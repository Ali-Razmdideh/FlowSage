# flowsage-predict

LLM persona agent that walks a screenshot sequence and produces a Markdown friction
report — the Phase 0 "predictive engine" script described in the [project plan](../../plans/full-project-coding-plan.md).

A [LangGraph](https://langchain-ai.github.io/langgraph/) agent loads a persona
(demographic anchors + behavioral sliders + contextual triggers), then walks your
screenshots one at a time. On each screen it calls Claude with vision input and asks:
what would this persona do here, and does anything trip them up? The result is a
severity-ranked friction report with heuristic references and suggested fixes.

## Setup

```bash
cd scripts/flowsage-predict
uv sync
export ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

```bash
# See the 5 bundled baseline personas
uv run flowsage-predict list-personas

# Walk a screenshot sequence with one of them
uv run flowsage-predict run \
  --screenshots ./my-checkout-screens \
  --persona novice \
  --goal "Complete purchase" \
  --flow-name "Checkout Flow" \
  --out friction_report.md
```

Screenshots are visited in alphabetical filename order, so name them so that order
matches the flow (`01_cart.png`, `02_shipping.png`, `03_confirm.png`, ...). Supported
formats: `.png`, `.jpg`, `.jpeg`, `.webp`.

`--persona` accepts either a baseline persona id (`novice`, `power_user`,
`accessibility_constrained`, `low_patience_mobile`, `non_native_speaker`) or a path to
a custom persona YAML file — see `src/flowsage_predict/baseline_personas/*.yaml` for
the schema.

## Development

```bash
uv sync --all-extras
uv run autoflake8 --recursive --in-place src tests   # remove unused imports/vars
uv run black src tests                               # format
uv run mypy --strict src                             # strict typing
uv run pytest                                         # unit tests (no network calls)
```

`AnthropicVisionClient` is the only part of this package that talks to the network;
everything else (persona loading, the LangGraph walkthrough, Markdown rendering) is
tested against a fake `VisionClient` so `pytest` never needs an API key.

## Module map

| Module | Responsibility |
|---|---|
| `models.py` | Pydantic models shared across the pipeline (`Persona`, `FrictionIssue`, `SimulationReport`, ...) |
| `personas.py` | Load/validate persona YAML files, bundled or custom |
| `vision.py` | `VisionClient` protocol + `AnthropicVisionClient` (Claude vision + forced tool call) |
| `agent.py` | LangGraph state machine that loops the vision client over the screenshot sequence |
| `report.py` | Renders a `SimulationReport` as the `friction_report.md` Markdown |
| `cli.py` | `flowsage-predict` command-line entry point |
