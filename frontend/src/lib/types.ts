export interface User {
  id: string;
  email: string;
  created_at: string;
}

export interface Persona {
  id: string;
  slug: string;
  name: string;
  description: string;
  baseline: boolean;
  tech_affinity: string;
  primary_device: string;
  discovery_mode: string;
  contextual_triggers: string[];
  technical_literacy: number;
  anxiety: number;
  patience: number;
  curiosity: number;
}

export type RunStatus = "queued" | "running" | "completed" | "failed";

export interface SimulationRun {
  id: string;
  flow_name: string;
  goal: string;
  persona_id: string;
  status: RunStatus;
  error: string | null;
}

export interface SimulationStep {
  id: string;
  sequence: number;
  screen: string;
  action: string;
  reasoning: string;
}

export type FrictionSeverity = "low" | "medium" | "high" | "critical";

export interface FrictionIssue {
  id: string;
  screen: string;
  severity: FrictionSeverity;
  title: string;
  heuristic_violated: string;
  persona_impact: string;
  description: string;
  suggested_fix: string;
}

export interface SimulationRunDetail extends SimulationRun {
  steps: SimulationStep[];
  issues: FrictionIssue[];
}

export type FrictionKind = "abnormal_drop_off" | "rage_loop" | "backtrack";

export interface FunnelStep {
  screen: string;
  sessions_entered: number;
  sessions_continued: number;
  drop_off_rate: number;
}

export interface FrictionNode {
  screen: string;
  kind: FrictionKind;
  detail: string;
  sessions_affected: number;
}

export interface FunnelReport {
  funnel: FunnelStep[];
  friction_nodes: FrictionNode[];
  total_sessions: number;
  total_events: number;
}

export interface FunnelFilters {
  cohort?: string;
  device?: string;
  since?: string;
}
