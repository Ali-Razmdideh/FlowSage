from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flowsage_graph.ingest import (
    NullGraphSink,
    load_events,
    session_transitions,
)
from flowsage_graph.models import Event

_T0 = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)


def _event(session: str, screen: str, seconds: int, event: str = "screen_view") -> Event:
    return Event(
        session_id=session,
        screen=screen,
        event=event,
        timestamp=_T0 + timedelta(seconds=seconds),
        device="mobile",
        cohort="paid",
    )


def test_load_events_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"session_id": "s1", "screen": "cart", "event": "screen_view", '
        '"timestamp": "2026-07-17T12:00:00Z"}\n'
        '{"session_id": "s1", "screen": "checkout", "event": "screen_view", '
        '"timestamp": "2026-07-17T12:01:00Z"}\n',
        encoding="utf-8",
    )
    events = load_events(path)
    assert len(events) == 2
    assert events[0].screen == "cart"


def test_load_events_jsonl_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"session_id": "s1", "screen": "cart", "event": "e", '
        '"timestamp": "2026-07-17T12:00:00Z"}\n\n   \n',
        encoding="utf-8",
    )
    assert len(load_events(path)) == 1


def test_load_events_csv(tmp_path: Path) -> None:
    path = tmp_path / "events.csv"
    path.write_text(
        "session_id,screen,event,timestamp,device,cohort\n"
        "s1,cart,screen_view,2026-07-17T12:00:00Z,mobile,paid\n"
        "s1,checkout,screen_view,2026-07-17T12:01:00Z,mobile,paid\n",
        encoding="utf-8",
    )
    events = load_events(path)
    assert len(events) == 2
    assert events[1].device == "mobile"


def test_load_events_rejects_unknown_extension(tmp_path: Path) -> None:
    path = tmp_path / "events.txt"
    path.write_text("irrelevant", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported event log format"):
        load_events(path)


def test_session_transitions_collapses_same_screen_repeats() -> None:
    events = [
        _event("s1", "cart", 0),
        _event("s1", "cart", 1),
        _event("s1", "checkout", 2),
    ]
    transitions = session_transitions(events)
    assert len(transitions) == 1
    assert transitions[0][0].screen == "cart"
    assert transitions[0][1].screen == "checkout"


def test_session_transitions_are_isolated_per_session() -> None:
    events = [
        _event("s1", "cart", 0),
        _event("s2", "landing", 0),
        _event("s1", "checkout", 1),
        _event("s2", "cart", 1),
    ]
    transitions = session_transitions(events)
    assert len(transitions) == 2
    pairs = {(a.screen, b.screen) for a, b in transitions}
    assert pairs == {("cart", "checkout"), ("landing", "cart")}


def test_session_transitions_sorts_out_of_order_input() -> None:
    events = [
        _event("s1", "checkout", 5),
        _event("s1", "cart", 0),
    ]
    transitions = session_transitions(events)
    assert transitions == [(events[1], events[0])]


def test_null_graph_sink_does_nothing() -> None:
    NullGraphSink().ingest([_event("s1", "cart", 0)])
