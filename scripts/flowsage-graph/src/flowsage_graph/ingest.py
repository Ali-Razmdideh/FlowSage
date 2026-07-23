"""Parse the event log and upsert it into Neo4j as a temporal journey graph.

Schema: `(:Screen {name})-[:TRANSITION {session_id, count, device, cohort,
first_seen, last_seen}]->(:Screen)`, one edge per (from_screen, to_screen,
session_id) triple. `count` tracks how many times that session made that exact
transition, which is what `funnel.py` uses to spot rage-loops later.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Protocol

from neo4j import Driver, GraphDatabase, ManagedTransaction

from flowsage_graph.models import Event

_MERGE_TRANSITION_QUERY = """
MERGE (a:Screen {name: $from_screen, workspace_id: $workspace_id})
MERGE (b:Screen {name: $to_screen, workspace_id: $workspace_id})
MERGE (a)-[t:TRANSITION {session_id: $session_id}]->(b)
ON CREATE SET
    t.count = 1,
    t.device = $device,
    t.cohort = $cohort,
    t.first_seen = $timestamp,
    t.last_seen = $timestamp,
    t.workspace_id = $workspace_id
ON MATCH SET
    t.count = t.count + 1,
    t.last_seen = $timestamp
"""


def load_events(path: Path) -> list[Event]:
    """Load an event log, dispatching on file extension (`.jsonl` or `.csv`)."""
    if path.suffix == ".jsonl":
        return _load_events_jsonl(path)
    if path.suffix == ".csv":
        return _load_events_csv(path)
    raise ValueError(f"Unsupported event log format {path.suffix!r}; expected .jsonl or .csv")


def _load_events_jsonl(path: Path) -> list[Event]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(Event.model_validate(json.loads(line)))
    return events


def _load_events_csv(path: Path) -> list[Event]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [Event.model_validate(row) for row in reader]


def session_transitions(events: list[Event]) -> list[tuple[Event, Event]]:
    """Pair up consecutive same-session events into (from, to) transitions.

    Events are grouped by `session_id` and sorted by timestamp; consecutive
    events on the *same* screen are collapsed (they represent dwelling, not
    moving), matching only genuine screen-to-screen transitions.
    """
    by_session: dict[str, list[Event]] = {}
    for event in events:
        by_session.setdefault(event.session_id, []).append(event)

    transitions: list[tuple[Event, Event]] = []
    for session_events in by_session.values():
        ordered = sorted(session_events, key=lambda e: e.timestamp)
        for prev, curr in zip(ordered, ordered[1:]):
            if prev.screen != curr.screen:
                transitions.append((prev, curr))
    return transitions


class GraphSink(Protocol):
    def ingest(self, events: list[Event], workspace_id: str) -> None: ...


class NullGraphSink:
    """No-op sink used when Neo4j ingestion is skipped or unreachable."""

    def ingest(self, events: list[Event], workspace_id: str) -> None:
        return None


def _merge_transition_tx(
    tx: ManagedTransaction, from_event: Event, to_event: Event, workspace_id: str
) -> None:
    tx.run(
        _MERGE_TRANSITION_QUERY,
        from_screen=from_event.screen,
        to_screen=to_event.screen,
        session_id=to_event.session_id,
        device=to_event.device,
        cohort=to_event.cohort,
        timestamp=to_event.timestamp.isoformat(),
        workspace_id=workspace_id,
    )


class Neo4jGraphSink:
    """Upserts session transitions into Neo4j as `Screen`/`TRANSITION` graph data."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "Neo4jGraphSink":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def ingest(self, events: list[Event], workspace_id: str) -> None:
        transitions = session_transitions(events)
        with self._driver.session() as session:
            for from_event, to_event in transitions:
                session.execute_write(_merge_transition_tx, from_event, to_event, workspace_id)
