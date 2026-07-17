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
});
