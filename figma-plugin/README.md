# FlowSage Figma Plugin

Select frames in Figma, run a real FlowSage persona simulation against them, and get
friction findings placed back on the canvas as annotation cards next to each frame.

## Setup (sideloaded, dev-mode plugin)

1. `npm install && npm run build` (produces `dist/code.js` + `dist/ui.html`).
2. In Figma desktop: **Plugins → Development → Import plugin from manifest…**, select
   this directory's `manifest.json`.
3. Open the plugin, go to Settings, enter your FlowSage instance's Base URL (e.g.
   `https://127.0.0.1` for a local deploy) and an API key from
   **Settings → Integrations** in the FlowSage web app. Click Save.

## Using it

1. Select one or more frames on the canvas — selection order becomes walkthrough order.
2. Pick a persona, enter a goal and a flow name.
3. Click **Run & Annotate**. The plugin exports the selected frames, uploads them to
   FlowSage as a new simulation run, polls until it finishes, then creates an annotation
   card (severity, title, heuristic, suggested fix) next to each frame with a finding.

## Manual QA checklist (run after any change — no automated tool in this repo's dev
environment can drive a real Figma session)

- [ ] Sideload the plugin fresh; Settings loads empty on first run, no crash.
- [ ] Save Settings with a real Base URL + API key; close and reopen the plugin;
      confirm the values persisted (this exercises `figma.clientStorage`, which
      nothing here can unit test).
- [ ] Select zero frames, click Run & Annotate: shows the "Select at least one frame…"
      error, no network call happens, nothing appears on canvas.
- [ ] Select 2+ frames (in a deliberate order), pick a persona, run a real simulation
      against a live FlowSage backend: confirm a simulation run appears in the FlowSage
      web app's Predictive Engine with `screen` values `001`/`002`/… in walkthrough
      order matching your selection order.
- [ ] After completion, confirm one annotation card per finding appears on the canvas,
      positioned to the right of the correct source frame (not a different one),
      stacked vertically when a frame has 2+ findings, with the right severity color,
      title, heuristic, and suggested-fix text.
- [ ] Force a failed run (e.g. an invalid API key mid-run) and confirm the UI shows the
      run's error message instead of silently hanging or annotating nothing with no
      explanation.
