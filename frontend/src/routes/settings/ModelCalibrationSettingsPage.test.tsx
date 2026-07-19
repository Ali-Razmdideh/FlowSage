import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { CalibrationSettings } from "../../lib/types";
import { ModelCalibrationSettingsPage } from "./ModelCalibrationSettingsPage";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getModelCalibrationSettings: vi.fn(),
      updateModelCalibrationSettings: vi.fn(),
    },
  };
});

const SETTINGS: CalibrationSettings = {
  anomaly_threshold: 0.35,
  churn_risk_alert_threshold: 0.5,
  auto_retrain_on_anomaly: false,
  digest_frequency: "weekly",
};

describe("ModelCalibrationSettingsPage", () => {
  it("renders the current thresholds and toggles", async () => {
    vi.mocked(api.getModelCalibrationSettings).mockResolvedValue(SETTINGS);

    render(<ModelCalibrationSettingsPage />);

    expect(await screen.findByText("35%")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Weekly" })).toHaveClass("bg-primary");
  });

  it("saves updated settings", async () => {
    vi.mocked(api.getModelCalibrationSettings).mockResolvedValue(SETTINGS);
    vi.mocked(api.updateModelCalibrationSettings).mockResolvedValue({
      ...SETTINGS,
      auto_retrain_on_anomaly: true,
      digest_frequency: "daily",
    });

    render(<ModelCalibrationSettingsPage />);

    await screen.findByText("35%");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Daily" }));
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(api.updateModelCalibrationSettings).toHaveBeenCalledWith(
        expect.objectContaining({ auto_retrain_on_anomaly: true, digest_frequency: "daily" }),
      );
    });
    expect(await screen.findByText("Settings saved.")).toBeInTheDocument();
  });
});
