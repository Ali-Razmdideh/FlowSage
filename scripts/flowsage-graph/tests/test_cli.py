from pathlib import Path

import pytest

from flowsage_graph.cli import build_parser, main
from flowsage_graph.ingest import GraphSink
from flowsage_graph.models import Event


class _RecordingSink:
    def __init__(self) -> None:
        self.ingested: list[Event] = []

    def ingest(self, events: list[Event], workspace_id: str) -> None:
        self.ingested.extend(events)


class _FailingSink:
    def ingest(self, events: list[Event], workspace_id: str) -> None:
        raise RuntimeError("connection refused")


def test_build_parser_requires_command() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_main_run_end_to_end_writes_html(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        '{"session_id": "s1", "screen": "landing", "event": "screen_view", '
        '"timestamp": "2026-07-17T12:00:00Z"}\n'
        '{"session_id": "s1", "screen": "cart", "event": "screen_view", '
        '"timestamp": "2026-07-17T12:01:00Z"}\n',
        encoding="utf-8",
    )
    out_path = tmp_path / "funnel.html"
    sink = _RecordingSink()

    exit_code = main(
        ["run", "--events", str(events_path), "--out", str(out_path)],
        sink=sink,
    )

    assert exit_code == 0
    assert out_path.exists()
    assert "FlowSage Funnel Report" in out_path.read_text(encoding="utf-8")
    assert len(sink.ingested) == 2


def test_main_run_continues_when_neo4j_sink_raises(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        '{"session_id": "s1", "screen": "landing", "event": "screen_view", '
        '"timestamp": "2026-07-17T12:00:00Z"}\n',
        encoding="utf-8",
    )
    out_path = tmp_path / "funnel.html"

    exit_code = main(
        ["run", "--events", str(events_path), "--out", str(out_path)],
        sink=_FailingSink(),
    )

    assert exit_code == 0
    assert out_path.exists()


def test_main_run_reports_failure_for_bad_event_log(tmp_path: Path) -> None:
    events_path = tmp_path / "events.txt"
    events_path.write_text("irrelevant", encoding="utf-8")

    exit_code = main(
        ["run", "--events", str(events_path), "--out", str(tmp_path / "out.html")],
        sink=_RecordingSink(),
    )
    assert exit_code == 1


def test_build_neo4j_sink_type_matches_protocol() -> None:
    # Sanity check that our test doubles satisfy the GraphSink protocol shape.
    sink: GraphSink = _RecordingSink()
    sink.ingest([], "ws-test")
