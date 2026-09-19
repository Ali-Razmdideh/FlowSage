# Design-integrity fixes

## Purpose

Make FlowSage's calibration, friction signals, and retention controls report
only what the available evidence supports. The changes correct misleading
product behavior without changing the simulation or event-ingestion contracts.

## Calibration

Calibration compares a persona's latest completed simulation with observed
funnel data. A missing funnel row is unknown evidence, not zero observed
friction. A funnel row with fewer than ten entered sessions is also insufficient
evidence. Such rows remain visible to explain the gap, but do not contribute an
anomaly, accuracy point, or retraining input.

The comparison set contains every screen the simulation walked, plus any screen
with a predicted issue. Screens without a predicted issue receive a predicted
score of zero, allowing observed friction to expose missed simulation signals.
Only comparisons backed by sufficient observed sessions affect persona accuracy.
If a persona has no supported comparisons, it is shown without an accuracy
point, and the UI reports that calibration is awaiting evidence rather than
claiming an optimized system.

The API adds evidence metadata and nullable observed score/delta fields to each
screen calibration. Existing consumers can distinguish a real zero from an
unknown measurement. Cached narratives and retraining continue to use only
actual anomalies.

## Rage-loop signals

A rage loop means a visitor repeats the same action on one screen at least three
times within ten seconds, before progressing. Events that merely share a screen
(such as a page view followed by focus and typing) do not form a rage loop.
Detection remains session-local and reports at most one affected session per
screen. The friction detail identifies the time-window rule.

## Retention

The daily retention job continues to delete old raw events and audit entries.
It also removes screenshot directories belonging to completed or failed,
expired simulation runs. Queued and running run directories and pending
scheduled uploads remain protected. Deletion is restricted to paths below the
configured upload directory.

The settings page calls the region field a workspace region instead of implying
that it provides regional-compliance guarantees. Its retention copy names the
data that the job removes: raw events, audit entries, and completed simulation
images.

## Error handling and safety

Missing, malformed, or inaccessible screenshot paths are skipped with a
sanitized log message so one bad record does not block retention for another
workspace. Calibration accepts incomplete event data and surfaces unknown
evidence rather than manufacturing a result.

## Verification

Regression tests will cover absent and low-sample calibration evidence,
unpredicted simulated screens, no-evidence UI state, repeated-action and
time-window rage-loop behavior, protected versus expired screenshot paths, and
updated settings messaging. Backend, graph, and frontend test/type/lint/build
checks will be run as applicable.
