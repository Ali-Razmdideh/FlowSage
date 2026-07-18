import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type {
  ChurnRiskSegment,
  CohortComparisonReport,
  FrictionKind,
  FrictionNode,
  FunnelReport,
  NodeIntelligence,
} from "../../lib/types";

const KIND_LABEL: Record<FrictionKind, string> = {
  abnormal_drop_off: "Abnormal drop-off",
  rage_loop: "Rage loop",
  backtrack: "Backtracking",
};

export function JourneyGraphPage() {
  const [cohort, setCohort] = useState("");
  const [device, setDevice] = useState("");
  const [report, setReport] = useState<FunnelReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [churnSegments, setChurnSegments] = useState<ChurnRiskSegment[] | null>(null);
  const [comparison, setComparison] = useState<CohortComparisonReport | null>(null);
  const [selectedNode, setSelectedNode] = useState<FrictionNode | null>(null);
  const [nodeIntel, setNodeIntel] = useState<NodeIntelligence | null>(null);
  const [nodeError, setNodeError] = useState<string | null>(null);

  useEffect(() => {
    const filters = {
      ...(cohort && { cohort }),
      ...(device && { device }),
    };
    api
      .getFunnel(filters)
      .then(setReport)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load journey graph.");
      });
  }, [cohort, device]);

  useEffect(() => {
    const filters = { ...(device && { device }) };
    api.getChurnRisk(filters).then(setChurnSegments).catch(() => setChurnSegments(null));
    api
      .getCohortComparison(filters)
      .then(setComparison)
      .catch(() => setComparison(null));
  }, [device]);

  const openNode = (node: FrictionNode) => {
    setSelectedNode(node);
    setNodeIntel(null);
    setNodeError(null);
    api
      .getNodeIntelligence(node.screen, {
        ...(cohort && { cohort }),
        ...(device && { device }),
      })
      .then(setNodeIntel)
      .catch((err: unknown) => {
        setNodeError(err instanceof ApiError ? err.message : "Failed to load node intelligence.");
      });
  };

  return (
    <div className="flex gap-8">
      <div className="flex flex-col gap-8 flex-1 min-w-0">
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div>
            <h1 className="font-headline text-3xl">Journey Graph</h1>
            <p className="text-on-surface-variant mt-1">
              The most-traveled path through your product, discovered automatically.
            </p>
          </div>
          <div className="flex gap-3">
            <input
              value={cohort}
              onChange={(event) => setCohort(event.target.value)}
              placeholder="Filter by cohort"
              className="ghost-border rounded-lg px-3 py-2 text-sm"
            />
            <input
              value={device}
              onChange={(event) => setDevice(event.target.value)}
              placeholder="Filter by device"
              className="ghost-border rounded-lg px-3 py-2 text-sm"
            />
          </div>
        </div>

        {error !== null ? <p className="text-error text-sm">{error}</p> : null}

        {report !== null && report.funnel.length === 0 ? (
          <EmptyState />
        ) : (
          <>
            <section className="bg-surface-container-lowest rounded-xl p-6">
              <h2 className="font-headline text-xl mb-4">Discovered Funnel</h2>
              <div className="flex flex-col gap-4">
                {report?.funnel.map((step) => {
                  const maxEntered = Math.max(...report.funnel.map((s) => s.sessions_entered), 1);
                  const widthPct = Math.round((step.sessions_entered / maxEntered) * 100);
                  return (
                    <div key={step.screen}>
                      <div className="flex justify-between text-sm mb-1">
                        <span className="font-medium">{step.screen}</span>
                        <span className="text-on-surface-variant">
                          {step.sessions_entered} sessions ·{" "}
                          {(step.drop_off_rate * 100).toFixed(0)}% drop-off
                        </span>
                      </div>
                      <div className="h-2.5 rounded-full bg-surface-container overflow-hidden">
                        <div
                          className="h-full rounded-full bg-primary"
                          style={{ width: `${widthPct}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            <section className="bg-surface-container-lowest rounded-xl p-6">
              <h2 className="font-headline text-xl mb-4">Friction Nodes</h2>
              {report === null || report.friction_nodes.length === 0 ? (
                <p className="text-on-surface-variant text-sm">No friction detected.</p>
              ) : (
                <ul className="flex flex-col gap-3">
                  {report.friction_nodes.map((node) => (
                    <li key={`${node.screen}-${node.kind}`}>
                      <button
                        type="button"
                        onClick={() => openNode(node)}
                        className={`w-full text-left ghost-border rounded-lg p-4 transition-colors hover:bg-surface-container ${
                          selectedNode?.screen === node.screen && selectedNode.kind === node.kind
                            ? "bg-surface-container"
                            : ""
                        }`}
                      >
                        <span className="text-xs font-label uppercase tracking-wide text-error">
                          {KIND_LABEL[node.kind]}
                        </span>
                        <p className="font-medium mt-1">{node.screen}</p>
                        <p className="text-sm text-on-surface-variant mt-1">{node.detail}</p>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <ChurnRiskSection segments={churnSegments} />
            <CohortComparisonSection comparison={comparison} />
          </>
        )}
      </div>

      {selectedNode !== null ? (
        <NodeIntelligenceAside
          node={selectedNode}
          intel={nodeIntel}
          error={nodeError}
          onClose={() => setSelectedNode(null)}
        />
      ) : null}
    </div>
  );
}

function ChurnRiskSection({ segments }: { segments: ChurnRiskSegment[] | null }) {
  if (segments === null || segments.length === 0) return null;
  const maxRisk = Math.max(...segments.map((s) => s.risk_score), 0.01);

  return (
    <section className="bg-surface-container-lowest rounded-xl p-6">
      <h2 className="font-headline text-xl mb-1">Churn Risk by Segment</h2>
      <p className="text-sm text-on-surface-variant mb-4">
        Blends funnel drop-off with friction density per cohort.
      </p>
      <div className="flex flex-col gap-4">
        {segments.map((segment) => (
          <div key={segment.cohort}>
            <div className="flex justify-between text-sm mb-1">
              <span className="font-medium">{segment.cohort}</span>
              <span className="text-on-surface-variant">
                {(segment.risk_score * 100).toFixed(0)}% risk · {segment.sessions_at_risk} sessions
                at risk
              </span>
            </div>
            <div className="h-2.5 rounded-full bg-surface-container overflow-hidden">
              <div
                className="h-full rounded-full bg-error"
                style={{ width: `${(segment.risk_score / maxRisk) * 100}%` }}
              />
            </div>
            <p className="text-xs text-on-surface-variant mt-1">{segment.top_reason}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function CohortComparisonSection({ comparison }: { comparison: CohortComparisonReport | null }) {
  if (comparison === null || comparison.cohorts.length < 2) return null;
  const cohortNames = comparison.cohorts.map((c) => c.cohort);

  return (
    <section className="bg-surface-container-lowest rounded-xl p-6">
      <h2 className="font-headline text-xl mb-1">Cohort Path Comparison</h2>
      <p className="text-sm text-on-surface-variant mb-4">
        Drop-off rate per screen, ranked by how much cohorts diverge.
      </p>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-on-surface-variant">
            <th className="font-normal pb-2">Screen</th>
            {cohortNames.map((name) => (
              <th key={name} className="font-normal pb-2">
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {comparison.screens.map((screen) => (
            <tr key={screen.screen}>
              <td className="py-2">{screen.screen}</td>
              {cohortNames.map((name) => {
                const value = screen.drop_off_by_cohort[name];
                return (
                  <td key={name} className="py-2">
                    {value === undefined ? "—" : `${(value * 100).toFixed(0)}%`}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function NodeIntelligenceAside({
  node,
  intel,
  error,
  onClose,
}: {
  node: FrictionNode;
  intel: NodeIntelligence | null;
  error: string | null;
  onClose: () => void;
}) {
  return (
    <aside className="w-[380px] shrink-0 bg-surface-container-lowest rounded-xl p-6 h-fit sticky top-6">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h2 className="font-headline text-xl">Node Intelligence</h2>
          <p className="text-sm text-on-surface-variant">Analysis of {node.screen}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="text-on-surface-variant hover:text-on-surface text-xl leading-none"
        >
          ×
        </button>
      </div>

      {error !== null ? <p className="text-error text-sm">{error}</p> : null}

      {intel === null && error === null ? (
        <p className="text-on-surface-variant text-sm">Loading…</p>
      ) : null}

      {intel !== null ? (
        <div className="flex flex-col gap-4">
          <div className="rounded-lg border-l-4 border-error bg-error-container/20 p-4">
            <p className="text-xs font-label uppercase tracking-wide text-error mb-1">
              AI Insight
            </p>
            <p className="text-sm">{intel.ai_insight}</p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="ghost-border rounded-lg p-3">
              <p className="text-xs text-on-surface-variant">Drop-off Rate</p>
              <p className="font-headline text-xl">{(intel.drop_off_rate * 100).toFixed(1)}%</p>
            </div>
            <div className="ghost-border rounded-lg p-3">
              <p className="text-xs text-on-surface-variant">Avg. Time on Node</p>
              <p className="font-headline text-xl">
                {intel.avg_seconds_on_node === null
                  ? "—"
                  : `${intel.avg_seconds_on_node.toFixed(0)}s`}
              </p>
            </div>
          </div>

          {intel.recommendations.length > 0 ? (
            <div>
              <p className="text-xs font-label uppercase tracking-wide text-on-surface-variant mb-2">
                Re-engagement Recommendations
              </p>
              <ul className="flex flex-col gap-2">
                {intel.recommendations.map((rec) => (
                  <li key={rec.rank} className="ghost-border rounded-lg p-3">
                    <p className="font-medium text-sm">
                      {rec.rank} — {rec.title}
                    </p>
                    <p className="text-xs text-on-surface-variant mt-1">{rec.description}</p>
                    {rec.expected_lift_pct !== null ? (
                      <p className="text-xs text-primary mt-1">
                        Expected conversion lift: +{rec.expected_lift_pct.toFixed(0)}%
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
}

function EmptyState() {
  return (
    <div className="bg-surface-container-lowest rounded-xl p-16 text-center">
      <h2 className="font-headline text-2xl mb-2">Awaiting Event Ingestion</h2>
      <p className="text-on-surface-variant max-w-md mx-auto">
        The journey graph will materialize once events start arriving via{" "}
        <code className="font-mono text-sm">POST /v1/events</code>.
      </p>
    </div>
  );
}
