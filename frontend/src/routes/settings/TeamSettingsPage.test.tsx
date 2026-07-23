import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { Member } from "../../lib/types";
import { TeamSettingsPage } from "./TeamSettingsPage";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getMembers: vi.fn(),
      addMember: vi.fn(),
      updateMemberRole: vi.fn(),
      removeMember: vi.fn(),
    },
  };
});

const ADMIN_MEMBER: Member = {
  id: "member-1",
  email: "admin@example.com",
  role: "admin",
  user_id: "user-1",
  created_at: "2024-01-01T00:00:00Z",
};

const VIEWER_MEMBER: Member = {
  id: "member-2",
  email: "viewer@example.com",
  role: "viewer",
  user_id: "user-2",
  created_at: "2024-01-02T00:00:00Z",
};

describe("TeamSettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  it("renders members from the API", async () => {
    vi.mocked(api.getMembers).mockResolvedValue([ADMIN_MEMBER, VIEWER_MEMBER]);

    render(<TeamSettingsPage />);

    expect(await screen.findByText("admin@example.com")).toBeInTheDocument();
    expect(screen.getByText("viewer@example.com")).toBeInTheDocument();
  });

  it("invites a new member and reloads the list", async () => {
    vi.mocked(api.getMembers).mockResolvedValue([ADMIN_MEMBER, VIEWER_MEMBER]);
    vi.mocked(api.addMember).mockResolvedValue({
      id: "member-3",
      email: "newmember@example.com",
      role: "researcher",
      user_id: "user-3",
      created_at: "2024-01-03T00:00:00Z",
    });

    render(<TeamSettingsPage />);

    await screen.findByText("admin@example.com");

    // Click "Invite Member" button
    fireEvent.click(screen.getByRole("button", { name: "Invite Member" }));

    // Fill in email
    const emailInput = screen.getByDisplayValue("") as HTMLInputElement;
    fireEvent.change(emailInput, { target: { value: "newmember@example.com" } });

    // Change role to "researcher" - get all selects and find the first one (in invite form)
    const selects = screen.getAllByDisplayValue("viewer");
    const roleSelect = selects[0] as HTMLSelectElement;
    fireEvent.change(roleSelect, { target: { value: "researcher" } });

    // Click "Add to Workspace"
    fireEvent.click(screen.getByRole("button", { name: "Add to Workspace" }));

    await waitFor(() => {
      expect(api.addMember).toHaveBeenCalledWith({
        email: "newmember@example.com",
        role: "researcher",
      });
    });

    await waitFor(() => {
      expect(api.getMembers).toHaveBeenCalledTimes(2);
    });
  });

  it("disables role select and remove button for the sole admin", async () => {
    vi.mocked(api.getMembers).mockResolvedValue([ADMIN_MEMBER, VIEWER_MEMBER]);

    render(<TeamSettingsPage />);

    await screen.findByText("admin@example.com");

    // Find the admin row's role select
    const selects = screen.getAllByDisplayValue("admin");
    const adminRoleSelect = selects[0] as HTMLSelectElement;
    expect(adminRoleSelect).toBeDisabled();

    // Find the admin row's remove button by finding the text and then the button in that row
    const adminRow = screen.getByText("admin@example.com").closest("tr");
    const removeButton = adminRow?.querySelector("button");
    expect(removeButton).toBeDisabled();
  });
});
