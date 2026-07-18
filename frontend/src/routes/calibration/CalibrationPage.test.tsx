import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { CalibrationReport, RetrainingJob } from "../../lib/types";
import { CalibrationPage } from "./CalibrationPage";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getCalibrationReport: vi.fn(),
      startRetraining: vi.fn(),
      retrainingStreamUrl: vi.fn(() => "/api/calibration/retrain/job-1/stream"),
    },
  };
});

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onerror: (() => void) | null = null;
  private listeners: Record<string, ((event: MessageEvent<string>) => void)[]> = {};

  constructor(_url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    (this.listeners[type] ??= []).push(listener);
  }

  emit(type: string, data: unknown) {
    for (const listener of this.listeners[type] ?? []) {
      listener({ data: JSON.stringify(data) } as MessageEvent<string>);
    }
  }

  close() {}
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

describe("CalibrationPage", () => {
  it("shows the optimized state when there is no anomaly", async () => {
    const report: CalibrationReport = {
      personas: [],
      accuracy_points: [{ persona_id: "p1", persona_name: "Novice", complexity: 0.5, accuracy: 0.98 }],
      has_anomaly: false,
    };
    vi.mocked(api.getCalibrationReport).mockResolvedValue(report);

    render(<CalibrationPage />);

    expect(await screen.findByText("System Optimized")).toBeInTheDocument();
    expect(screen.getByText("98.0%")).toBeInTheDocument();
  });

  it("shows the anomaly banner and per-screen table when a persona is miscalibrated", async () => {
    const report: CalibrationReport = {
      personas: [
        {
          persona_id: "p1",
          persona_name: "Low-Patience Mobile",
          run_id: "r1",
          screens: [
            { screen: "checkout", predicted_score: 0.2, observed_score: 0.9, delta: 0.7, anomaly: true },
          ],
        },
      ],
      accuracy_points: [{ persona_id: "p1", persona_name: "Low-Patience Mobile", complexity: 0.3, accuracy: 0.4 }],
      has_anomaly: true,
    };
    vi.mocked(api.getCalibrationReport).mockResolvedValue(report);

    render(<CalibrationPage />);

    expect(await screen.findByText("Calibration Anomaly Detected")).toBeInTheDocument();
    expect(screen.getByText("checkout")).toBeInTheDocument();
    expect(screen.getByText("+0.70")).toBeInTheDocument();
  });

  it("starts retraining and shows live progress when the button is clicked", async () => {
    const report: CalibrationReport = {
      personas: [
        {
          persona_id: "p1",
          persona_name: "Low-Patience Mobile",
          run_id: "r1",
          screens: [
            { screen: "checkout", predicted_score: 0.2, observed_score: 0.9, delta: 0.7, anomaly: true },
          ],
        },
      ],
      accuracy_points: [],
      has_anomaly: true,
    };
    const job: RetrainingJob = {
      id: "job-1",
      persona_id: "p1",
      status: "queued",
      epoch: 0,
      total_epochs: 1,
      progress: 0,
      error: null,
    };
    vi.mocked(api.getCalibrationReport).mockResolvedValue(report);
    vi.mocked(api.startRetraining).mockResolvedValue(job);

    render(<CalibrationPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Initiate Retraining →" }));

    expect(await screen.findByText("Persona Re-calibration in Progress")).toBeInTheDocument();
    expect(api.startRetraining).toHaveBeenCalledWith("p1");

    const source = FakeEventSource.instances[0]!;
    source.emit("progress", { ...job, status: "running", epoch: 1, progress: 100 });

    expect(await screen.findByText("100%")).toBeInTheDocument();
  });
});
