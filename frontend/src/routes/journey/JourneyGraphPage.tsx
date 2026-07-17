import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { FrictionKind, FunnelReport } from "../../lib/types";

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

  return (
    <div className="flex flex-col gap-8">
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
                        {step.sessions_entered} sessions · {(step.drop_off_rate * 100).toFixed(0)}%
                        drop-off
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
                  <li key={`${node.screen}-${node.kind}`} className="ghost-border rounded-lg p-4">
                    <span className="text-xs font-label uppercase tracking-wide text-error">
                      {KIND_LABEL[node.kind]}
                    </span>
                    <p className="font-medium mt-1">{node.screen}</p>
                    <p className="text-sm text-on-surface-variant mt-1">{node.detail}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
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
