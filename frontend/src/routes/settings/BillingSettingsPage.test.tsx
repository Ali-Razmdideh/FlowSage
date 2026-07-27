import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { UsageSnapshot } from "../../lib/types";
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

describe("BillingSettingsPage", () => {
  it("renders the current plan and usage", async () => {
    vi.mocked(api.getBillingUsage).mockResolvedValue(FREE_USAGE);

    render(<BillingSettingsPage />);

    expect(await screen.findByText(/Free/i)).toBeInTheDocument();
    expect(screen.getByText(/250/)).toBeInTheDocument();
    expect(screen.getByText(/1,000|1000/)).toBeInTheDocument();
  });

  it("redirects to Stripe Checkout on Upgrade to Pro", async () => {
    vi.mocked(api.getBillingUsage).mockResolvedValue(FREE_USAGE);
    vi.mocked(api.startCheckout).mockResolvedValue({ url: "https://checkout.stripe.com/pay/cs_test_123" });
    const assignMock = vi.fn();
    Object.defineProperty(window, "location", { value: { assign: assignMock }, writable: true });

    render(<BillingSettingsPage />);
    await screen.findByText(/Free/i);
    fireEvent.click(screen.getByRole("button", { name: /Upgrade to Pro/i }));

    await waitFor(() => {
      expect(api.startCheckout).toHaveBeenCalledWith("pro");
      expect(assignMock).toHaveBeenCalledWith("https://checkout.stripe.com/pay/cs_test_123");
    });
  });
});
