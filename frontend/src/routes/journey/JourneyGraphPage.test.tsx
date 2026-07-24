import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { FunnelReport, NodeIntelligence } from "../../lib/types";
import { JourneyGraphPage } from "./JourneyGraphPage";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getFunnel: vi.fn(),
      getChurnRisk: vi.fn().mockResolvedValue([]),
      getCohortComparison: vi.fn().mockResolvedValue({ cohorts: [], screens: [] }),
      getNodeIntelligence: vi.fn(),
      exportNodeToSlack: vi.fn(),
      exportNodeToJira: vi.fn(),
      importSampleData: vi.fn(),
    },
  };
});

describe("JourneyGraphPage", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

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

  it("opens the Node Intelligence aside when a friction node is clicked", async () => {
    const report: FunnelReport = {
      funnel: [
        {
          screen: "Checkout_Final_Payment",
          sessions_entered: 10,
          sessions_continued: 2,
          drop_off_rate: 0.8,
        },
      ],
      friction_nodes: [
        {
          screen: "Checkout_Final_Payment",
          kind: "abnormal_drop_off",
          detail: "80% of sessions dropped off.",
          sessions_affected: 8,
        },
      ],
      total_sessions: 10,
      total_events: 20,
    };
    const intel: NodeIntelligence = {
      screen: "Checkout_Final_Payment",
      drop_off_rate: 0.8,
      avg_seconds_on_node: 14,
      friction_nodes: report.friction_nodes,
      ai_insight: "80% of sessions abandon Checkout_Final_Payment without continuing.",
      recommendations: [
        {
          rank: 1,
          title: "Simplify the Checkout_Final_Payment step",
          description: "Reduce required fields.",
          expected_lift_pct: 14,
        },
      ],
    };
    vi.mocked(api.getFunnel).mockResolvedValue(report);
    vi.mocked(api.getNodeIntelligence).mockResolvedValue(intel);

    render(<JourneyGraphPage />);

    fireEvent.click(
      await screen.findByRole("button", { name: /Checkout_Final_Payment/ }),
    );

    expect(await screen.findByText("Node Intelligence")).toBeInTheDocument();
    expect(await screen.findByText(/80% of sessions abandon/)).toBeInTheDocument();
    expect(screen.getByText(/Simplify the Checkout_Final_Payment step/)).toBeInTheDocument();
  });

  it("exports the open node to Slack and shows a success message", async () => {
    vi.mocked(api.getFunnel).mockResolvedValue({
      funnel: [{ screen: "checkout", sessions_entered: 10, sessions_continued: 5, drop_off_rate: 0.5 }],
      friction_nodes: [
        { screen: "checkout", kind: "abnormal_drop_off", detail: "High drop-off.", sessions_affected: 5 },
      ],
      total_sessions: 10,
      total_events: 20,
    });
    vi.mocked(api.getNodeIntelligence).mockResolvedValue({
      screen: "checkout",
      drop_off_rate: 0.5,
      avg_seconds_on_node: 30,
      friction_nodes: [],
      ai_insight: "High drop-off at checkout.",
      recommendations: [],
    });
    vi.mocked(api.exportNodeToSlack).mockResolvedValue({ status: "sent" });

    render(<JourneyGraphPage />);

    fireEvent.click(await screen.findByRole("button", { name: /checkout/ }));
    await screen.findByText("High drop-off at checkout.");

    const slackButton = screen.getByRole("button", { name: "Export to Slack" });
    fireEvent.click(slackButton);

    expect(await screen.findByText(/Exported to Slack/)).toBeInTheDocument();
    expect(api.exportNodeToSlack).toHaveBeenCalledWith("checkout");
  });

  it("shows an Import Sample Data button in the empty state and refetches the funnel on success", async () => {
    vi.mocked(api.getFunnel).mockResolvedValue({
      funnel: [],
      friction_nodes: [],
      total_sessions: 0,
      total_events: 0,
    });
    vi.mocked(api.importSampleData).mockResolvedValue({ events_ingested: 44, run_id: "run-1" });

    render(<JourneyGraphPage />);

    const importButton = await screen.findByRole("button", { name: /import sample data/i });
    fireEvent.click(importButton);

    await waitFor(() => expect(vi.mocked(api.getFunnel)).toHaveBeenCalledTimes(2));
  });
});
