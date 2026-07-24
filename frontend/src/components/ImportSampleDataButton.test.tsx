import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import { ImportSampleDataButton } from "./ImportSampleDataButton";

vi.mock("../lib/api", () => ({
  api: { importSampleData: vi.fn() },
  ApiError: class ApiError extends Error {},
}));

const mockApi = vi.mocked(api);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ImportSampleDataButton", () => {
  it("calls importSampleData and onImported on click", async () => {
    mockApi.importSampleData.mockResolvedValue({ events_ingested: 44, run_id: "run-1" });
    const onImported = vi.fn();

    render(<ImportSampleDataButton onImported={onImported} />);
    fireEvent.click(screen.getByRole("button", { name: /import sample data/i }));

    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    expect(mockApi.importSampleData).toHaveBeenCalledTimes(1);
  });

  it("shows an error message when the import fails", async () => {
    mockApi.importSampleData.mockRejectedValue(new Error("boom"));

    render(<ImportSampleDataButton />);
    fireEvent.click(screen.getByRole("button", { name: /import sample data/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
