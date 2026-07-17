"""Render a `FunnelReport` as a static, dependency-free HTML page."""

from __future__ import annotations

from html import escape

from flowsage_graph.models import FrictionKind, FunnelReport

_KIND_LABEL = {
    FrictionKind.ABNORMAL_DROP_OFF: "Abnormal drop-off",
    FrictionKind.RAGE_LOOP: "Rage loop",
    FrictionKind.BACKTRACK: "Backtracking",
}


def render_html(report: FunnelReport) -> str:
    max_entered = max((step.sessions_entered for step in report.funnel), default=1) or 1

    funnel_rows = []
    for step in report.funnel:
        bar_pct = round(100 * step.sessions_entered / max_entered)
        funnel_rows.append(
            f"""
            <div class="funnel-step">
              <div class="funnel-label">
                <span class="screen">{escape(step.screen)}</span>
                <span class="stat">{step.sessions_entered} sessions
                  &middot; {step.drop_off_rate:.0%} drop-off</span>
              </div>
              <div class="bar-track"><div class="bar-fill" style="width: {bar_pct}%"></div></div>
            </div>
            """
        )

    friction_rows = []
    for node in report.friction_nodes:
        friction_rows.append(
            f"""
            <li class="friction-item friction-{node.kind.value}">
              <span class="badge">{escape(_KIND_LABEL[node.kind])}</span>
              <strong>{escape(node.screen)}</strong>
              <p>{escape(node.detail)}</p>
            </li>
            """
        )
    no_sessions_html = '<p class="empty">No sessions found.</p>'
    funnel_html = "".join(funnel_rows) if funnel_rows else no_sessions_html

    no_friction_html = '<p class="empty">No friction detected.</p>'
    friction_html = (
        '<ul class="friction-list">' + "".join(friction_rows) + "</ul>"
        if friction_rows
        else no_friction_html
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>FlowSage Funnel Report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Inter, sans-serif; margin: 0;
         background: #f5f6f8; color: #1b1c1d; }}
  main {{ max-width: 860px; margin: 0 auto; padding: 2.5rem 1.5rem; }}
  h1 {{ font-size: 1.75rem; margin-bottom: 0.25rem; }}
  .subtitle {{ color: #55585f; margin-bottom: 2rem; }}
  section {{ background: #fff; border-radius: 12px; padding: 1.5rem 1.75rem;
             margin-bottom: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
  section h2 {{ font-size: 1.1rem; margin-top: 0; }}
  .funnel-step {{ margin-bottom: 1rem; }}
  .funnel-label {{ display: flex; justify-content: space-between; font-size: 0.9rem;
                   margin-bottom: 0.35rem; }}
  .funnel-label .screen {{ font-weight: 600; }}
  .funnel-label .stat {{ color: #55585f; }}
  .bar-track {{ background: #e6e8ec; border-radius: 999px; height: 10px; overflow: hidden; }}
  .bar-fill {{ background: #094cb2; height: 100%; border-radius: 999px; }}
  .friction-list {{ list-style: none; margin: 0; padding: 0; }}
  .friction-item {{ padding: 0.75rem 0; border-top: 1px solid #eceef1; }}
  .friction-item:first-child {{ border-top: none; }}
  .friction-item p {{ margin: 0.25rem 0 0; color: #434653; font-size: 0.9rem; }}
  .badge {{ display: inline-block; font-size: 0.72rem; font-weight: 700;
            text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem;
            border-radius: 999px; margin-right: 0.5rem; background: #ffdad6; color: #ba1a1a; }}
  .friction-rage_loop .badge {{ background: #dcc661; color: #6d5e00; }}
  .friction-backtrack .badge {{ background: #d9e2ff; color: #094cb2; }}
  .empty {{ color: #55585f; }}
</style>
</head>
<body>
<main>
  <h1>FlowSage Funnel Report</h1>
  <p class="subtitle">{report.total_sessions} sessions &middot; {report.total_events} events</p>
  <section>
    <h2>Discovered Funnel</h2>
    {funnel_html}
  </section>
  <section>
    <h2>Friction Nodes</h2>
    {friction_html}
  </section>
</main>
</body>
</html>
"""
