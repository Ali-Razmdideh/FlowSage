import type {
  AlertsReport,
  ApiKey,
  ApiKeyCreated,
  CalibrationReport,
  CalibrationSettings,
  ChurnRiskSegment,
  CohortComparisonReport,
  FunnelFilters,
  FunnelReport,
  JiraConnectPayload,
  JiraExportResult,
  JiraStatus,
  Member,
  MemberAddPayload,
  NodeIntelligence,
  Persona,
  PersonaCreatePayload,
  PersonaDetail,
  PersonaUpdatePayload,
  RetrainingJob,
  Role,
  SimulationRun,
  SimulationRunDetail,
  SlackExportResult,
  SlackStatus,
  User,
  Webhook,
  WebhookCreated,
  WebhookDelivery,
  WebhookTestResult,
  WebhookUpdatePayload,
  Workspace,
  WorkspaceSummary,
  WorkspaceUpdatePayload,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function parseErrorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
    }
  } catch {
    // response wasn't JSON; fall through to statusText
  }
  return response.statusText;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(init?.body !== undefined && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorDetail(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function toQueryString(filters: FunnelFilters): string {
  const params = new URLSearchParams();
  if (filters.cohort) params.set("cohort", filters.cohort);
  if (filters.device) params.set("device", filters.device);
  if (filters.since) params.set("since", filters.since);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

interface CohortCompareFilters {
  cohorts?: string[];
  device?: string;
  since?: string;
}

function toCohortCompareQueryString(filters: CohortCompareFilters): string {
  const params = new URLSearchParams();
  for (const cohort of filters.cohorts ?? []) params.append("cohorts", cohort);
  if (filters.device) params.set("device", filters.device);
  if (filters.since) params.set("since", filters.since);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export interface CreateSimulationInput {
  personaId: string;
  goal: string;
  flowName: string;
  files: File[];
}

export const api = {
  login: (email: string, password: string): Promise<User> =>
    request<User>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  logout: (): Promise<{ status: string }> =>
    request<{ status: string }>("/auth/logout", { method: "POST" }),

  me: (): Promise<User> => request<User>("/auth/me"),

  listPersonas: (): Promise<Persona[]> => request<Persona[]>("/personas"),

  getPersona: (id: string): Promise<PersonaDetail> => request<PersonaDetail>(`/personas/${id}`),

  createPersona: (payload: PersonaCreatePayload): Promise<Persona> =>
    request<Persona>("/personas", { method: "POST", body: JSON.stringify(payload) }),

  updatePersona: (id: string, payload: PersonaUpdatePayload): Promise<Persona> =>
    request<Persona>(`/personas/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),

  resetPersona: (id: string): Promise<Persona> =>
    request<Persona>(`/personas/${id}/reset`, { method: "POST" }),

  deletePersona: (id: string): Promise<void> =>
    request<void>(`/personas/${id}`, { method: "DELETE" }),

  createSimulation: (input: CreateSimulationInput): Promise<SimulationRun> => {
    const formData = new FormData();
    formData.set("persona_id", input.personaId);
    formData.set("goal", input.goal);
    formData.set("flow_name", input.flowName);
    for (const file of input.files) {
      formData.append("files", file);
    }
    return request<SimulationRun>("/simulations", { method: "POST", body: formData });
  },

  getSimulation: (id: string): Promise<SimulationRunDetail> =>
    request<SimulationRunDetail>(`/simulations/${id}`),

  getFunnel: (filters: FunnelFilters = {}): Promise<FunnelReport> =>
    request<FunnelReport>(`/graph/funnel${toQueryString(filters)}`),

  simulationStreamUrl: (id: string): string => `${API_BASE}/simulations/${id}/stream`,

  getCalibrationReport: (): Promise<CalibrationReport> =>
    request<CalibrationReport>("/calibration/report"),

  startRetraining: (personaId: string): Promise<RetrainingJob> =>
    request<RetrainingJob>("/calibration/retrain", {
      method: "POST",
      body: JSON.stringify({ persona_id: personaId }),
    }),

  getRetrainingJob: (id: string): Promise<RetrainingJob> =>
    request<RetrainingJob>(`/calibration/retrain/${id}`),

  retrainingStreamUrl: (id: string): string => `${API_BASE}/calibration/retrain/${id}/stream`,

  getCohortComparison: (filters: CohortCompareFilters = {}): Promise<CohortComparisonReport> =>
    request<CohortComparisonReport>(`/graph/cohorts/compare${toCohortCompareQueryString(filters)}`),

  getChurnRisk: (filters: FunnelFilters = {}): Promise<ChurnRiskSegment[]> =>
    request<ChurnRiskSegment[]>(`/graph/churn-risk${toQueryString(filters)}`),

  getNodeIntelligence: (
    screen: string,
    filters: FunnelFilters = {},
  ): Promise<NodeIntelligence> =>
    request<NodeIntelligence>(
      `/graph/nodes/${encodeURIComponent(screen)}${toQueryString(filters)}`,
    ),

  getAlerts: (): Promise<AlertsReport> => request<AlertsReport>("/alerts"),

  exportIssueToSlack: (issueId: string): Promise<SlackExportResult> =>
    request<SlackExportResult>(`/friction-issues/${issueId}/export/slack`, { method: "POST" }),

  exportIssueToJira: (issueId: string): Promise<JiraExportResult> =>
    request<JiraExportResult>(`/friction-issues/${issueId}/export/jira`, { method: "POST" }),

  exportNodeToSlack: (screen: string): Promise<SlackExportResult> =>
    request<SlackExportResult>(
      `/graph/nodes/${encodeURIComponent(screen)}/export/slack`,
      { method: "POST" },
    ),

  exportNodeToJira: (screen: string): Promise<JiraExportResult> =>
    request<JiraExportResult>(
      `/graph/nodes/${encodeURIComponent(screen)}/export/jira`,
      { method: "POST" },
    ),

  getModelCalibrationSettings: (): Promise<CalibrationSettings> =>
    request<CalibrationSettings>("/settings/model-calibration"),

  updateModelCalibrationSettings: (payload: CalibrationSettings): Promise<CalibrationSettings> =>
    request<CalibrationSettings>("/settings/model-calibration", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  getWorkspaces: (): Promise<WorkspaceSummary[]> => request<WorkspaceSummary[]>("/workspaces"),

  createWorkspace: (name: string): Promise<Workspace> =>
    request<Workspace>("/workspaces", { method: "POST", body: JSON.stringify({ name }) }),

  getCurrentWorkspace: (): Promise<Workspace> => request<Workspace>("/workspaces/current"),

  updateCurrentWorkspace: (payload: WorkspaceUpdatePayload): Promise<Workspace> =>
    request<Workspace>("/workspaces/current", { method: "PATCH", body: JSON.stringify(payload) }),

  archiveCurrentWorkspace: (): Promise<Workspace> =>
    request<Workspace>("/workspaces/current/archive", { method: "POST" }),

  getMembers: (): Promise<Member[]> => request<Member[]>("/workspaces/current/members"),

  addMember: (payload: MemberAddPayload): Promise<Member> =>
    request<Member>("/workspaces/current/members", { method: "POST", body: JSON.stringify(payload) }),

  updateMemberRole: (membershipId: string, role: Role): Promise<Member> =>
    request<Member>(`/workspaces/current/members/${membershipId}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),

  removeMember: (membershipId: string): Promise<void> =>
    request<void>(`/workspaces/current/members/${membershipId}`, { method: "DELETE" }),

  switchWorkspace: (workspaceId: string): Promise<User> =>
    request<User>("/auth/switch-workspace", {
      method: "POST",
      body: JSON.stringify({ workspace_id: workspaceId }),
    }),

  getSlackStatus: (): Promise<SlackStatus> => request<SlackStatus>("/settings/integrations/slack"),

  connectSlack: (webhookUrl: string): Promise<SlackStatus> =>
    request<SlackStatus>("/settings/integrations/slack", {
      method: "PUT",
      body: JSON.stringify({ webhook_url: webhookUrl }),
    }),

  disconnectSlack: (): Promise<void> =>
    request<void>("/settings/integrations/slack", { method: "DELETE" }),

  getJiraStatus: (): Promise<JiraStatus> => request<JiraStatus>("/settings/integrations/jira"),

  connectJira: (payload: JiraConnectPayload): Promise<JiraStatus> =>
    request<JiraStatus>("/settings/integrations/jira", { method: "PUT", body: JSON.stringify(payload) }),

  disconnectJira: (): Promise<void> =>
    request<void>("/settings/integrations/jira", { method: "DELETE" }),

  getApiKeys: (): Promise<ApiKey[]> => request<ApiKey[]>("/settings/integrations/api-keys"),

  createApiKey: (name: string): Promise<ApiKeyCreated> =>
    request<ApiKeyCreated>("/settings/integrations/api-keys", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  revokeApiKey: (id: string): Promise<void> =>
    request<void>(`/settings/integrations/api-keys/${id}`, { method: "DELETE" }),

  getWebhooks: (): Promise<Webhook[]> => request<Webhook[]>("/settings/integrations/webhooks"),

  createWebhook: (url: string, eventTypes: string[]): Promise<WebhookCreated> =>
    request<WebhookCreated>("/settings/integrations/webhooks", {
      method: "POST",
      body: JSON.stringify({ url, event_types: eventTypes }),
    }),

  updateWebhook: (id: string, payload: WebhookUpdatePayload): Promise<Webhook> =>
    request<Webhook>(`/settings/integrations/webhooks/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  deleteWebhook: (id: string): Promise<void> =>
    request<void>(`/settings/integrations/webhooks/${id}`, { method: "DELETE" }),

  getWebhookDeliveries: (id: string): Promise<WebhookDelivery[]> =>
    request<WebhookDelivery[]>(`/settings/integrations/webhooks/${id}/deliveries`),

  testWebhook: (id: string): Promise<WebhookTestResult> =>
    request<WebhookTestResult>(`/settings/integrations/webhooks/${id}/test`, { method: "POST" }),
};
