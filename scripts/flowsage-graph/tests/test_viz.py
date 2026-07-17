from flowsage_graph.models import FrictionKind, FrictionNode, FunnelReport, FunnelStep
from flowsage_graph.viz import render_html


def test_render_html_includes_funnel_and_counts() -> None:
    report = FunnelReport(
        funnel=[
            FunnelStep(screen="landing", sessions_entered=10, sessions_continued=8),
            FunnelStep(screen="cart", sessions_entered=8, sessions_continued=8),
        ],
        friction_nodes=[],
        total_sessions=10,
        total_events=25,
    )
    html = render_html(report)
    assert "10 sessions" in html
    assert "25 events" in html
    assert "landing" in html
    assert "20% drop-off" in html
    assert "No friction detected." in html


def test_render_html_escapes_screen_names() -> None:
    report = FunnelReport(
        funnel=[
            FunnelStep(screen="<script>alert(1)</script>", sessions_entered=1, sessions_continued=1)
        ],
        friction_nodes=[
            FrictionNode(
                screen="<b>cart</b>",
                kind=FrictionKind.RAGE_LOOP,
                detail="<i>detail</i>",
                sessions_affected=1,
            )
        ],
        total_sessions=1,
        total_events=1,
    )
    html = render_html(report)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;cart&lt;/b&gt;" in html
    assert "&lt;i&gt;detail&lt;/i&gt;" in html


def test_render_html_handles_empty_report() -> None:
    html = render_html(FunnelReport())
    assert "No sessions found." in html
    assert "No friction detected." in html
