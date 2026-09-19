import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AuthContext, type AuthState } from "../../auth/AuthContext";
import { api } from "../../lib/api";
import { PredictiveEnginePage } from "./PredictiveEnginePage";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return { ...actual, api: { ...actual.api, listPersonas: vi.fn() } };
});

const viewer: AuthState = {
  user: {
    id: "viewer-1",
    email: "viewer@example.com",
    created_at: "2026-01-01T00:00:00Z",
    workspace_id: "workspace-1",
    role: "viewer",
    workspaces: [{ id: "workspace-1", name: "Research" }],
  },
  loading: false,
  login: vi.fn(),
  logout: vi.fn(),
  switchWorkspace: vi.fn(),
};

describe("PredictiveEnginePage", () => {
  it("explains simulation access to viewers without rendering mutating controls", async () => {
    vi.mocked(api.listPersonas).mockResolvedValue([]);

    render(
      <MemoryRouter>
        <AuthContext.Provider value={viewer}>
          <PredictiveEnginePage />
        </AuthContext.Provider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Researcher access is required to create personas and run simulations.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /new persona/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run Simulation" })).not.toBeInTheDocument();
  });
});
