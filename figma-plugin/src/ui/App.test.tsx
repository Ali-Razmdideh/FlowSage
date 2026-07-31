// figma-plugin/src/ui/App.test.tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import * as bridge from "./bridge";
import * as api from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("App", () => {
  it("loads saved settings and the persona list on mount", async () => {
    vi.spyOn(bridge, "callPlugin").mockResolvedValue({
      baseUrl: "https://flowsage.example",
      apiKey: "fs_saved_key",
    });
    vi.spyOn(api, "listPersonas").mockResolvedValue([{ id: "p1", slug: "novice", name: "Novice" }]);

    render(<App />);

    expect(await screen.findByDisplayValue("https://flowsage.example")).toBeInTheDocument();
    expect(await screen.findByText("Novice")).toBeInTheDocument();
  });

  it("saves settings via the bridge when the Save button is clicked", async () => {
    const callPluginSpy = vi
      .spyOn(bridge, "callPlugin")
      .mockResolvedValueOnce({ baseUrl: "", apiKey: "" })
      .mockResolvedValueOnce(undefined);
    vi.spyOn(api, "listPersonas").mockResolvedValue([]);

    render(<App />);
    await waitFor(() => expect(callPluginSpy).toHaveBeenCalledWith("get-settings"));

    fireEvent.change(screen.getByLabelText(/base url/i), {
      target: { value: "https://my-instance.example" },
    });
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: "fs_new_key" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(callPluginSpy).toHaveBeenCalledWith("save-settings", {
        baseUrl: "https://my-instance.example",
        apiKey: "fs_new_key",
      }),
    );
  });

  it("runs the full export -> upload -> poll -> annotate flow and shows a success message", async () => {
    vi.spyOn(bridge, "callPlugin").mockImplementation(async (type: string) => {
      if (type === "get-settings") return { baseUrl: "https://flowsage.example", apiKey: "fs_key" };
      if (type === "export-selection") {
        return [{ index: 0, bytes: [1, 2, 3] }];
      }
      if (type === "annotate") return undefined;
      return undefined;
    });
    vi.spyOn(api, "listPersonas").mockResolvedValue([{ id: "p1", slug: "novice", name: "Novice" }]);
    vi.spyOn(api, "createSimulationRun").mockResolvedValue({
      id: "run-1",
      flow_name: "Checkout",
      goal: "Buy",
      persona_id: "p1",
      status: "queued",
      error: null,
    });
    vi.spyOn(api, "getSimulationRun").mockResolvedValue({
      id: "run-1",
      flow_name: "Checkout",
      goal: "Buy",
      persona_id: "p1",
      status: "completed",
      error: null,
      issues: [
        {
          id: "i1",
          screen: "001",
          severity: "high",
          title: "Confusing CTA",
          heuristic_violated: "Visibility of system status",
          persona_impact: "",
          description: "",
          suggested_fix: "Make the button more obvious.",
        },
      ],
    });

    render(<App />);
    await screen.findByText("Novice");

    fireEvent.change(screen.getByLabelText(/goal/i), { target: { value: "Buy" } });
    fireEvent.change(screen.getByLabelText(/flow name/i), { target: { value: "Checkout" } });
    fireEvent.click(screen.getByRole("button", { name: /run & annotate/i }));

    expect(await screen.findByText(/1 issue annotated/i)).toBeInTheDocument();
  });

  it("shows an error message when the simulation run fails", async () => {
    vi.spyOn(bridge, "callPlugin").mockImplementation(async (type: string) => {
      if (type === "get-settings") return { baseUrl: "https://flowsage.example", apiKey: "fs_key" };
      if (type === "export-selection") return [{ index: 0, bytes: [1, 2, 3] }];
      return undefined;
    });
    vi.spyOn(api, "listPersonas").mockResolvedValue([{ id: "p1", slug: "novice", name: "Novice" }]);
    vi.spyOn(api, "createSimulationRun").mockResolvedValue({
      id: "run-1",
      flow_name: "Checkout",
      goal: "Buy",
      persona_id: "p1",
      status: "queued",
      error: null,
    });
    vi.spyOn(api, "getSimulationRun").mockResolvedValue({
      id: "run-1",
      flow_name: "Checkout",
      goal: "Buy",
      persona_id: "p1",
      status: "failed",
      error: "Vision call timed out",
    });

    render(<App />);
    await screen.findByText("Novice");
    fireEvent.change(screen.getByLabelText(/goal/i), { target: { value: "Buy" } });
    fireEvent.change(screen.getByLabelText(/flow name/i), { target: { value: "Checkout" } });
    fireEvent.click(screen.getByRole("button", { name: /run & annotate/i }));

    expect(await screen.findByText(/Vision call timed out/i)).toBeInTheDocument();
  });
});
