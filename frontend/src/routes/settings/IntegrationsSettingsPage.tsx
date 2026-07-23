import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { ApiKey, ApiKeyCreated, JiraStatus, SlackStatus, Webhook, WebhookCreated } from "../../lib/types";

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function SlackCard() {
  const [status, setStatus] = useState<SlackStatus | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getSlackStatus()
      .then(setStatus)
      .catch((err: unknown) => setError(errorMessage(err, "Failed to load Slack status.")));
  }, []);

  async function handleConnect() {
    setError(null);
    try {
      const updated = await api.connectSlack(webhookUrl);
      setStatus(updated);
      setConnecting(false);
      setWebhookUrl("");
    } catch (err) {
      setError(errorMessage(err, "Failed to connect Slack."));
    }
  }

  async function handleDisconnect() {
    setError(null);
    try {
      await api.disconnectSlack();
      setStatus({ connected: false, webhook_url_preview: null });
    } catch (err) {
      setError(errorMessage(err, "Failed to disconnect Slack."));
    }
  }

  if (status === null) return <p className="text-on-surface-variant text-sm">Loading…</p>;

  return (
    <div className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-3">
      <h3 className="font-headline text-lg">Slack</h3>
      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}
      {status.connected ? (
        <>
          <p className="text-sm text-on-surface-variant">Connected ({status.webhook_url_preview})</p>
          <button
            type="button"
            onClick={() => void handleDisconnect()}
            className="self-start rounded-lg ghost-border py-2 px-4 text-sm font-medium"
          >
            Disconnect
          </button>
        </>
      ) : connecting ? (
        <>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Webhook URL</span>
            <input
              value={webhookUrl}
              onChange={(event) => setWebhookUrl(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2 bg-transparent"
            />
          </label>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => void handleConnect()}
              disabled={webhookUrl.length === 0}
              className="rounded-lg bg-primary py-2 px-4 text-on-primary font-medium disabled:opacity-50"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => setConnecting(false)}
              className="rounded-lg ghost-border py-2 px-4 font-medium"
            >
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="text-sm text-on-surface-variant">Not connected.</p>
          <button
            type="button"
            onClick={() => setConnecting(true)}
            className="self-start rounded-lg bg-primary py-2 px-4 text-on-primary text-sm font-medium"
          >
            Connect
          </button>
        </>
      )}
    </div>
  );
}

function JiraCard() {
  const [status, setStatus] = useState<JiraStatus | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [baseUrl, setBaseUrl] = useState("");
  const [email, setEmail] = useState("");
  const [apiToken, setApiToken] = useState("");
  const [projectKey, setProjectKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getJiraStatus()
      .then(setStatus)
      .catch((err: unknown) => setError(errorMessage(err, "Failed to load Jira status.")));
  }, []);

  async function handleConnect() {
    setError(null);
    try {
      const updated = await api.connectJira({
        base_url: baseUrl,
        email,
        api_token: apiToken,
        project_key: projectKey,
      });
      setStatus(updated);
      setConnecting(false);
    } catch (err) {
      setError(errorMessage(err, "Failed to connect Jira."));
    }
  }

  async function handleDisconnect() {
    setError(null);
    try {
      await api.disconnectJira();
      setStatus({ connected: false, base_url: null, email: null, project_key: null });
    } catch (err) {
      setError(errorMessage(err, "Failed to disconnect Jira."));
    }
  }

  if (status === null) return <p className="text-on-surface-variant text-sm">Loading…</p>;

  return (
    <div className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-3">
      <h3 className="font-headline text-lg">Jira</h3>
      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}
      {status.connected ? (
        <>
          <p className="text-sm text-on-surface-variant">
            Connected — {status.project_key} ({status.email})
          </p>
          <button
            type="button"
            onClick={() => void handleDisconnect()}
            className="self-start rounded-lg ghost-border py-2 px-4 text-sm font-medium"
          >
            Disconnect
          </button>
        </>
      ) : connecting ? (
        <>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Base URL</span>
            <input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              className="ghost-border rounded-lg px-3 py-2 bg-transparent"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Email</span>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="ghost-border rounded-lg px-3 py-2 bg-transparent"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">API Token</span>
            <input
              type="password"
              value={apiToken}
              onChange={(e) => setApiToken(e.target.value)}
              className="ghost-border rounded-lg px-3 py-2 bg-transparent"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Project Key</span>
            <input
              value={projectKey}
              onChange={(e) => setProjectKey(e.target.value)}
              className="ghost-border rounded-lg px-3 py-2 bg-transparent"
            />
          </label>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => void handleConnect()}
              disabled={!baseUrl || !email || !apiToken || !projectKey}
              className="rounded-lg bg-primary py-2 px-4 text-on-primary font-medium disabled:opacity-50"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => setConnecting(false)}
              className="rounded-lg ghost-border py-2 px-4 font-medium"
            >
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="text-sm text-on-surface-variant">Not connected.</p>
          <button
            type="button"
            onClick={() => setConnecting(true)}
            className="self-start rounded-lg bg-primary py-2 px-4 text-on-primary text-sm font-medium"
          >
            Connect
          </button>
        </>
      )}
    </div>
  );
}

function ApiKeysSection() {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [revealed, setRevealed] = useState<ApiKeyCreated | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api
      .getApiKeys()
      .then(setKeys)
      .catch((err: unknown) => setError(errorMessage(err, "Failed to load API keys.")));
  }

  useEffect(load, []);

  async function handleCreate() {
    setError(null);
    try {
      const created = await api.createApiKey(name);
      setRevealed(created);
      setCreating(false);
      setName("");
      load();
    } catch (err) {
      setError(errorMessage(err, "Failed to create API key."));
    }
  }

  async function handleRevoke(id: string) {
    setError(null);
    try {
      await api.revokeApiKey(id);
      load();
    } catch (err) {
      setError(errorMessage(err, "Failed to revoke API key."));
    }
  }

  if (keys === null) return <p className="text-on-surface-variant text-sm">Loading…</p>;

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="font-headline text-xl">API Keys</h2>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="rounded-lg bg-primary py-2 px-4 text-on-primary text-sm font-medium"
        >
          Create key
        </button>
      </div>
      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}

      {revealed !== null ? (
        <div className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-2">
          <p className="text-sm text-on-surface-variant">
            Copy this key now — you won&apos;t be able to see it again.
          </p>
          <code className="bg-surface-container rounded-lg px-3 py-2 text-sm break-all">
            {revealed.key}
          </code>
          <button
            type="button"
            onClick={() => setRevealed(null)}
            className="self-start rounded-lg ghost-border py-2 px-4 text-sm font-medium"
          >
            Done
          </button>
        </div>
      ) : null}

      {creating ? (
        <div className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Key name</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="ghost-border rounded-lg px-3 py-2 bg-transparent"
            />
          </label>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => void handleCreate()}
              disabled={name.length === 0}
              className="rounded-lg bg-primary py-2 px-4 text-on-primary font-medium disabled:opacity-50"
            >
              Generate
            </button>
            <button
              type="button"
              onClick={() => setCreating(false)}
              className="rounded-lg ghost-border py-2 px-4 font-medium"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      <table className="w-full text-sm bg-surface-container-lowest rounded-xl overflow-hidden">
        <thead className="text-left text-on-surface-variant border-b border-outline-variant">
          <tr>
            <th className="px-6 py-3 font-medium">Name</th>
            <th className="px-6 py-3 font-medium">Prefix</th>
            <th className="px-6 py-3 font-medium">Created</th>
            <th className="px-6 py-3 font-medium">Last used</th>
            <th className="px-6 py-3 font-medium"></th>
          </tr>
        </thead>
        <tbody>
          {keys.map((key) => (
            <tr key={key.id} className="border-b border-outline-variant last:border-0">
              <td className="px-6 py-3">{key.name}</td>
              <td className="px-6 py-3 font-mono text-xs">{key.key_prefix}…</td>
              <td className="px-6 py-3 text-on-surface-variant">
                {new Date(key.created_at).toLocaleDateString()}
              </td>
              <td className="px-6 py-3 text-on-surface-variant">
                {key.last_used_at ? new Date(key.last_used_at).toLocaleDateString() : "Never"}
              </td>
              <td className="px-6 py-3 text-right">
                {key.revoked ? (
                  <span className="text-on-surface-variant text-xs">Revoked</span>
                ) : (
                  <button
                    type="button"
                    onClick={() => void handleRevoke(key.id)}
                    className="text-error text-xs font-medium hover:underline"
                  >
                    Revoke
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function WebhooksSection() {
  const [webhooks, setWebhooks] = useState<Webhook[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [url, setUrl] = useState("");
  const [revealed, setRevealed] = useState<WebhookCreated | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api
      .getWebhooks()
      .then(setWebhooks)
      .catch((err: unknown) => setError(errorMessage(err, "Failed to load webhooks.")));
  }

  useEffect(load, []);

  async function handleCreate() {
    setError(null);
    try {
      const created = await api.createWebhook(url, ["alert.triggered"]);
      setRevealed(created);
      setCreating(false);
      setUrl("");
      load();
    } catch (err) {
      setError(errorMessage(err, "Failed to create webhook."));
    }
  }

  async function handleDelete(id: string) {
    setError(null);
    try {
      await api.deleteWebhook(id);
      load();
    } catch (err) {
      setError(errorMessage(err, "Failed to delete webhook."));
    }
  }

  if (webhooks === null) return <p className="text-on-surface-variant text-sm">Loading…</p>;

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="font-headline text-xl">Webhooks</h2>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="rounded-lg bg-primary py-2 px-4 text-on-primary text-sm font-medium"
        >
          Add webhook
        </button>
      </div>
      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}

      {revealed !== null ? (
        <div className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-2">
          <p className="text-sm text-on-surface-variant">
            Copy this signing secret now — you won&apos;t be able to see it again.
          </p>
          <code className="bg-surface-container rounded-lg px-3 py-2 text-sm break-all">
            {revealed.secret}
          </code>
          <button
            type="button"
            onClick={() => setRevealed(null)}
            className="self-start rounded-lg ghost-border py-2 px-4 text-sm font-medium"
          >
            Done
          </button>
        </div>
      ) : null}

      {creating ? (
        <div className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Webhook URL</span>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="ghost-border rounded-lg px-3 py-2 bg-transparent"
            />
          </label>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => void handleCreate()}
              disabled={url.length === 0}
              className="rounded-lg bg-primary py-2 px-4 text-on-primary font-medium disabled:opacity-50"
            >
              Add
            </button>
            <button
              type="button"
              onClick={() => setCreating(false)}
              className="rounded-lg ghost-border py-2 px-4 font-medium"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      <table className="w-full text-sm bg-surface-container-lowest rounded-xl overflow-hidden">
        <thead className="text-left text-on-surface-variant border-b border-outline-variant">
          <tr>
            <th className="px-6 py-3 font-medium">URL</th>
            <th className="px-6 py-3 font-medium">Events</th>
            <th className="px-6 py-3 font-medium">Created</th>
            <th className="px-6 py-3 font-medium"></th>
          </tr>
        </thead>
        <tbody>
          {webhooks.map((webhook) => (
            <tr key={webhook.id} className="border-b border-outline-variant last:border-0">
              <td className="px-6 py-3 break-all">{webhook.url}</td>
              <td className="px-6 py-3 text-on-surface-variant">{webhook.event_types.join(", ")}</td>
              <td className="px-6 py-3 text-on-surface-variant">
                {new Date(webhook.created_at).toLocaleDateString()}
              </td>
              <td className="px-6 py-3 text-right">
                <button
                  type="button"
                  onClick={() => void handleDelete(webhook.id)}
                  className="text-error text-xs font-medium hover:underline"
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function IntegrationsSettingsPage() {
  return (
    <div className="flex flex-col gap-8 max-w-3xl">
      <div>
        <h1 className="font-headline text-3xl">Integrations</h1>
        <p className="text-on-surface-variant mt-1">
          Connect Slack/Jira, manage API keys and webhooks.
        </p>
      </div>

      <section className="flex flex-col gap-4">
        <h2 className="font-headline text-xl">Marketplace</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <SlackCard />
          <JiraCard />
        </div>
      </section>

      <ApiKeysSection />
      <WebhooksSection />
    </div>
  );
}
