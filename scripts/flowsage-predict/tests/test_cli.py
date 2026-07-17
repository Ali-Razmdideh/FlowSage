from pathlib import Path

import pytest

from flowsage_predict.cli import _discover_screenshots, _resolve_persona, build_parser, main
from flowsage_predict.models import ScreenEvaluation


class _FakeVisionClient:
    def evaluate_screen(self, persona, goal, screenshot, history):  # type: ignore[no-untyped-def]
        return ScreenEvaluation(action="proceeds", reasoning="looks fine")


def test_discover_screenshots_sorts_by_filename(tmp_path: Path) -> None:
    (tmp_path / "02_shipping.png").write_bytes(b"fake")
    (tmp_path / "01_cart.png").write_bytes(b"fake")
    (tmp_path / "notes.txt").write_text("ignore me")

    screenshots = _discover_screenshots(tmp_path)

    assert [p.name for p in screenshots] == ["01_cart.png", "02_shipping.png"]


def test_discover_screenshots_raises_on_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        _discover_screenshots(tmp_path / "missing")


def test_discover_screenshots_raises_when_empty(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="No screenshots"):
        _discover_screenshots(tmp_path)


def test_resolve_persona_baseline_id() -> None:
    persona = _resolve_persona("novice")
    assert persona.name == "Novice User"


def test_resolve_persona_custom_yaml_path(tmp_path: Path) -> None:
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "id: custom\nname: Custom\ndescription: d\n"
        "demographic_anchors: {tech_affinity: low, primary_device: mobile, "
        "discovery_mode: search-driven}\n"
        "sliders: {technical_literacy: 0.1, anxiety: 0.1, patience: 0.1, curiosity: 0.1}\n",
        encoding="utf-8",
    )
    persona = _resolve_persona(str(custom))
    assert persona.id == "custom"


def test_build_parser_requires_command() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_main_run_end_to_end_writes_report(tmp_path: Path) -> None:
    screenshots_dir = tmp_path / "screens"
    screenshots_dir.mkdir()
    (screenshots_dir / "01_cart.png").write_bytes(b"fake-png-bytes")
    (screenshots_dir / "02_confirm.png").write_bytes(b"fake-png-bytes")
    out_path = tmp_path / "friction_report.md"

    exit_code = main(
        [
            "run",
            "--screenshots",
            str(screenshots_dir),
            "--persona",
            "novice",
            "--goal",
            "Complete purchase",
            "--flow-name",
            "Checkout",
            "--out",
            str(out_path),
        ],
        vision_client=_FakeVisionClient(),
    )

    assert exit_code == 0
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert "Friction Report — Checkout" in content
    assert "completed the flow cleanly" in content


def test_main_run_reports_failure_for_missing_screenshots_dir(tmp_path: Path) -> None:
    exit_code = main(
        [
            "run",
            "--screenshots",
            str(tmp_path / "missing"),
            "--persona",
            "novice",
            "--goal",
            "goal",
            "--flow-name",
            "flow",
        ],
        vision_client=_FakeVisionClient(),
    )
    assert exit_code == 1


def test_list_personas_command_runs_without_error(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["list-personas"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "novice" in captured.out
