import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../../lib/api";
import type { Persona, ScheduledSimulation, TrendPoint } from "../../lib/types";
import { ScheduledSimulationsPage } from "./ScheduledSimulationsPage";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listPersonas: vi.fn(),
      listScheduledSimulations: vi.fn(),
      createScheduledSimulation: vi.fn(),
      updateScheduledSimulation: vi.fn(),
      deleteScheduledSimulation: vi.fn(),
      pushScheduledSimulationScreenshots: vi.fn(),
      getScheduledSimulationTrend: vi.fn(),
    },
  };
});

const PERSONA: Persona = {
  id: "persona-1",
  slug: "novice-user",
  name: "Novice User",
  description: "Represents users with limited domain knowledge.",
  baseline: true,
  tech_affinity: "Low",
  primary_device: "Mobile / Tablet",
  discovery_mode: "Search-driven",
  contextual_triggers: [],
  technical_literacy: 0.2,
  anxiety: 0.85,
  patience: 0.3,
  curiosity: 0.4,
};

const CONFIG: ScheduledSimulation = {
  id: "sched-1",
  flow_name: "Checkout",
  goal: "Ship checkout",
  persona_id: "persona-1",
  interval: "daily",
  active: true,
  has_pending_screenshots: false,
  last_fired_at: null,
  created_at: "2026-08-01T00:00:00Z",
};

const TREND: TrendPoint[] = [
  { run_id: "run-1", created_at: "2026-07-30T00:00:00Z", score: 0.4, issue_count: 1 },
  { run_id: "run-2", created_at: "2026-07-31T00:00:00Z", score: 0.7, issue_count: 3 },
];

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/predictive/scheduled"]}>
      <Routes>
        <Route path="/predictive/scheduled" element={<ScheduledSimulationsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ScheduledSimulationsPage", () => {
  it("lists existing schedules with their trend", async () => {
    vi.mocked(api.listPersonas).mockResolvedValue([PERSONA]);
    vi.mocked(api.listScheduledSimulations).mockResolvedValue([CONFIG]);
    vi.mocked(api.getScheduledSimulationTrend).mockResolvedValue(TREND);

    renderPage();

    expect(await screen.findByText("Checkout")).toBeInTheDocument();
    // Scoped to the <p> tag: the "New Schedule" form also has a persona <select>
    // (option "Novice User") and an interval <select> (option "Daily"), so
    // unscoped text matches would find more than one element.
    expect(screen.getByText(/Novice User/, { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText(/Daily/, { selector: "p" })).toBeInTheDocument();

    const chart = screen.getByRole("img", { name: /Friction score trend/i });
    expect(within(chart).getByText("0%")).toBeInTheDocument();
    expect(within(chart).getByText("50%")).toBeInTheDocument();
    expect(within(chart).getByText("100%")).toBeInTheDocument();
  });

  it("creates a new schedule from the form", async () => {
    vi.mocked(api.listPersonas).mockResolvedValue([PERSONA]);
    vi.mocked(api.listScheduledSimulations).mockResolvedValue([]);
    vi.mocked(api.createScheduledSimulation).mockResolvedValue(CONFIG);

    renderPage();
    await waitFor(() => expect(api.listPersonas).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText(/Flow name/i), { target: { value: "Checkout" } });
    fireEvent.click(screen.getByRole("button", { name: /Create Schedule/i }));

    await waitFor(() =>
      expect(api.createScheduledSimulation).toHaveBeenCalledWith(
        expect.objectContaining({ flow_name: "Checkout", persona_id: "persona-1" }),
      ),
    );
    expect(await screen.findByText("Checkout")).toBeInTheDocument();
  });

  it("edits an existing schedule's goal and interval", async () => {
    vi.mocked(api.listPersonas).mockResolvedValue([PERSONA]);
    vi.mocked(api.listScheduledSimulations).mockResolvedValue([CONFIG]);
    vi.mocked(api.getScheduledSimulationTrend).mockResolvedValue([]);
    vi.mocked(api.updateScheduledSimulation).mockResolvedValue({
      ...CONFIG,
      goal: "Ship checkout faster",
      interval: "weekly",
    });

    renderPage();
    await screen.findByText("Checkout");

    fireEvent.click(screen.getByRole("button", { name: /Edit/i }));
    fireEvent.change(screen.getByDisplayValue("Ship checkout"), {
      target: { value: "Ship checkout faster" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save/i }));

    await waitFor(() =>
      expect(api.updateScheduledSimulation).toHaveBeenCalledWith("sched-1", {
        goal: "Ship checkout faster",
        interval: "daily",
      }),
    );
  });

  it("keeps the card in edit mode with the draft intact and shows a per-card error when saving fails", async () => {
    vi.mocked(api.listPersonas).mockResolvedValue([PERSONA]);
    vi.mocked(api.listScheduledSimulations).mockResolvedValue([CONFIG]);
    vi.mocked(api.getScheduledSimulationTrend).mockResolvedValue([]);
    vi.mocked(api.updateScheduledSimulation).mockRejectedValue(
      new ApiError(500, "Failed to update scheduled simulation."),
    );

    renderPage();
    await screen.findByText("Checkout");

    fireEvent.click(screen.getByRole("button", { name: /Edit/i }));
    fireEvent.change(screen.getByDisplayValue("Ship checkout"), {
      target: { value: "Ship checkout faster" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save/i }));

    await waitFor(() => expect(api.updateScheduledSimulation).toHaveBeenCalled());

    // Still in edit mode, draft intact, and a visible per-card error — not
    // silently reverted to the stale config with no feedback.
    expect(await screen.findByText(/Failed to save changes/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue("Ship checkout faster")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save/i })).toBeInTheDocument();
  });

  it("does not hang on 'Loading…' when the initial fetch fails", async () => {
    vi.mocked(api.listPersonas).mockResolvedValue([]);
    vi.mocked(api.listScheduledSimulations).mockRejectedValue(
      new ApiError(500, "Failed to load scheduled simulations."),
    );

    renderPage();

    expect(await screen.findByText(/Failed to load scheduled simulations/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Loading…")).not.toBeInTheDocument());
    expect(screen.getByText("No scheduled runs yet.")).toBeInTheDocument();
  });

  it("deletes a schedule", async () => {
    vi.mocked(api.listPersonas).mockResolvedValue([PERSONA]);
    vi.mocked(api.listScheduledSimulations).mockResolvedValue([CONFIG]);
    vi.mocked(api.getScheduledSimulationTrend).mockResolvedValue([]);
    vi.mocked(api.deleteScheduledSimulation).mockResolvedValue(undefined);

    renderPage();
    await screen.findByText("Checkout");

    fireEvent.click(screen.getByRole("button", { name: /Delete/i }));

    await waitFor(() => expect(api.deleteScheduledSimulation).toHaveBeenCalledWith("sched-1"));
    await waitFor(() => expect(screen.queryByText("Checkout")).not.toBeInTheDocument());
  });
});
