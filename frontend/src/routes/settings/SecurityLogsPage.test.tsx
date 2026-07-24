import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { SecurityLogsPage } from "./SecurityLogsPage";

vi.mock("../../lib/api", () => ({
  api: { getAuditLogs: vi.fn() },
  ApiError: class ApiError extends Error {},
}));

const mockApi = vi.mocked(api);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("SecurityLogsPage", () => {
  it("renders audit log entries", async () => {
    mockApi.getAuditLogs.mockResolvedValue({
      entries: [
        {
          id: "log-1",
          actor_user_id: "user-1",
          action: "auth.login",
          target_type: null,
          target_id: null,
          extra_data: {},
          ip_address: "203.0.113.7",
          created_at: "2026-07-24T10:00:00Z",
        },
      ],
      next_cursor: null,
    });

    render(<SecurityLogsPage />);

    await waitFor(() => expect(screen.getByText("auth.login")).toBeInTheDocument());
    expect(screen.getByText("203.0.113.7")).toBeInTheDocument();
  });

  it("shows an empty state when there are no entries", async () => {
    mockApi.getAuditLogs.mockResolvedValue({ entries: [], next_cursor: null });
    render(<SecurityLogsPage />);
    await waitFor(() => expect(screen.getByText(/no security events/i)).toBeInTheDocument());
  });
});
