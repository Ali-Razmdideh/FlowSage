import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import { DashboardPage } from "./DashboardPage";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listPersonas: vi.fn().mockResolvedValue([]),
      getFunnel: vi.fn().mockResolvedValue({
        funnel: [],
        friction_nodes: [],
        total_sessions: 0,
        total_events: 0,
      }),
      getAlerts: vi.fn(),
    },
  };
});

describe("DashboardPage alerts banner", () => {
  it("shows nothing when there are no alerts", async () => {
    vi.mocked(api.getAlerts).mockResolvedValue({ calibration_alerts: [], churn_alerts: [] });

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    await screen.findByText("Executive Summary");
    expect(screen.queryByText("Alerts")).not.toBeInTheDocument();
  });

  it("shows a banner with calibration and churn alerts", async () => {
    vi.mocked(api.getAlerts).mockResolvedValue({
      calibration_alerts: [{ persona_name: "Nora", screen: "checkout", delta: 0.7 }],
      churn_alerts: [{ cohort: "at_risk", risk_score: 0.72, top_reason: "High drop-off" }],
    });

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Alerts")).toBeInTheDocument();
    expect(screen.getByText(/Nora on checkout/)).toBeInTheDocument();
    expect(screen.getByText(/at_risk at 72%/)).toBeInTheDocument();
  });
});
