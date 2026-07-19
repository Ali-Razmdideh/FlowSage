import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { SimulationRunDetail } from "../../lib/types";
import { RunningSimulationPage } from "./RunningSimulationPage";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getSimulation: vi.fn(),
      simulationStreamUrl: vi.fn().mockReturnValue("http://test/stream"),
      exportIssueToSlack: vi.fn(),
      exportIssueToJira: vi.fn(),
    },
  };
});

class MockEventSource {
  static instances: MockEventSource[] = [];
  onerror: (() => void) | null = null;
  listeners: Record<string, ((event: MessageEvent<string>) => void)[]> = {};
  constructor() {
    MockEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    (this.listeners[type] ??= []).push(listener);
  }
  close() {}
}
vi.stubGlobal("EventSource", MockEventSource);

const RUN: SimulationRunDetail = {
  id: "run-1",
  flow_name: "Checkout",
  goal: "Buy a widget",
  persona_id: "persona-1",
  status: "completed",
  error: null,
  steps: [],
  issues: [
    {
      id: "issue-1",
      screen: "checkout",
      severity: "high",
      title: "Confusing CTA",
      heuristic_violated: "Visibility of system status",
      persona_impact: "Anxious users abandon.",
      description: "The primary button is unlabeled.",
      suggested_fix: "Add a clear label.",
    },
  ],
};

describe("RunningSimulationPage export buttons", () => {
  it("exports a friction issue to Slack and shows a success message", async () => {
    vi.mocked(api.getSimulation).mockResolvedValue(RUN);
    vi.mocked(api.exportIssueToSlack).mockResolvedValue({ status: "sent" });

    render(
      <MemoryRouter initialEntries={["/predictive/runs/run-1"]}>
        <Routes>
          <Route path="/predictive/runs/:runId" element={<RunningSimulationPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const slackButton = await screen.findByRole("button", { name: "Export to Slack" });
    fireEvent.click(slackButton);

    expect(await screen.findByText(/Exported to Slack/)).toBeInTheDocument();
    expect(api.exportIssueToSlack).toHaveBeenCalledWith("issue-1");
  });

  it("exports a friction issue to Jira and shows the created issue key", async () => {
    vi.mocked(api.getSimulation).mockResolvedValue(RUN);
    vi.mocked(api.exportIssueToJira).mockResolvedValue({ issue_key: "FLOW-42" });

    render(
      <MemoryRouter initialEntries={["/predictive/runs/run-1"]}>
        <Routes>
          <Route path="/predictive/runs/:runId" element={<RunningSimulationPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const jiraButton = await screen.findByRole("button", { name: "Export to Jira" });
    fireEvent.click(jiraButton);

    expect(await screen.findByText(/FLOW-42/)).toBeInTheDocument();
  });
});
