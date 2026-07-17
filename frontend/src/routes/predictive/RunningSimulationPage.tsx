import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError } from "../../lib/api";
import type { FrictionIssue, RunStatus, SimulationRunDetail, SimulationStep } from "../../lib/types";

interface StepEventPayload {
  id: string;
  sequence: number;
  screen: string;
  action: string;
  reasoning: string;
}

interface DoneEventPayload {
  status: RunStatus;
  error: string | null;
}

export function RunningSimulationPage() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<SimulationRunDetail | null>(null);
  const [steps, setSteps] = useState<SimulationStep[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const seenStepIds = useRef(new Set<string>());

  useEffect(() => {
    if (runId === undefined) return;

    api
      .getSimulation(runId)
      .then((detail) => {
        setRun(detail);
        setSteps(detail.steps);
        for (const step of detail.steps) seenStepIds.current.add(step.id);
      })
      .catch((err: unknown) => {
        setLoadError(err instanceof ApiError ? err.message : "Failed to load run.");
      });

    // Opening the stream is safe even if the run has already finished -- the
    // backend sends its backlog of steps, then an immediate "done" event.
    const source = new EventSource(api.simulationStreamUrl(runId), { withCredentials: true });

    source.addEventListener("step", (event: MessageEvent<string>) => {
      const step = JSON.parse(event.data) as StepEventPayload;
      if (seenStepIds.current.has(step.id)) return;
      seenStepIds.current.add(step.id);
      setSteps((prev) => [...prev, step]);
    });

    source.addEventListener("done", (event: MessageEvent<string>) => {
      const done = JSON.parse(event.data) as DoneEventPayload;
      setRun((prev) => (prev === null ? prev : { ...prev, status: done.status, error: done.error }));
      source.close();
      // Re-fetch once so `issues` (not streamed) reflects the finished run.
      void api.getSimulation(runId).then(setRun);
    });

    source.onerror = () => {
      source.close();
    };

    return () => source.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  if (loadError !== null) {
    return <p className="text-error text-sm">{loadError}</p>;
  }
  if (run === null) {
    return <p className="text-on-surface-variant text-sm">Loading…</p>;
  }

  return (
    <div className="flex flex-col gap-8 max-w-4xl">
      <div>
        <p className="font-label text-xs uppercase tracking-wide text-on-surface-variant">
          {run.status === "running" ? "Live Analysis" : run.status}
        </p>
        <h1 className="font-headline text-3xl">{run.flow_name}</h1>
        <p className="text-on-surface-variant mt-1">Goal: {run.goal}</p>
      </div>

      {run.status === "failed" && run.error !== null ? (
        <p role="alert" className="text-error text-sm">
          {run.error}
        </p>
      ) : null}

      <section className="bg-surface-container-lowest rounded-xl p-6">
        <h2 className="font-headline text-xl mb-4">Agentic Orchestration</h2>
        {steps.length === 0 ? (
          <p className="text-on-surface-variant text-sm">Waiting for the first step…</p>
        ) : (
          <ol className="flex flex-col gap-4">
            {steps.map((step) => (
              <li key={step.id} className="ghost-border rounded-lg p-4">
                <p className="font-medium">{step.screen}</p>
                <p className="text-sm mt-1">{step.action}</p>
                <p className="text-sm text-on-surface-variant italic mt-1">{step.reasoning}</p>
              </li>
            ))}
          </ol>
        )}
      </section>

      {run.issues.length > 0 ? (
        <section className="bg-surface-container-lowest rounded-xl p-6">
          <h2 className="font-headline text-xl mb-4">Friction Issues</h2>
          <ul className="flex flex-col gap-4">
            {run.issues.map((issue) => (
              <FrictionIssueCard key={issue.id} issue={issue} />
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

const SEVERITY_LABEL: Record<FrictionIssue["severity"], string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  critical: "Critical",
};

function FrictionIssueCard({ issue }: { issue: FrictionIssue }) {
  return (
    <li className="ghost-border rounded-lg p-4">
      <div className="flex items-center gap-2">
        <span className="text-xs font-label uppercase tracking-wide text-error">
          {SEVERITY_LABEL[issue.severity]}
        </span>
        <p className="font-medium">{issue.title}</p>
      </div>
      <p className="text-sm text-on-surface-variant mt-2">{issue.description}</p>
      <dl className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm">
        <div>
          <dt className="text-on-surface-variant">Heuristic violated</dt>
          <dd>{issue.heuristic_violated}</dd>
        </div>
        <div>
          <dt className="text-on-surface-variant">Persona impact</dt>
          <dd>{issue.persona_impact}</dd>
        </div>
      </dl>
      <p className="text-sm mt-3">
        <span className="text-on-surface-variant">Suggested fix: </span>
        {issue.suggested_fix}
      </p>
    </li>
  );
}
