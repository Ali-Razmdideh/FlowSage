import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AuthContext, type AuthState } from "../auth/AuthContext";
import { Sidebar } from "./Sidebar";

function renderSidebar(role: "admin" | "researcher" | "viewer" = "researcher") {
  const auth: AuthState = {
    user: {
      id: "user-1",
      email: "researcher@example.com",
      created_at: "2026-01-01T00:00:00Z",
      workspace_id: "workspace-1",
      role,
      workspaces: [{ id: "workspace-1", name: "Research" }],
    },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    switchWorkspace: vi.fn(),
  };
  render(
    <MemoryRouter>
      <AuthContext.Provider value={auth}>
        <Sidebar />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

describe("Sidebar", () => {
  it("opens a mobile navigation menu with the primary destinations", () => {
    renderSidebar();

    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));

    const menu = screen.getByRole("navigation", { name: "Mobile navigation" });
    expect(menu).toHaveTextContent("Dashboard");
    expect(menu).toHaveTextContent("Journey Graph");
    expect(menu).toHaveTextContent("New Simulation");
  });

  it("does not offer viewers a new simulation action", () => {
    renderSidebar("viewer");

    expect(screen.queryByRole("link", { name: "New Simulation" })).not.toBeInTheDocument();
    expect(screen.getByText("Researcher access required to run simulations.")).toBeInTheDocument();
  });
});
