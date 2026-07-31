export interface Persona {
  id: string;
  slug: string;
  name: string;
}

export type RunStatus = "queued" | "running" | "completed" | "failed";

export interface FrictionIssue {
  id: string;
  screen: string;
  severity: "low" | "medium" | "high" | "critical";
  title: string;
  heuristic_violated: string;
  persona_impact: string;
  description: string;
  suggested_fix: string;
}

export interface SimulationRun {
  id: string;
  flow_name: string;
  goal: string;
  persona_id: string;
  status: RunStatus;
  error: string | null;
  issues?: FrictionIssue[];
}
