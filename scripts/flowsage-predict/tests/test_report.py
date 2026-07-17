from datetime import datetime, timezone

from flowsage_predict.models import (
    BehavioralSliders,
    DemographicAnchors,
    FrictionIssue,
    Persona,
    Severity,
    SimulationReport,
    SimulationStep,
)
from flowsage_predict.report import render_markdown


def _persona() -> Persona:
    return Persona(
        id="novice",
        name="Novice User",
        description="Test persona",
        demographic_anchors=DemographicAnchors(
            tech_affinity="low", primary_device="mobile", discovery_mode="search-driven"
        ),
        sliders=BehavioralSliders(technical_literacy=0.2, anxiety=0.8, patience=0.3, curiosity=0.4),
    )


def test_render_markdown_with_no_issues() -> None:
    report = SimulationReport(
        persona=_persona(),
        goal="Complete purchase",
        flow_name="Checkout",
        steps=[SimulationStep(screen="cart", action="Adds item", reasoning="Wants it")],
        issues=[],
        completed=True,
        generated_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )
    markdown = render_markdown(report)
    assert "# Friction Report — Checkout" in markdown
    assert "Novice User" in markdown
    assert "completed the flow cleanly" in markdown
    assert "1. **cart** — Adds item" in markdown


def test_render_markdown_sorts_issues_by_severity_and_counts_them() -> None:
    low = FrictionIssue(
        screen="a",
        severity=Severity.LOW,
        title="Minor",
        heuristic_violated="h",
        persona_impact="p",
        description="d",
        suggested_fix="f",
    )
    critical = FrictionIssue(
        screen="b",
        severity=Severity.CRITICAL,
        title="Blocker",
        heuristic_violated="h",
        persona_impact="p",
        description="d",
        suggested_fix="f",
    )
    report = SimulationReport(
        persona=_persona(),
        goal="Complete purchase",
        flow_name="Checkout",
        steps=[
            SimulationStep(screen="a", action="act1", reasoning="r1", friction=low),
            SimulationStep(screen="b", action="act2", reasoning="r2", friction=critical),
        ],
        issues=[low, critical],
        completed=False,
    )
    markdown = render_markdown(report)
    assert markdown.index("Blocker") < markdown.index("Minor")
    assert "2 issue(s) detected" in markdown
    assert "🔴 Critical: 1" in markdown
    assert "🟢 Low: 1" in markdown
    assert "no, abandoned" in markdown
    assert " ⚠️" in markdown
