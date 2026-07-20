import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { Workspace, WorkspacePrivacy } from "../../lib/types";

const PRIVACY_OPTIONS: { value: WorkspacePrivacy; label: string }[] = [
  { value: "private", label: "Private" },
  { value: "restricted", label: "Restricted" },
];

export function GeneralSettingsPage() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);

  useEffect(() => {
    api
      .getCurrentWorkspace()
      .then(setWorkspace)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load workspace.");
      });
  }, []);

  function update<K extends keyof Workspace>(key: K, value: Workspace[K]) {
    setWorkspace((prev) => (prev ? { ...prev, [key]: value } : prev));
    setSaved(false);
  }

  async function handleSave() {
    if (!workspace) return;
    setError(null);
    setSaving(true);
    try {
      const updated = await api.updateCurrentWorkspace({
        name: workspace.name,
        description: workspace.description,
        avatar_url: workspace.avatar_url,
        privacy: workspace.privacy,
        region: workspace.region,
        retention_days: workspace.retention_days,
      });
      setWorkspace(updated);
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save workspace.");
    } finally {
      setSaving(false);
    }
  }

  async function handleArchive() {
    setError(null);
    setArchiving(true);
    try {
      const updated = await api.archiveCurrentWorkspace();
      setWorkspace(updated);
      setConfirmArchive(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to archive workspace.");
    } finally {
      setArchiving(false);
    }
  }

  if (error !== null && workspace === null) {
    return <p className="text-error text-sm">{error}</p>;
  }

  if (workspace === null) {
    return <p className="text-on-surface-variant text-sm">Loading…</p>;
  }

  return (
    <div className="flex flex-col gap-8 max-w-3xl">
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div>
          <h1 className="font-headline text-3xl">General Settings</h1>
          <p className="text-on-surface-variant mt-1">
            Workspace identity, privacy, and data retention.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={saving}
          className="rounded-lg bg-primary py-2.5 px-6 text-on-primary font-medium hover:opacity-90 transition disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save Changes"}
        </button>
      </div>

      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}
      {saved ? <p className="text-sm text-primary">Workspace saved.</p> : null}

      <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-4">
        <h2 className="font-headline text-xl">Workspace Identity</h2>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-on-surface-variant">Project Name</span>
          <input
            type="text"
            value={workspace.name}
            onChange={(event) => update("name", event.target.value)}
            className="ghost-border rounded-lg px-3 py-2 bg-transparent"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-on-surface-variant">Description</span>
          <textarea
            value={workspace.description}
            onChange={(event) => update("description", event.target.value)}
            className="ghost-border rounded-lg px-3 py-2 bg-transparent"
            rows={3}
          />
        </label>
        <div className="flex gap-8 text-sm text-on-surface-variant">
          <span>Workspace ID: {workspace.slug}</span>
          <span>Established: {new Date(workspace.created_at).toLocaleDateString()}</span>
        </div>
      </section>

      <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-4">
        <h2 className="font-headline text-xl">Configuration Parameters</h2>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-on-surface-variant">Workspace Privacy</span>
          <div className="grid grid-cols-2 gap-3">
            {PRIVACY_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => update("privacy", option.value)}
                className={`rounded-lg py-3 text-center font-medium transition ${
                  workspace.privacy === option.value
                    ? "bg-primary text-on-primary"
                    : "ghost-border text-on-surface-variant hover:bg-surface-container"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-on-surface-variant">Regional Compliance</span>
          <input
            type="text"
            value={workspace.region}
            onChange={(event) => update("region", event.target.value)}
            className="ghost-border rounded-lg px-3 py-2 bg-transparent"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-on-surface-variant">Retention Policy (days)</span>
          <input
            type="number"
            min={1}
            max={3650}
            value={workspace.retention_days}
            onChange={(event) => update("retention_days", Number(event.target.value))}
            className="ghost-border rounded-lg px-3 py-2 bg-transparent"
          />
        </label>
      </section>

      <section className="bg-error-container/10 rounded-xl p-6 flex flex-col gap-4">
        <h2 className="font-headline text-xl text-on-error-container">Archive Workspace</h2>
        <p className="text-sm text-on-surface-variant">
          Archiving disables new simulations and event ingestion for this workspace. This can't be
          undone from this screen.
        </p>
        {workspace.archived ? (
          <p className="text-sm font-medium text-error">This workspace is archived.</p>
        ) : confirmArchive ? (
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => void handleArchive()}
              disabled={archiving}
              className="rounded-lg bg-error py-2 px-4 text-on-error font-medium disabled:opacity-50"
            >
              {archiving ? "Archiving…" : "Confirm Archive"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmArchive(false)}
              className="rounded-lg ghost-border py-2 px-4 font-medium"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setConfirmArchive(true)}
            className="self-start rounded-lg ghost-border py-2 px-4 font-medium text-error"
          >
            Archive Workspace
          </button>
        )}
      </section>
    </div>
  );
}
