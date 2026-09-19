# Flow identity and analytics lifecycle design

## Purpose

Make analytics and calibration reliably attributable to a specific product journey, while keeping existing workspaces and clients operational. Complete retention across the graph projection and give workspace administrators usable evidence, export, and deletion controls.

## Flow identity

Introduce a workspace-scoped immutable flow identity with a monotonically increasing version. Events and simulation runs reference the flow identity and version. Existing rows remain valid with null flow identity and appear only in an explicit legacy/unassigned view.

Event ingestion accepts an optional flow key and version. If supplied, the key resolves to a workspace-owned flow. New simulations target a flow rather than only a display name. The legacy flow-name fields remain during migration and responses include both forms until clients migrate.

## Scoped analytics

Funnel, friction, calibration, alerts, and graph projections accept optional flow identity/version filters. The default workspace dashboard remains an all-flows aggregation; calibration requires a specific flow selection when a workspace has more than one active flow, preventing accidental cross-flow comparison.

Graph nodes and transitions include flow identity/version in their merge keys. This prevents same-named screens from different flows being merged.

## Retention and privacy

The retention worker deletes graph nodes and relationships belonging to expired event windows using the same workspace and cutoff as Postgres retention. It records a bounded audit event for manual export and deletion requests. Workspace export generates a downloadable archive of workspace-owned relational data. Workspace deletion requires an explicit confirmation token and asynchronously removes relational records, graph projections, and upload directories.

## Product evidence

The journey view exposes flow/version filters and a data-quality card: event count, distinct sessions, covered simulation screens, and unmatched screens. Friction findings link to a filtered session-evidence list so a researcher can inspect the underlying events.

## Compatibility and verification

All database columns are nullable at introduction. Existing API payloads remain accepted. Tests cover workspace boundaries, flow/version isolation, legacy events, graph deletion, retention failure isolation, exports, deletion confirmation, and frontend filters/evidence states.
