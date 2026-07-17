import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../../lib/api";
import type { Persona } from "../../lib/types";

export function PredictiveEnginePage() {
  const navigate = useNavigate();
  const [personas, setPersonas] = useState<Persona[] | null>(null);
  const [personaId, setPersonaId] = useState("");
  const [goal, setGoal] = useState("Complete purchase");
  const [flowName, setFlowName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api
      .listPersonas()
      .then((list) => {
        setPersonas(list);
        const first = list[0];
        if (first) setPersonaId(first.id);
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load personas.");
      });
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (files.length === 0) {
      setError("Select at least one screenshot.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const run = await api.createSimulation({ personaId, goal, flowName, files });
      navigate(`/predictive/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start simulation.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-8 max-w-3xl">
      <div>
        <h1 className="font-headline text-3xl">Predictive Engine</h1>
        <p className="text-on-surface-variant mt-1">
          Walk a screenshot sequence with an LLM persona and get a friction report before a
          real user sees it.
        </p>
      </div>

      <section className="bg-surface-container-lowest rounded-xl p-6">
        <h2 className="font-headline text-xl mb-4">Persona Library</h2>
        {personas === null ? (
          <p className="text-on-surface-variant text-sm">Loading personas…</p>
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

      <section className="bg-surface-container-lowest rounded-xl p-6">
        <h2 className="font-headline text-xl mb-4">Run New Simulation</h2>
        <form onSubmit={(event) => void handleSubmit(event)} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Persona</span>
            <select
              required
              value={personaId}
              onChange={(event) => setPersonaId(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2"
            >
              {personas?.map((persona) => (
                <option key={persona.id} value={persona.id}>
                  {persona.name}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Flow name</span>
            <input
              required
              value={flowName}
              onChange={(event) => setFlowName(event.target.value)}
              placeholder="Checkout Flow"
              className="ghost-border rounded-lg px-3 py-2"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Goal</span>
            <input
              required
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">
              Screenshots, in flow order (png/jpg/webp)
            </span>
            <input
              required
              type="file"
              multiple
              accept="image/png,image/jpeg,image/webp"
              onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
              className="ghost-border rounded-lg px-3 py-2"
            />
          </label>

          {error !== null ? (
            <p role="alert" className="text-sm text-error">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={submitting || personas === null || personas.length === 0}
            className="rounded-lg bg-primary py-2.5 text-on-primary font-medium hover:opacity-90 transition disabled:opacity-50"
          >
            {submitting ? "Starting…" : "Run Simulation"}
          </button>
        </form>
      </section>
    </div>
  );
}
