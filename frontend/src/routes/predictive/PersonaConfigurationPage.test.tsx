import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { PersonaDetail } from "../../lib/types";
import { PersonaConfigurationPage } from "./PersonaConfigurationPage";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getPersona: vi.fn(),
      createPersona: vi.fn(),
      updatePersona: vi.fn(),
      resetPersona: vi.fn(),
      deletePersona: vi.fn(),
    },
  };
});

const BASELINE_PERSONA: PersonaDetail = {
  id: "persona-1",
  slug: "novice-user",
  name: "Novice User",
  description: "Represents users with limited domain knowledge.",
  baseline: true,
  tech_affinity: "Low",
  primary_device: "Mobile / Tablet",
  discovery_mode: "Search-driven",
  contextual_triggers: ["Time Constraint", "High Distraction"],
  technical_literacy: 0.2,
  anxiety: 0.85,
  patience: 0.3,
  curiosity: 0.4,
  memories: [
    {
      id: "mem-1",
      title: "Cart Abandonment (Complex Pricing)",
      note: "Persona aborted checkout flow upon encountering tiered pricing.",
      kind: "retraining",
      created_at: "2026-07-17T12:00:00Z",
    },
  ],
};

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/predictive/personas/new" element={<PersonaConfigurationPage />} />
        <Route path="/predictive/personas/:personaId" element={<PersonaConfigurationPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("PersonaConfigurationPage", () => {
  it("loads an existing persona and renders its sliders and memory bank", async () => {
    vi.mocked(api.getPersona).mockResolvedValue(BASELINE_PERSONA);

    renderAt("/predictive/personas/persona-1");

    expect(await screen.findByDisplayValue("Novice User")).toBeInTheDocument();
    expect(screen.getByText("85%")).toBeInTheDocument();
    expect(screen.getByText("Baseline Persona")).toBeInTheDocument();
    expect(screen.getByText("Cart Abandonment (Complex Pricing)")).toBeInTheDocument();
  });

  it("resets a baseline persona to its default values", async () => {
    vi.mocked(api.getPersona).mockResolvedValue(BASELINE_PERSONA);
    vi.mocked(api.resetPersona).mockResolvedValue({ ...BASELINE_PERSONA, anxiety: 0.5 });

    renderAt("/predictive/personas/persona-1");

    fireEvent.click(await screen.findByRole("button", { name: "Reset Default" }));

    expect(api.resetPersona).toHaveBeenCalledWith("persona-1");
    expect(await screen.findByText("50%")).toBeInTheDocument();
  });

  it("does not show Delete for a baseline persona", async () => {
    vi.mocked(api.getPersona).mockResolvedValue(BASELINE_PERSONA);

    renderAt("/predictive/personas/persona-1");

    await screen.findByDisplayValue("Novice User");
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("creates a new persona from the form", async () => {
    vi.mocked(api.createPersona).mockResolvedValue({ ...BASELINE_PERSONA, id: "persona-2" });

    renderAt("/predictive/personas/new");

    fireEvent.change(screen.getByPlaceholderText("novice-user"), {
      target: { value: "power-user" },
    });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Power User" } });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Fast, confident, expects shortcuts." },
    });
    fireEvent.change(screen.getByLabelText("Tech Affinity"), { target: { value: "High" } });
    fireEvent.change(screen.getByLabelText("Primary Device"), { target: { value: "Desktop" } });
    fireEvent.change(screen.getByLabelText("Discovery Mode"), {
      target: { value: "Keyboard shortcuts" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create Persona" }));

    await waitFor(() => {
      expect(api.createPersona).toHaveBeenCalledWith(
        expect.objectContaining({ slug: "power-user", name: "Power User" }),
      );
    });
  });
});
