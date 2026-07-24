import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import { GettingStartedPage } from "./GettingStartedPage";

vi.mock("../lib/api", () => ({
  api: { getOnboardingStatus: vi.fn(), importSampleData: vi.fn() },
  ApiError: class ApiError extends Error {},
}));

const mockApi = vi.mocked(api);

beforeEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  render(
    <MemoryRouter>
      <GettingStartedPage />
    </MemoryRouter>,
  );
}

describe("GettingStartedPage", () => {
  it("renders all 4 checklist items reflecting status", async () => {
    mockApi.getOnboardingStatus.mockResolvedValue({
      has_api_key: true,
      has_events: false,
      has_completed_simulation: false,
      has_multiple_members: false,
    });

    renderPage();

    await waitFor(() => expect(screen.getByText("Create an API key")).toBeInTheDocument());
    expect(screen.getByText("Ingest your first event")).toBeInTheDocument();
    expect(screen.getByText("Run your first simulation")).toBeInTheDocument();
    expect(screen.getByText("Invite a teammate")).toBeInTheDocument();
  });

  it("refetches status after a successful sample data import", async () => {
    mockApi.getOnboardingStatus
      .mockResolvedValueOnce({
        has_api_key: false,
        has_events: false,
        has_completed_simulation: false,
        has_multiple_members: false,
      })
      .mockResolvedValueOnce({
        has_api_key: false,
        has_events: true,
        has_completed_simulation: false,
        has_multiple_members: false,
      });
    mockApi.importSampleData.mockResolvedValue({ events_ingested: 44, run_id: "run-1" });

    renderPage();
    await waitFor(() => expect(screen.getByRole("button", { name: /import sample data/i })).toBeInTheDocument());

    screen.getByRole("button", { name: /import sample data/i }).click();

    await waitFor(() => expect(mockApi.getOnboardingStatus).toHaveBeenCalledTimes(2));
  });
});
