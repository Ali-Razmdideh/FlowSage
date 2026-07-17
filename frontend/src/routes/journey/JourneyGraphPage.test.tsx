import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { FunnelReport } from "../../lib/types";
import { JourneyGraphPage } from "./JourneyGraphPage";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return { ...actual, api: { ...actual.api, getFunnel: vi.fn() } };
});

describe("JourneyGraphPage", () => {
  it("shows the empty state when there is no funnel yet", async () => {
    vi.mocked(api.getFunnel).mockResolvedValue({
      funnel: [],
      friction_nodes: [],
      total_sessions: 0,
      total_events: 0,
    });

    render(<JourneyGraphPage />);

    expect(await screen.findByText("Awaiting Event Ingestion")).toBeInTheDocument();
  });

  it("renders the discovered funnel and friction nodes", async () => {
    const report: FunnelReport = {
      funnel: [
        { screen: "Landing_Main", sessions_entered: 10, sessions_continued: 8, drop_off_rate: 0.2 },
      ],
      friction_nodes: [
        {
          screen: "Checkout_Final_Payment",
          kind: "rage_loop",
          detail: "1 session(s) repeated 3+ actions.",
          sessions_affected: 1,
        },
      ],
      total_sessions: 10,
      total_events: 20,
    };
    vi.mocked(api.getFunnel).mockResolvedValue(report);

    render(<JourneyGraphPage />);

    expect(await screen.findByText("Landing_Main")).toBeInTheDocument();
    expect(screen.getByText(/20% drop-off/)).toBeInTheDocument();
    expect(screen.getByText("Rage loop")).toBeInTheDocument();
  });
});
