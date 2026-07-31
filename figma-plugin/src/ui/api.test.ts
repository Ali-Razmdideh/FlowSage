// figma-plugin/src/ui/api.test.ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { createSimulationRun, getSimulationRun, listPersonas } from "./api";

const config = { baseUrl: "https://flowsage.example", apiKey: "fs_test_key" };

describe("listPersonas", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("GETs /personas with the X-API-Key header", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ id: "p1", slug: "novice", name: "Novice" }],
    });
    vi.stubGlobal("fetch", fetchMock);

    const personas = await listPersonas(config);

    expect(fetchMock).toHaveBeenCalledWith(
      "https://flowsage.example/personas",
      expect.objectContaining({ headers: { "X-API-Key": "fs_test_key" } }),
    );
    expect(personas).toEqual([{ id: "p1", slug: "novice", name: "Novice" }]);
  });

  it("throws with the response status on a non-ok response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401 }));
    await expect(listPersonas(config)).rejects.toThrow("401");
  });
});

describe("createSimulationRun", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("POSTs multipart form data with persona_id, goal, flow_name, and files", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: "run-1", status: "queued" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const run = await createSimulationRun(config, {
      personaId: "p1",
      goal: "Buy a widget",
      flowName: "Checkout",
      files: [{ filename: "001.png", bytes: new Uint8Array([1, 2, 3]) }],
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://flowsage.example/simulations");
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("fs_test_key");
    expect(init.body).toBeInstanceOf(FormData);
    expect(run.id).toBe("run-1");
  });
});

describe("getSimulationRun", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("GETs /simulations/{id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: "run-1", status: "completed", issues: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const run = await getSimulationRun(config, "run-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "https://flowsage.example/simulations/run-1",
      expect.objectContaining({ headers: { "X-API-Key": "fs_test_key" } }),
    );
    expect(run.status).toBe("completed");
  });
});
