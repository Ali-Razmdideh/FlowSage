"""Command-line entry point: `flowsage-graph`.

Parses an event log, ingests it into Neo4j as a journey graph, auto-discovers the
funnel, detects friction nodes, and writes a static HTML report.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from flowsage_graph.funnel import detect_friction, discover_funnel
from flowsage_graph.ingest import GraphSink, Neo4jGraphSink, NullGraphSink, load_events
from flowsage_graph.models import FunnelReport
from flowsage_graph.viz import render_html


def _build_neo4j_sink(args: argparse.Namespace) -> GraphSink:
    if args.skip_neo4j:
        return NullGraphSink()

    uri = args.neo4j_uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = args.neo4j_user or os.environ.get("NEO4J_USER", "neo4j")
    password = args.neo4j_password or os.environ.get("NEO4J_PASSWORD", "flowsage_dev")
    return Neo4jGraphSink(uri, user, password)


def _cmd_run(args: argparse.Namespace, sink: GraphSink) -> FunnelReport:
    events = load_events(Path(args.events))

    try:
        sink.ingest(events)
    except Exception as exc:  # noqa: BLE001 - Neo4j being unreachable shouldn't block the report
        print(f"flowsage-graph: warning: Neo4j ingestion skipped ({exc})", file=sys.stderr)

    funnel = discover_funnel(events)
    friction = detect_friction(events, funnel)
    report = FunnelReport(
        funnel=funnel,
        friction_nodes=friction,
        total_sessions=len({e.session_id for e in events}),
        total_events=len(events),
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(report), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flowsage-graph",
        description="Event log -> Neo4j journey graph -> funnel discovery -> HTML report.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Ingest an event log and build a funnel report")
    run_parser.add_argument("--events", required=True, help="Path to a .jsonl or .csv event log")
    run_parser.add_argument("--out", default="funnel_report.html", help="Output HTML path")
    run_parser.add_argument("--neo4j-uri", default=None, help="Defaults to $NEO4J_URI")
    run_parser.add_argument("--neo4j-user", default=None, help="Defaults to $NEO4J_USER")
    run_parser.add_argument("--neo4j-password", default=None, help="Defaults to $NEO4J_PASSWORD")
    run_parser.add_argument(
        "--skip-neo4j", action="store_true", help="Only build the HTML report, skip ingestion"
    )

    return parser


def main(argv: list[str] | None = None, sink: GraphSink | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        try:
            report = _cmd_run(args, sink=sink or _build_neo4j_sink(args))
        except Exception as exc:  # noqa: BLE001 - top-level CLI error boundary
            print(f"flowsage-graph: {exc}", file=sys.stderr)
            return 1
        print(
            f"Wrote {args.out} — {len(report.funnel)} funnel step(s), "
            f"{len(report.friction_nodes)} friction node(s)"
        )
        return 0

    parser.error(f"Unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
