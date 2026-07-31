# Sample data

Demo fixtures for `flowsage-graph`/`flowsage-predict`, and the source data bundled
into the backend (`backend/src/flowsage_backend/resources/`) for the "Import Sample
Data" action on the Journey Graph empty state and the Getting Started checklist.

## `events.jsonl`

44 synthetic events across 11 sessions of an e-commerce checkout flow
(`Landing_Main` → `Product_View_PDP` → `Cart_Summary` → `Checkout_Final_Payment`).
Deliberately includes all three friction patterns `flowsage-graph` detects:

- Drop-off at every step (some sessions never reach checkout)
- A rage-click burst on `Checkout_Final_Payment`
- A backtrack from `Cart_Summary` back to `Product_View_PDP`

```bash
cd scripts/flowsage-graph
uv run flowsage-graph run --events ../sample_data/events.jsonl --out /tmp/funnel_report.html --skip-neo4j
```

## `screenshots/`

A 3-screen mock checkout flow (`01_cart.png`, `02_shipping.png`, `03_confirm.png`)
with a deliberate usability bug on the shipping screen (a zip code field flagged
as invalid with no explanation) for `flowsage-predict` to catch:

```bash
cd scripts/flowsage-predict
uv run flowsage-predict run \
  --screenshots ../sample_data/screenshots \
  --persona novice \
  --goal "Complete purchase" \
  --flow-name "Checkout Flow" \
  --out /tmp/friction_report.md
```

Requires `ANTHROPIC_API_KEY` to be set, since this one does call Claude.
