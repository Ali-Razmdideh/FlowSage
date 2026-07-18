import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { AccuracyPoint, CalibrationReport, RetrainingJob } from "../../lib/types";

interface RetrainingDonePayload {
  status: RetrainingJob["status"];
  error: string | null;
}

export function CalibrationPage() {
  const [report, setReport] = useState<CalibrationReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<RetrainingJob | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  const loadReport = () => {
    api
      .getCalibrationReport()
      .then(setReport)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load calibration report.");
      });
  };

  useEffect(() => {
    loadReport();
    return () => sourceRef.current?.close();
  }, []);

  const startRetraining = async (personaId: string) => {
    try {
      const job = await api.startRetraining(personaId);
      setActiveJob(job);

      const source = new EventSource(api.retrainingStreamUrl(job.id), { withCredentials: true });
      sourceRef.current = source;

      source.addEventListener("progress", (event: MessageEvent<string>) => {
        setActiveJob(JSON.parse(event.data) as RetrainingJob);
      });

      source.addEventListener("done", (event: MessageEvent<string>) => {
        JSON.parse(event.data) as RetrainingDonePayload;
        source.close();
        setActiveJob(null);
        loadReport();
      });

      source.onerror = () => {
        source.close();
      };
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start retraining.");
    }
  };

  if (error !== null) {
    return <p className="text-error text-sm">{error}</p>;
  }

  if (activeJob !== null) {
    return <RetrainingView job={activeJob} />;
  }

  if (report === null) {
    return <p className="text-on-surface-variant text-sm">Loading…</p>;
  }

  return report.has_anomaly ? (
    <AnomalyView report={report} onRetrain={startRetraining} />
  ) : (
    <OptimizedView report={report} />
  );
}

function RetrainingView({ job }: { job: RetrainingJob }) {
  return (
    <div className="flex flex-col gap-8 max-w-3xl">
      <div>
        <span className="inline-block rounded-full bg-error-container px-3 py-1 text-xs font-label uppercase tracking-wide text-on-error-container">
          Active Re-training
        </span>
        <h1 className="font-headline text-3xl mt-3">Persona Re-calibration in Progress</h1>
      </div>

      <section className="bg-surface-container-lowest rounded-xl p-6">
        <div className="flex justify-between text-sm mb-2">
          <span>
            {job.epoch} / {job.total_epochs} anomalous screens processed
          </span>
          <span className="font-medium">{job.progress.toFixed(0)}%</span>
        </div>
        <div className="h-2.5 rounded-full bg-surface-container overflow-hidden">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${job.progress}%` }}
          />
        </div>
        {job.status === "failed" && job.error !== null ? (
          <p role="alert" className="text-error text-sm mt-3">
            {job.error}
          </p>
        ) : null}
      </section>
    </div>
  );
}

function AnomalyView({
  report,
  onRetrain,
}: {
  report: CalibrationReport;
  onRetrain: (personaId: string) => void;
}) {
  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="font-headline text-3xl">Calibration Insights</h1>
        <p className="text-on-surface-variant mt-1">
          Reconciling predictive persona models with real-world user telemetry.
        </p>
      </div>

      <div className="rounded-xl border-l-4 border-error bg-error-container/20 p-6">
        <p className="font-medium text-error">Calibration Anomaly Detected</p>
        <p className="text-sm mt-1">
          One or more personas are miscalibrated for the observed journey. Inspect the affected
          screens below and initiate retraining to correct the drift.
        </p>
      </div>

      {report.personas.map((persona) => {
        const hasAnomaly = persona.screens.some((s) => s.anomaly);
        return (
          <section key={persona.persona_id} className="bg-surface-container-lowest rounded-xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-headline text-xl">{persona.persona_name}</h2>
              {hasAnomaly ? (
                <button
                  type="button"
                  onClick={() => onRetrain(persona.persona_id)}
                  className="text-sm font-medium text-primary hover:underline"
                >
                  Initiate Retraining →
                </button>
              ) : null}
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-on-surface-variant">
                  <th className="font-normal pb-2">Screen</th>
                  <th className="font-normal pb-2">Predicted</th>
                  <th className="font-normal pb-2">Observed</th>
                  <th className="font-normal pb-2">Delta</th>
                </tr>
              </thead>
              <tbody>
                {persona.screens.map((screen) => (
                  <tr
                    key={screen.screen}
                    className={screen.anomaly ? "bg-error-container/10" : undefined}
                  >
                    <td className="py-2">{screen.screen}</td>
                    <td className="py-2">{screen.predicted_score.toFixed(2)}</td>
                    <td className="py-2">{screen.observed_score.toFixed(2)}</td>
                    <td className={`py-2 ${screen.anomaly ? "text-error font-medium" : ""}`}>
                      {screen.delta >= 0 ? "+" : ""}
                      {screen.delta.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        );
      })}

      <AccuracyScatter points={report.accuracy_points} />
    </div>
  );
}

function OptimizedView({ report }: { report: CalibrationReport }) {
  const meanAccuracy =
    report.accuracy_points.length === 0
      ? 1
      : report.accuracy_points.reduce((sum, p) => sum + p.accuracy, 0) /
        report.accuracy_points.length;

  return (
    <div className="flex flex-col gap-8">
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="font-headline text-3xl">Calibration Insights</h1>
          <p className="text-on-surface-variant mt-1">
            Persona accuracy achieves nominal equilibrium.
          </p>
        </div>
        <span className="rounded-full bg-surface-container px-3 py-1 text-xs font-label uppercase tracking-wide">
          Optimized Status
        </span>
      </div>

      <section className="bg-surface-container-lowest rounded-xl p-6 flex items-center gap-3">
        <span className="material-symbols-outlined text-2xl text-primary">check_circle</span>
        <div>
          <p className="font-medium">System Optimized</p>
          <p className="text-sm text-on-surface-variant">
            Synthetic personas are tracking reality within expected margins. No recalibration
            required.
          </p>
        </div>
      </section>

      <section className="bg-surface-container-lowest rounded-xl p-6">
        <p className="font-label text-xs uppercase tracking-wide text-on-surface-variant">
          Total Convergence
        </p>
        <p className="font-headline text-5xl text-primary mt-1">
          {(meanAccuracy * 100).toFixed(1)}%
        </p>
      </section>

      <AccuracyScatter points={report.accuracy_points} />
    </div>
  );
}

const SCATTER_SIZE = 320;

function AccuracyScatter({ points }: { points: AccuracyPoint[] }) {
  return (
    <section className="bg-surface-container-lowest rounded-xl p-6">
      <h2 className="font-headline text-xl mb-1">Persona Accuracy Mapping</h2>
      <p className="text-sm text-on-surface-variant mb-4">
        Synthetic persona alignment versus real-world cohort segments.
      </p>
      {points.length === 0 ? (
        <p className="text-on-surface-variant text-sm">No persona runs to plot yet.</p>
      ) : (
        <svg
          viewBox={`0 0 ${SCATTER_SIZE} ${SCATTER_SIZE}`}
          className="w-full max-w-md"
          role="img"
          aria-label="Persona accuracy vs. journey complexity scatter plot"
        >
          <line x1="0" y1={SCATTER_SIZE} x2={SCATTER_SIZE} y2={SCATTER_SIZE} className="stroke-outline-variant" strokeWidth={1} />
          <line x1="0" y1="0" x2="0" y2={SCATTER_SIZE} className="stroke-outline-variant" strokeWidth={1} />
          {points.map((point) => (
            <circle
              key={point.persona_id}
              cx={point.complexity * SCATTER_SIZE}
              cy={(1 - point.accuracy) * SCATTER_SIZE}
              r={8}
              className="fill-primary"
            >
              <title>
                {point.persona_name}: {(point.accuracy * 100).toFixed(0)}% accuracy
              </title>
            </circle>
          ))}
        </svg>
      )}
    </section>
  );
}
