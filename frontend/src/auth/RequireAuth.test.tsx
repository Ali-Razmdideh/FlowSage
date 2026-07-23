import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AuthContext, type AuthState } from "./AuthContext";
import { RequireAuth } from "./RequireAuth";

function renderWithAuth(state: AuthState) {
  return render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <AuthContext.Provider value={state}>
        <Routes>
          <Route path="/login" element={<p>Login screen</p>} />
          <Route
            path="/dashboard"
            element={
              <RequireAuth>
                <p>Protected content</p>
              </RequireAuth>
            }
          />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

describe("RequireAuth", () => {
  it("shows a loading state while the session check is in flight", () => {
    renderWithAuth({
      user: null,
      loading: true,
      login: vi.fn(),
      logout: vi.fn(),
      switchWorkspace: vi.fn(),
    });
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("redirects to /login when there is no authenticated user", () => {
    renderWithAuth({
      user: null,
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
      switchWorkspace: vi.fn(),
    });
    expect(screen.getByText("Login screen")).toBeInTheDocument();
  });

  it("renders the protected content when authenticated", () => {
    renderWithAuth({
      user: {
        id: "u1",
        email: "a@b.com",
        created_at: "now",
        workspace_id: "w1",
        role: "admin",
        workspaces: [{ id: "w1", name: "Workspace 1" }],
      },
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
      switchWorkspace: vi.fn(),
    });
    expect(screen.getByText("Protected content")).toBeInTheDocument();
  });
});
