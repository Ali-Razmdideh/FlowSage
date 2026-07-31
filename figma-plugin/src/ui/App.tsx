// figma-plugin/src/ui/App.tsx
import { useEffect, useState } from "react";
import { callPlugin } from "./bridge";
import { createSimulationRun, getSimulationRun, listPersonas, type BackendConfig } from "./api";
import { buildScreenFilename } from "../shared/simulationClient";
import type { Persona, SimulationRun } from "../shared/types";

interface ExportedFrame {
  index: number;
  bytes: number[];
}

async function pollUntilDone(
  config: BackendConfig,
  runId: string,
  intervalMs = 1500,
): Promise<SimulationRun> {
  for (;;) {
    const run = await getSimulationRun(config, runId);
    if (run.status === "completed" || run.status === "failed") return run;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

export function App() {
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [personaId, setPersonaId] = useState("");
  const [goal, setGoal] = useState("");
  const [flowName, setFlowName] = useState("");
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [message, setMessage] = useState("");

  useEffect(() => {
    async function loadSettings() {
      try {
        const settings = await callPlugin<{ baseUrl: string; apiKey: string }>("get-settings");
        setBaseUrl(settings.baseUrl);
        setApiKey(settings.apiKey);
        if (settings.baseUrl && settings.apiKey) {
          const loadedPersonas = await listPersonas({
            baseUrl: settings.baseUrl,
            apiKey: settings.apiKey,
          });
          setPersonas(loadedPersonas);
        }
      } catch (error) {
        setStatus("error");
        setMessage(error instanceof Error ? error.message : "Failed to load settings");
      }
    }

    loadSettings();
  }, []);

  async function handleSaveSettings() {
    try {
      await callPlugin("save-settings", { baseUrl, apiKey });
      const loadedPersonas = await listPersonas({ baseUrl, apiKey });
      setPersonas(loadedPersonas);
    } catch (error) {
      setStatus("error");
      setMessage(error instanceof Error ? error.message : "Failed to save settings");
    }
  }

  async function handleRun() {
    setStatus("running");
    setMessage("");
    try {
      const config: BackendConfig = { baseUrl, apiKey };
      const exported = await callPlugin<ExportedFrame[]>("export-selection");
      const files = exported.map((frame) => ({
        filename: buildScreenFilename(frame.index),
        bytes: new Uint8Array(frame.bytes),
      }));

      const created = await createSimulationRun(config, { personaId, goal, flowName, files });
      const finished = await pollUntilDone(config, created.id);

      if (finished.status === "failed") {
        setStatus("error");
        setMessage(finished.error ?? "Simulation run failed");
        return;
      }

      const issues = finished.issues ?? [];
      await callPlugin("annotate", { issues });
      setStatus("done");
      setMessage(`${issues.length} issue${issues.length === 1 ? "" : "s"} annotated`);
    } catch (error) {
      setStatus("error");
      setMessage(error instanceof Error ? error.message : "Unknown error");
    }
  }

  return (
    <div style={{ fontFamily: "sans-serif", padding: 12, display: "flex", flexDirection: "column", gap: 12 }}>
      <section>
        <h3>Settings</h3>
        <label htmlFor="base-url">Base URL</label>
        <input id="base-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        <label htmlFor="api-key">API key</label>
        <input id="api-key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
        <button onClick={handleSaveSettings}>Save</button>
      </section>

      <section>
        <h3>Run a simulation</h3>
        <label htmlFor="persona">Persona</label>
        <select id="persona" value={personaId} onChange={(e) => setPersonaId(e.target.value)}>
          <option value="">Select a persona…</option>
          {personas.map((persona) => (
            <option key={persona.id} value={persona.id}>
              {persona.name}
            </option>
          ))}
        </select>
        <label htmlFor="goal">Goal</label>
        <input id="goal" value={goal} onChange={(e) => setGoal(e.target.value)} />
        <label htmlFor="flow-name">Flow name</label>
        <input id="flow-name" value={flowName} onChange={(e) => setFlowName(e.target.value)} />
        <button onClick={handleRun} disabled={status === "running"}>
          Run & Annotate
        </button>
      </section>

      {status !== "idle" && <p>{status === "running" ? "Running…" : message}</p>}
    </div>
  );
}
