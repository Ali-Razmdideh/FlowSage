import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { AuthContext, type AuthState } from "./auth/AuthContext";

function renderAppAt(path: string, user: AuthState["user"]) {
  const state: AuthState = {
    user,
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    switchWorkspace: vi.fn(),
  };
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthContext.Provider value={state}>
        <App />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

describe("HomeRoute", () => {
  it("shows the landing page at / when logged out", () => {
    renderAppAt("/", null);
    expect(screen.getAllByRole("link", { name: /log in/i }).length).toBeGreaterThan(0);
  });

  it("redirects authenticated users away from / to the dashboard", () => {
    renderAppAt("/", {
      id: "u1",
      email: "admin@flowsage.dev",
      created_at: "now",
      workspace_id: "w1",
      role: "admin",
      workspaces: [{ id: "w1", name: "Workspace 1" }],
    });
    expect(screen.queryByRole("link", { name: /log in/i })).not.toBeInTheDocument();
  });
});
