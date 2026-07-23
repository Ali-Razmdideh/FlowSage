import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { Workspace } from "../../lib/types";
import { GeneralSettingsPage } from "./GeneralSettingsPage";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getCurrentWorkspace: vi.fn(),
      updateCurrentWorkspace: vi.fn(),
      archiveCurrentWorkspace: vi.fn(),
    },
  };
});

const WORKSPACE: Workspace = {
  id: "workspace-1",
  name: "My Workspace",
  description: "A test workspace",
  slug: "my-workspace",
  avatar_url: "https://example.com/avatar.png",
  privacy: "private",
  region: "us-east-1",
  retention_days: 90,
  created_at: "2024-01-01T00:00:00Z",
  archived: false,
};

describe("GeneralSettingsPage", () => {
  it("renders the workspace and loads it from the API", async () => {
    vi.mocked(api.getCurrentWorkspace).mockResolvedValue(WORKSPACE);

    render(<GeneralSettingsPage />);

    expect(await screen.findByDisplayValue("My Workspace")).toBeInTheDocument();
    expect(screen.getByDisplayValue("A test workspace")).toBeInTheDocument();
    expect(screen.getByText(/Workspace ID: my-workspace/)).toBeInTheDocument();
  });

  it("saves updated workspace settings", async () => {
    vi.mocked(api.getCurrentWorkspace).mockResolvedValue(WORKSPACE);
    vi.mocked(api.updateCurrentWorkspace).mockResolvedValue({
      ...WORKSPACE,
      name: "Updated Workspace",
    });

    render(<GeneralSettingsPage />);

    await screen.findByDisplayValue("My Workspace");
    const nameInput = screen.getByDisplayValue("My Workspace") as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "Updated Workspace" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(api.updateCurrentWorkspace).toHaveBeenCalledWith(
        expect.objectContaining({ name: "Updated Workspace" }),
      );
    });
    expect(await screen.findByText("Workspace saved.")).toBeInTheDocument();
  });

  it("archives the workspace when confirmed", async () => {
    vi.mocked(api.getCurrentWorkspace).mockResolvedValue(WORKSPACE);
    vi.mocked(api.archiveCurrentWorkspace).mockResolvedValue({
      ...WORKSPACE,
      archived: true,
    });

    render(<GeneralSettingsPage />);

    await screen.findByDisplayValue("My Workspace");
    fireEvent.click(screen.getByRole("button", { name: "Archive Workspace" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm Archive" }));

    await waitFor(() => {
      expect(api.archiveCurrentWorkspace).toHaveBeenCalled();
    });
    expect(await screen.findByText("This workspace is archived.")).toBeInTheDocument();
  });
});
