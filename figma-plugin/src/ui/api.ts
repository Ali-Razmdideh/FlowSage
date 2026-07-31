// figma-plugin/src/ui/api.ts
import type { Persona, SimulationRun } from "../shared/types";

export interface BackendConfig {
  baseUrl: string;
  apiKey: string;
}

export interface ExportedFile {
  filename: string;
  bytes: Uint8Array;
}

async function assertOk(response: { ok: boolean; status: number }): Promise<void> {
  if (!response.ok) {
    throw new Error(`FlowSage API request failed with status ${response.status}`);
  }
}

/** Strips a single trailing slash so a user-pasted Base URL like
 * `https://127.0.0.1/api/` doesn't produce a double-slash (`.../api//personas`)
 * when concatenated with a leading-slash path below. */
function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/+$/, "");
}

export async function listPersonas(config: BackendConfig): Promise<Persona[]> {
  const response = await fetch(`${normalizeBaseUrl(config.baseUrl)}/personas`, {
    headers: { "X-API-Key": config.apiKey },
  });
  await assertOk(response);
  return response.json();
}

export async function createSimulationRun(
  config: BackendConfig,
  params: { personaId: string; goal: string; flowName: string; files: ExportedFile[] },
): Promise<SimulationRun> {
  const formData = new FormData();
  formData.set("persona_id", params.personaId);
  formData.set("goal", params.goal);
  formData.set("flow_name", params.flowName);
  for (const file of params.files) {
    formData.append("files", new Blob([file.bytes as BlobPart], { type: "image/png" }), file.filename);
  }

  const response = await fetch(`${normalizeBaseUrl(config.baseUrl)}/simulations`, {
    method: "POST",
    headers: { "X-API-Key": config.apiKey },
    body: formData,
  });
  await assertOk(response);
  return response.json();
}

export async function getSimulationRun(
  config: BackendConfig,
  runId: string,
): Promise<SimulationRun> {
  const response = await fetch(`${normalizeBaseUrl(config.baseUrl)}/simulations/${runId}`, {
    headers: { "X-API-Key": config.apiKey },
  });
  await assertOk(response);
  return response.json();
}
