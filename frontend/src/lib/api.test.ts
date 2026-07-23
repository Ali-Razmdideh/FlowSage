import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";

function mockFetchOnce(response: Partial<Response> & { json?: () => Promise<unknown> }): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({}),
      ...response,
    } as Response),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("sends credentials and parses a successful JSON response", async () => {
    mockFetchOnce({ json: async () => ({ id: "u1", email: "a@b.com", created_at: "now" }) });

    const user = await api.me();

    expect(user).toEqual({ id: "u1", email: "a@b.com", created_at: "now" });
    const [, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(init?.credentials).toBe("include");
  });

  it("throws ApiError with the backend's detail message on failure", async () => {
    mockFetchOnce({
      ok: false,
      status: 401,
      statusText: "Unauthorized",
      json: async () => ({ detail: "Invalid email or password" }),
    });

    await expect(api.login("a@b.com", "wrong")).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      message: "Invalid email or password",
    });
  });

  it("falls back to statusText when the error body isn't JSON", async () => {
    mockFetchOnce({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      json: async () => {
        throw new SyntaxError("not json");
      },
    });

    await expect(api.me()).rejects.toBeInstanceOf(ApiError);
    await expect(api.me()).rejects.toMatchObject({ message: "Internal Server Error" });
  });

  it("does not set Content-Type for FormData bodies (browser sets the boundary)", async () => {
    mockFetchOnce({ json: async () => ({ id: "r1", status: "queued" }) });

    await api.createSimulation({
      personaId: "p1",
      goal: "goal",
      flowName: "flow",
      files: [new File(["x"], "a.png", { type: "image/png" })],
    });

    const [, init] = vi.mocked(fetch).mock.calls[0]!;
    const headers = init?.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBeUndefined();
    expect(init?.body).toBeInstanceOf(FormData);
  });

  it("builds the funnel query string only from provided filters", async () => {
    mockFetchOnce({ json: async () => ({ funnel: [], friction_nodes: [], total_sessions: 0, total_events: 0 }) });

    await api.getFunnel({ cohort: "paid_users" });

    const [url] = vi.mocked(fetch).mock.calls[0]!;
    expect(url).toContain("/graph/funnel?cohort=paid_users");
  });

  it("getWorkspaces requests the workspaces endpoint", async () => {
    mockFetchOnce({
      json: async () => [
        { id: "w1", name: "Workspace 1", role: "admin" },
        { id: "w2", name: "Workspace 2", role: "researcher" },
      ],
    });

    const workspaces = await api.getWorkspaces();

    expect(workspaces).toHaveLength(2);
    expect(workspaces[0]).toEqual({ id: "w1", name: "Workspace 1", role: "admin" });
    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(url).toContain("/workspaces");
    expect(init?.method).toBeUndefined(); // GET
  });

  it("createWorkspace sends POST with workspace name", async () => {
    mockFetchOnce({
      json: async () => ({
        id: "w1",
        name: "New Workspace",
        slug: "new-workspace",
        description: "Test workspace",
        avatar_url: null,
        privacy: "private",
        region: "us-east-1",
        retention_days: 30,
        archived: false,
        created_at: "2026-01-01T00:00:00Z",
      }),
    });

    const workspace = await api.createWorkspace("New Workspace");

    expect(workspace.id).toBe("w1");
    expect(workspace.name).toBe("New Workspace");
    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(url).toContain("/workspaces");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ name: "New Workspace" }));
  });

  it("getCurrentWorkspace requests the current workspace endpoint", async () => {
    mockFetchOnce({
      json: async () => ({
        id: "w1",
        name: "Current Workspace",
        slug: "current-workspace",
        description: "The current workspace",
        avatar_url: null,
        privacy: "private",
        region: "us-east-1",
        retention_days: 30,
        archived: false,
        created_at: "2026-01-01T00:00:00Z",
      }),
    });

    const workspace = await api.getCurrentWorkspace();

    expect(workspace.name).toBe("Current Workspace");
    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(url).toContain("/workspaces/current");
    expect(init?.method).toBeUndefined(); // GET
  });

  it("updateCurrentWorkspace sends PATCH with workspace update payload", async () => {
    mockFetchOnce({
      json: async () => ({
        id: "w1",
        name: "Updated Workspace",
        slug: "updated-workspace",
        description: "Updated description",
        avatar_url: null,
        privacy: "restricted",
        region: "eu-west-1",
        retention_days: 60,
        archived: false,
        created_at: "2026-01-01T00:00:00Z",
      }),
    });

    const payload = {
      name: "Updated Workspace",
      description: "Updated description",
      avatar_url: null,
      privacy: "restricted" as const,
      region: "eu-west-1",
      retention_days: 60,
    };
    const workspace = await api.updateCurrentWorkspace(payload);

    expect(workspace.name).toBe("Updated Workspace");
    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(url).toContain("/workspaces/current");
    expect(init?.method).toBe("PATCH");
    expect(init?.body).toBe(JSON.stringify(payload));
  });

  it("archiveCurrentWorkspace sends POST to the archive endpoint", async () => {
    mockFetchOnce({
      json: async () => ({
        id: "w1",
        name: "Archived Workspace",
        slug: "archived-workspace",
        description: "This workspace is archived",
        avatar_url: null,
        privacy: "private",
        region: "us-east-1",
        retention_days: 30,
        archived: true,
        created_at: "2026-01-01T00:00:00Z",
      }),
    });

    const workspace = await api.archiveCurrentWorkspace();

    expect(workspace.archived).toBe(true);
    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(url).toContain("/workspaces/current/archive");
    expect(init?.method).toBe("POST");
  });

  it("getMembers requests the members endpoint", async () => {
    mockFetchOnce({
      json: async () => [
        { id: "m1", user_id: "u1", email: "user1@example.com", role: "admin", created_at: "2026-01-01T00:00:00Z" },
        { id: "m2", user_id: "u2", email: "user2@example.com", role: "researcher", created_at: "2026-01-01T00:00:00Z" },
      ],
    });

    const members = await api.getMembers();

    expect(members).toHaveLength(2);
    expect(members[0]?.email).toBe("user1@example.com");
    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(url).toContain("/workspaces/current/members");
    expect(init?.method).toBeUndefined(); // GET
  });

  it("addMember sends POST with member payload", async () => {
    mockFetchOnce({
      json: async () => ({
        id: "m3",
        user_id: "u3",
        email: "newuser@example.com",
        role: "viewer",
        created_at: "2026-01-01T00:00:00Z",
      }),
    });

    const payload = { email: "newuser@example.com", role: "viewer" as const };
    const member = await api.addMember(payload);

    expect(member.email).toBe("newuser@example.com");
    expect(member.role).toBe("viewer");
    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(url).toContain("/workspaces/current/members");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify(payload));
  });

  it("updateMemberRole sends PATCH with role to member endpoint", async () => {
    mockFetchOnce({
      json: async () => ({
        id: "m1",
        user_id: "u1",
        email: "user1@example.com",
        role: "admin",
        created_at: "2026-01-01T00:00:00Z",
      }),
    });

    const member = await api.updateMemberRole("m1", "admin");

    expect(member.role).toBe("admin");
    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(url).toContain("/workspaces/current/members/m1");
    expect(init?.method).toBe("PATCH");
    expect(init?.body).toBe(JSON.stringify({ role: "admin" }));
  });

  it("removeMember sends DELETE to member endpoint", async () => {
    mockFetchOnce({ json: async () => undefined });

    await api.removeMember("m1");

    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(url).toContain("/workspaces/current/members/m1");
    expect(init?.method).toBe("DELETE");
  });

  it("switchWorkspace sends POST with workspace_id to auth endpoint", async () => {
    mockFetchOnce({
      json: async () => ({
        id: "u1",
        email: "user@example.com",
        created_at: "2026-01-01T00:00:00Z",
        workspace_id: "w2",
        role: "admin",
        workspaces: [
          { id: "w1", name: "Workspace 1" },
          { id: "w2", name: "Workspace 2" },
        ],
      }),
    });

    const user = await api.switchWorkspace("w2");

    expect(user.workspace_id).toBe("w2");
    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(url).toContain("/auth/switch-workspace");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ workspace_id: "w2" }));
  });
});
