"""Render a `SimulationReport` as a Markdown friction report."""

from __future__ import annotations

from flowsage_predict.models import Severity, SimulationReport

_SEVERITY_ORDER = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
}

_SEVERITY_LABEL = {
    Severity.CRITICAL: "🔴 Critical",
    Severity.HIGH: "🟠 High",
    Severity.MEDIUM: "🟡 Medium",
    Severity.LOW: "🟢 Low",
}


def render_markdown(report: SimulationReport) -> str:
    """Produce the `friction_report.md` contents for one simulation run."""
    lines: list[str] = []
    lines.append(f"# Friction Report — {report.flow_name}")
    lines.append("")
    lines.append(f"**Persona:** {report.persona.name}")
    lines.append(f"**Goal:** {report.goal}")
    lines.append(f"**Screens visited:** {report.screenshots_visited}")
    lines.append(f"**Completed goal:** {'yes' if report.completed else 'no, abandoned'}")
    lines.append(f"**Generated:** {report.generated_at.isoformat()}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    if not report.issues:
        lines.append("No friction detected — the persona completed the flow cleanly.")
    else:
        counts = {severity: 0 for severity in Severity}
        for issue in report.issues:
            counts[issue.severity] += 1
        lines.append(f"{len(report.issues)} issue(s) detected:")
        lines.append("")
        for severity in sorted(Severity, key=lambda s: _SEVERITY_ORDER[s]):
            if counts[severity]:
                lines.append(f"- {_SEVERITY_LABEL[severity]}: {counts[severity]}")
    lines.append("")

    if report.issues:
        lines.append("## Friction Issues")
        lines.append("")
        ranked = sorted(report.issues, key=lambda issue: _SEVERITY_ORDER[issue.severity])
        for issue in ranked:
            lines.append(f"### {_SEVERITY_LABEL[issue.severity]} — {issue.title}")
            lines.append("")
            lines.append(f"- **Screen:** {issue.screen}")
            lines.append(f"- **Heuristic violated:** {issue.heuristic_violated}")
            lines.append(f"- **Persona impact:** {issue.persona_impact}")
            lines.append(f"- **Description:** {issue.description}")
            lines.append(f"- **Suggested fix:** {issue.suggested_fix}")
            lines.append("")

    lines.append("## Screen-by-Screen Walkthrough")
    lines.append("")
    for i, step in enumerate(report.steps, start=1):
        marker = " ⚠️" if step.friction else ""
        lines.append(f"{i}. **{step.screen}**{marker} — {step.action}")
        lines.append(f"   - _{step.reasoning}_")

    return "\n".join(lines) + "\n"
