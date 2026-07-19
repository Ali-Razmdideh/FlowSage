import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import type { AlertsReport, FunnelReport, Persona } from "../lib/types";

export function DashboardPage() {
  const [personas, setPersonas] = useState<Persona[] | null>(null);
  const [funnel, setFunnel] = useState<FunnelReport | null>(null);
  const [alerts, setAlerts] = useState<AlertsReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.listPersonas(), api.getFunnel()])
      .then(([personaList, funnelReport]) => {
        setPersonas(personaList);
        setFunnel(funnelReport);
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load dashboard data.");
      });

    api.getAlerts().then(setAlerts).catch(() => setAlerts(null));
  }, []);

  const topFriction = funnel?.friction_nodes.slice(0, 3) ?? [];
  const hasAlerts =
    alerts !== null && (alerts.calibration_alerts.length > 0 || alerts.churn_alerts.length > 0);

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="font-headline text-3xl">Executive Summary</h1>
        <p className="text-on-surface-variant mt-1">
          Where users will struggle, where they are struggling, and how well FlowSage is
          predicting the difference.
        </p>
      </div>

      {error !== null ? <p className="text-error text-sm">{error}</p> : null}

      {hasAlerts && alerts !== null ? (
        <div className="rounded-xl border-l-4 border-error bg-error-container/20 p-4">
          <span className="inline-block rounded-full bg-error-container px-3 py-1 text-xs font-label uppercase tracking-wide text-on-error-container mb-2">
            Alerts
          </span>
          <ul className="text-sm mt-2 flex flex-col gap-1">
            {alerts.calibration_alerts.map((alert) => (
              <li key={`cal-${alert.persona_name}-${alert.screen}`}>
                Calibration anomaly: {alert.persona_name} on {alert.screen} (delta{" "}
                {alert.delta.toFixed(2)})
              </li>
            ))}
            {alerts.churn_alerts.map((alert) => (
              <li key={`churn-${alert.cohort}`}>
                Churn risk: {alert.cohort} at {(alert.risk_score * 100).toFixed(0)}% —{" "}
                {alert.top_reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <SummaryCard
          label="Total Sessions Observed"
          value={funnel?.total_sessions.toLocaleString() ?? "—"}
        />
        <SummaryCard label="Events Ingested" value={funnel?.total_events.toLocaleString() ?? "—"} />
        <SummaryCard label="Active Personas" value={personas?.length.toString() ?? "—"} />
      </section>

      <section className="bg-surface-container-lowest rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-headline text-xl">Top Friction Nodes</h2>
          <Link to="/journey" className="text-sm text-primary hover:underline">
            View Journey Graph →
          </Link>
        </div>
        {topFriction.length === 0 ? (
          <p className="text-on-surface-variant text-sm">
            No friction detected yet. Ingest events to see the journey graph populate.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {topFriction.map((node) => (
              <li key={`${node.screen}-${node.kind}`} className="ghost-border rounded-lg p-4">
                <p className="font-medium">{node.screen}</p>
                <p className="text-sm text-on-surface-variant mt-1">{node.detail}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="bg-surface-container-lowest rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-headline text-xl">Persona Insights</h2>
          <Link to="/predictive" className="text-sm text-primary hover:underline">
            Manage Personas →
          </Link>
        </div>
        {personas === null || personas.length === 0 ? (
          <p className="text-on-surface-variant text-sm">No personas loaded yet.</p>
        ) : (
          <ul className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {personas.map((persona) => (
              <li key={persona.id} className="ghost-border rounded-lg p-4">
                <p className="font-medium">{persona.name}</p>
                <p className="text-sm text-on-surface-variant mt-1 line-clamp-2">
                  {persona.description}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface-container-lowest rounded-xl p-5">
      <p className="text-xs uppercase tracking-wide text-on-surface-variant font-label">{label}</p>
      <p className="font-headline text-3xl mt-2">{value}</p>
    </div>
  );
}
