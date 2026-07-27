import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AuthContext, type AuthState } from "../../auth/AuthContext";
import { api } from "../../lib/api";
import type { Role, UsageSnapshot, User } from "../../lib/types";
import { BillingSettingsPage } from "./BillingSettingsPage";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getBillingUsage: vi.fn(),
      startCheckout: vi.fn(),
      openBillingPortal: vi.fn(),
    },
  };
});

const FREE_USAGE: UsageSnapshot = {
  tier: "free",
  events_used: 250,
  events_limit: 1000,
  runs_used: 2,
  runs_limit: 5,
  seats_used: 1,
  seats_limit: 1,
};

/** The page now reads the current user's workspace role from `useAuth()` to
 * decide whether to show the (admin-only) checkout/portal controls, so every
 * render needs a real AuthContext around it. */
function renderAs(role: Role) {
  const user: User = {
    id: "user-1",
    email: "someone@example.com",
    created_at: "2024-01-01T00:00:00Z",
    workspace_id: "workspace-1",
    role,
    workspaces: [{ id: "workspace-1", name: "Default" }],
  };
  const auth: AuthState = {
    user,
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    switchWorkspace: vi.fn(),
  };
  return render(
    <AuthContext.Provider value={auth}>
      <BillingSettingsPage />
    </AuthContext.Provider>,
  );
}

describe("BillingSettingsPage", () => {
  it("renders the current plan and usage", async () => {
    vi.mocked(api.getBillingUsage).mockResolvedValue(FREE_USAGE);

    renderAs("admin");

    expect(await screen.findByText(/Free/i)).toBeInTheDocument();
    expect(screen.getByText(/250/)).toBeInTheDocument();
    expect(screen.getByText(/1,000|1000/)).toBeInTheDocument();
  });

  it("redirects to Stripe Checkout on Upgrade to Pro", async () => {
    vi.mocked(api.getBillingUsage).mockResolvedValue(FREE_USAGE);
    vi.mocked(api.startCheckout).mockResolvedValue({ url: "https://checkout.stripe.com/pay/cs_test_123" });
    const assignMock = vi.fn();
    Object.defineProperty(window, "location", { value: { assign: assignMock }, writable: true });

    renderAs("admin");
    await screen.findByText(/Free/i);
    fireEvent.click(screen.getByRole("button", { name: /Upgrade to Pro/i }));

    await waitFor(() => {
      expect(api.startCheckout).toHaveBeenCalledWith("pro");
      expect(assignMock).toHaveBeenCalledWith("https://checkout.stripe.com/pay/cs_test_123");
    });
  });

  it("hides the upgrade and manage-billing buttons for non-admins", async () => {
    vi.mocked(api.getBillingUsage).mockResolvedValue(FREE_USAGE);

    renderAs("viewer");

    // Usage stays visible to every role -- only the mutating controls go away.
    expect(await screen.findByText(/Usage This Month/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Upgrade to Pro/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Upgrade to Team/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Manage Billing/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Only workspace admins/i)).toBeInTheDocument();
  });
});
