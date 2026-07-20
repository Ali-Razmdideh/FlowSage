import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AuthContext, type AuthState } from "../auth/AuthContext";
import { LoginPage } from "./LoginPage";

function renderWithAuth(overrides: Partial<AuthState> = {}) {
  const state: AuthState = {
    user: null,
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    switchWorkspace: vi.fn(),
    ...overrides,
  };
  render(
    <MemoryRouter initialEntries={["/login"]}>
      <AuthContext.Provider value={state}>
        <LoginPage />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
  return state;
}

describe("LoginPage", () => {
  it("calls login with the entered credentials on submit", async () => {
    const user = userEvent.setup();
    const state = renderWithAuth();

    await user.type(screen.getByLabelText("Email"), "admin@flowsage.dev");
    await user.type(screen.getByLabelText("Password"), "supersecret123");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(state.login).toHaveBeenCalledWith("admin@flowsage.dev", "supersecret123");
    });
  });

  it("shows the error message when login rejects", async () => {
    const user = userEvent.setup();
    renderWithAuth({ login: vi.fn().mockRejectedValue(new Error("Invalid email or password")) });

    await user.type(screen.getByLabelText("Email"), "admin@flowsage.dev");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong.");
  });

  it("redirects away from the login form when already authenticated", () => {
    renderWithAuth({
      user: {
        id: "u1",
        email: "admin@flowsage.dev",
        created_at: "now",
        workspace_id: "w1",
        role: "admin",
        workspaces: [{ id: "w1", name: "Workspace 1" }],
      },
    });

    expect(screen.queryByRole("button", { name: /sign in/i })).not.toBeInTheDocument();
  });
});
