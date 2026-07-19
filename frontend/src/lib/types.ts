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

export interface ScreenCalibration {
  screen: string;
  predicted_score: number;
  observed_score: number;
  delta: number;
  anomaly: boolean;
}

export interface PersonaCalibration {
  persona_id: string;
  persona_name: string;
  run_id: string;
  screens: ScreenCalibration[];
}

export interface AccuracyPoint {
  persona_id: string;
  persona_name: string;
  complexity: number;
  accuracy: number;
}

export interface CalibrationReport {
  personas: PersonaCalibration[];
  accuracy_points: AccuracyPoint[];
  has_anomaly: boolean;
}

export type RetrainingStatus = "queued" | "running" | "completed" | "failed";

export interface RetrainingJob {
  id: string;
  persona_id: string;
  status: RetrainingStatus;
  epoch: number;
  total_epochs: number;
  progress: number;
  error: string | null;
}

export interface CohortFunnelSummary {
  cohort: string;
  total_sessions: number;
  funnel: FunnelStep[];
}

export interface ScreenCohortComparison {
  screen: string;
  drop_off_by_cohort: Record<string, number>;
  max_delta: number;
}

export interface CohortComparisonReport {
  cohorts: CohortFunnelSummary[];
  screens: ScreenCohortComparison[];
}

export interface ChurnRiskSegment {
  cohort: string;
  risk_score: number;
  sessions_at_risk: number;
  top_reason: string;
}

export interface Recommendation {
  rank: number;
  title: string;
  description: string;
  expected_lift_pct: number | null;
}

export interface NodeIntelligence {
  screen: string;
  drop_off_rate: number;
  avg_seconds_on_node: number | null;
  friction_nodes: FrictionNode[];
  ai_insight: string;
  recommendations: Recommendation[];
}

export interface CalibrationAlert {
  persona_name: string;
  screen: string;
  delta: number;
}

export interface ChurnAlert {
  cohort: string;
  risk_score: number;
  top_reason: string;
}

export interface AlertsReport {
  calibration_alerts: CalibrationAlert[];
  churn_alerts: ChurnAlert[];
}

export interface SlackExportResult {
  status: string;
}

export interface JiraExportResult {
  issue_key: string;
}
