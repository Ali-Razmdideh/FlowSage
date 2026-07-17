"""Command-line entry point: `flowsage-predict`.

Walks a directory of screenshots (in filename order) with a configurable LLM
persona and writes a Markdown friction report.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from flowsage_predict.agent import run_persona_walkthrough
from flowsage_predict.models import Persona, SimulationReport
from flowsage_predict.personas import find_baseline_persona, load_baseline_personas, load_persona
from flowsage_predict.report import render_markdown
from flowsage_predict.vision import AnthropicVisionClient, VisionClient

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _discover_screenshots(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise NotADirectoryError(f"{directory} is not a directory")
    screenshots = sorted(p for p in directory.iterdir() if p.suffix.lower() in _IMAGE_SUFFIXES)
    if not screenshots:
        raise FileNotFoundError(
            f"No screenshots (png/jpg/jpeg/webp) found in {directory}. "
            "Name files so alphabetical order matches screen order, e.g. 01_cart.png."
        )
    return screenshots


def _resolve_persona(persona_arg: str) -> Persona:
    persona_path = Path(persona_arg)
    if persona_path.suffix in {".yaml", ".yml"} and persona_path.exists():
        return load_persona(persona_path)
    return find_baseline_persona(persona_arg)


def _cmd_run(args: argparse.Namespace, vision_client: VisionClient) -> SimulationReport:
    persona = _resolve_persona(args.persona)
    screenshots = _discover_screenshots(Path(args.screenshots))

    final_state = run_persona_walkthrough(
        persona=persona,
        goal=args.goal,
        screenshots=screenshots,
        vision_client=vision_client,
    )

    # "completed" means the persona reached the last screen instead of abandoning early.
    report = SimulationReport(
        persona=persona,
        goal=args.goal,
        flow_name=args.flow_name,
        steps=final_state["steps"],
        issues=final_state["issues"],
        completed=final_state["index"] >= len(screenshots),
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(report), encoding="utf-8")
    return report


def _cmd_list_personas() -> None:
    for persona in load_baseline_personas():
        print(f"{persona.id}\t{persona.name}\t{persona.description.strip().splitlines()[0]}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flowsage-predict",
        description="LLM persona walkthrough of a screenshot sequence -> friction report.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a persona walkthrough")
    run_parser.add_argument(
        "--screenshots", required=True, help="Directory of screenshots, in flow order"
    )
    run_parser.add_argument(
        "--persona",
        required=True,
        help="Baseline persona id (see `list-personas`) or path to a custom persona YAML file",
    )
    run_parser.add_argument("--goal", required=True, help="What the persona is trying to do")
    run_parser.add_argument(
        "--flow-name", required=True, help="Human-readable name of the flow being tested"
    )
    run_parser.add_argument(
        "--out", default="friction_report.md", help="Output path for the Markdown report"
    )

    subparsers.add_parser("list-personas", help="List the bundled baseline personas")

    return parser


def main(argv: list[str] | None = None, vision_client: VisionClient | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "list-personas":
        _cmd_list_personas()
        return 0

    if args.command == "run":
        try:
            report = _cmd_run(args, vision_client=vision_client or AnthropicVisionClient())
        except Exception as exc:  # noqa: BLE001 - top-level CLI error boundary
            print(f"flowsage-predict: {exc}", file=sys.stderr)
            return 1
        print(
            f"Wrote {args.out} — {len(report.issues)} issue(s), "
            f"{'completed' if report.completed else 'abandoned'}"
        )
        return 0

    parser.error(f"Unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
