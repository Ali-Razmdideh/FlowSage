import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { IntegrationsSettingsPage } from "./IntegrationsSettingsPage";

vi.mock("../../lib/api", () => ({
  api: {
    getSlackStatus: vi.fn(),
    connectSlack: vi.fn(),
    disconnectSlack: vi.fn(),
    getJiraStatus: vi.fn(),
    connectJira: vi.fn(),
    disconnectJira: vi.fn(),
    getApiKeys: vi.fn(),
    createApiKey: vi.fn(),
    revokeApiKey: vi.fn(),
    getWebhooks: vi.fn(),
    createWebhook: vi.fn(),
    deleteWebhook: vi.fn(),
  },
  ApiError: class ApiError extends Error {},
}));

const mockApi = vi.mocked(api);

beforeEach(() => {
  vi.clearAllMocks();
  mockApi.getSlackStatus.mockResolvedValue({ connected: false, webhook_url_preview: null });
  mockApi.getJiraStatus.mockResolvedValue({
    connected: false,
    base_url: null,
    email: null,
    project_key: null,
  });
  mockApi.getApiKeys.mockResolvedValue([]);
  mockApi.getWebhooks.mockResolvedValue([]);
});

describe("IntegrationsSettingsPage", () => {
  it("connects Slack via the marketplace card form", async () => {
    mockApi.connectSlack.mockResolvedValue({ connected: true, webhook_url_preview: "...abcd" });
    render(<IntegrationsSettingsPage />);

    await waitFor(() => expect(screen.getAllByText(/not connected/i).length).toBeGreaterThan(0));
    const slackCard = screen.getByText("Slack").closest("div") as HTMLElement;
    await userEvent.click(within(slackCard).getByRole("button", { name: /connect/i }));
    await userEvent.type(screen.getByLabelText(/webhook url/i), "https://hooks.slack.test/abc");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(mockApi.connectSlack).toHaveBeenCalledWith("https://hooks.slack.test/abc")
    );
  });

  it("creates an API key and shows the raw key once", async () => {
    mockApi.createApiKey.mockResolvedValue({
      id: "key-1",
      name: "CI",
      key: "fs_live_abc123",
      key_prefix: "fs_live_abc1",
      created_at: "2026-07-23T00:00:00Z",
    });
    render(<IntegrationsSettingsPage />);

    await waitFor(() => expect(screen.getByText(/api keys/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /create key/i }));
    await userEvent.type(screen.getByLabelText(/key name/i), "CI");
    await userEvent.click(screen.getByRole("button", { name: /^generate$/i }));

    await waitFor(() => expect(screen.getByText("fs_live_abc123")).toBeInTheDocument());
  });

  it("adds a webhook and lists it in the table", async () => {
    const created = {
      id: "hook-1",
      url: "https://example.test/hook",
      event_types: ["alert.triggered"],
      enabled: true,
      created_at: "2026-07-23T00:00:00Z",
    };
    mockApi.createWebhook.mockResolvedValue({ ...created, secret: "s3cr3t" });
    mockApi.getWebhooks.mockResolvedValueOnce([]).mockResolvedValueOnce([created]);
    render(<IntegrationsSettingsPage />);

    await waitFor(() => expect(screen.getByText(/webhooks/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /add webhook/i }));
    await userEvent.type(screen.getByLabelText(/webhook url/i), "https://example.test/hook");
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() => expect(screen.getByText("https://example.test/hook")).toBeInTheDocument());
  });
});
