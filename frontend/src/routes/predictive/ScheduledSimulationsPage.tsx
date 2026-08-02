import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../../lib/api";
import type { Persona, ScheduledSimulation, ScheduleInterval, TrendPoint } from "../../lib/types";

const INTERVAL_LABELS: Record<ScheduleInterval, string> = {
  daily: "Daily",
  weekly: "Weekly",
  on_push: "On push",
};

const TREND_WIDTH = 320;
const TREND_HEIGHT = 120;

function FrictionTrendChart({ points }: { points: TrendPoint[] }) {
  if (points.length === 0) {
    return <p className="text-on-surface-variant text-sm">No completed runs yet.</p>;
  }
  const step = points.length > 1 ? TREND_WIDTH / (points.length - 1) : 0;
  const coords = points.map((point, index) => ({
    x: index * step,
    y: (1 - point.score) * TREND_HEIGHT,
  }));
  const path = coords.map((c, i) => `${i === 0 ? "M" : "L"}${c.x},${c.y}`).join(" ");

  return (
    <svg
      viewBox={`0 0 ${TREND_WIDTH} ${TREND_HEIGHT}`}
      className="w-full max-w-xs"
      role="img"
      aria-label="Friction score trend over scheduled runs"
    >
      <line
        x1={0}
        y1={TREND_HEIGHT}
        x2={TREND_WIDTH}
        y2={TREND_HEIGHT}
        className="stroke-outline-variant"
        strokeWidth={1}
      />
      <path d={path} fill="none" className="stroke-primary" strokeWidth={2} />
      {coords.map((c, i) => {
        const point = points[i];
        if (!point) return null;
        return (
          <circle key={point.run_id} cx={c.x} cy={c.y} r={4} className="fill-primary">
            <title>
              {new Date(point.created_at).toLocaleDateString()}: {(point.score * 100).toFixed(0)}%
            </title>
          </circle>
        );
      })}
    </svg>
  );
}

function ScheduledSimulationCard({
  config,
  personaName,
  trend,
  onToggleActive,
  onDelete,
  onPushScreenshots,
  onSaveEdits,
}: {
  config: ScheduledSimulation;
  personaName: string;
  trend: TrendPoint[];
  onToggleActive: () => void;
  onDelete: () => void;
  onPushScreenshots: (files: File[]) => void;
  onSaveEdits: (goal: string, interval: ScheduleInterval) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [goalDraft, setGoalDraft] = useState(config.goal);
  const [intervalDraft, setIntervalDraft] = useState<ScheduleInterval>(config.interval);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function startEditing() {
    setGoalDraft(config.goal);
    setIntervalDraft(config.interval);
    setSaveError(null);
    setEditing(true);
  }

  async function save() {
    setSaveError(null);
    setSaving(true);
    const succeeded = await onSaveEdits(goalDraft, intervalDraft);
    setSaving(false);
    if (succeeded) {
      // Only leave edit mode — and drop the local draft — once the server has
      // confirmed the change. On failure we stay in edit mode with the user's
      // draft intact so nothing is silently lost.
      setEditing(false);
    } else {
      setSaveError("Failed to save changes. Your edits are unsaved — try again.");
    }
  }

  return (
    <div className="ghost-border rounded-lg p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-medium">{config.flow_name}</p>
          <p className="text-sm text-on-surface-variant">
            {personaName} · {INTERVAL_LABELS[config.interval]}
            {config.has_pending_screenshots ? " · screenshots staged" : ""}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {editing ? null : (
            <button
              type="button"
              onClick={startEditing}
              className="text-sm font-medium text-primary hover:underline"
            >
              Edit
            </button>
          )}
          <button
            type="button"
            onClick={onToggleActive}
            className="text-sm font-medium text-primary hover:underline"
          >
            {config.active ? "Pause" : "Resume"}
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="text-sm font-medium text-error hover:underline"
          >
            Delete
          </button>
        </div>
      </div>

      {editing ? (
        <div className="flex flex-col gap-3 ghost-border rounded-lg p-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Goal</span>
            <input
              value={goalDraft}
              onChange={(event) => setGoalDraft(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Interval</span>
            <select
              value={intervalDraft}
              onChange={(event) => setIntervalDraft(event.target.value as ScheduleInterval)}
              className="ghost-border rounded-lg px-3 py-2"
            >
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
              <option value="on_push">On push</option>
            </select>
          </label>
          {saveError !== null ? (
            <p role="alert" className="text-sm text-error">
              {saveError}
            </p>
          ) : null}

          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving}
              className="rounded-lg bg-primary px-3 py-1.5 text-sm text-on-primary font-medium hover:opacity-90 transition disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              disabled={saving}
              className="text-sm font-medium text-on-surface-variant hover:underline disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <p className="text-sm text-on-surface-variant">{config.goal}</p>
      )}

      <label className="flex flex-col gap-1 text-sm">
        <span className="text-on-surface-variant">Push fresh screenshots (png/jpg/webp)</span>
        <input
          type="file"
          multiple
          accept="image/png,image/jpeg,image/webp"
          onChange={(event) => onPushScreenshots(Array.from(event.target.files ?? []))}
          className="ghost-border rounded-lg px-3 py-2"
        />
      </label>

      <FrictionTrendChart points={trend} />
    </div>
  );
}

export function ScheduledSimulationsPage() {
  const [personas, setPersonas] = useState<Persona[] | null>(null);
  const [configs, setConfigs] = useState<ScheduledSimulation[] | null>(null);
  const [trends, setTrends] = useState<Record<string, TrendPoint[]>>({});
  const [personaId, setPersonaId] = useState("");
  const [flowName, setFlowName] = useState("");
  const [goal, setGoal] = useState("Complete purchase");
  const [scheduleInterval, setScheduleInterval] = useState<ScheduleInterval>("daily");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    void loadAll();
  }, []);

  async function loadAll() {
    try {
      const [personaList, configList] = await Promise.all([
        api.listPersonas(),
        api.listScheduledSimulations(),
      ]);
      setPersonas(personaList);
      const first = personaList[0];
      if (first) setPersonaId(first.id);
      setConfigs(configList);
      const trendEntries = await Promise.all(
        configList.map(
          async (config) => [config.id, await api.getScheduledSimulationTrend(config.id)] as const,
        ),
      );
      setTrends(Object.fromEntries(trendEntries));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load scheduled simulations.");
      // Fall back to an empty (not null) list so the Schedules section renders
      // its actual empty/error state instead of hanging on "Loading…" forever.
      setConfigs((prev) => prev ?? []);
      setPersonas((prev) => prev ?? []);
    }
  }

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const config = await api.createScheduledSimulation({
        persona_id: personaId,
        flow_name: flowName,
        goal,
        interval: scheduleInterval,
      });
      setConfigs((prev) => [...(prev ?? []), config]);
      setTrends((prev) => ({ ...prev, [config.id]: [] }));
      setFlowName("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create scheduled simulation.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleToggleActive(config: ScheduledSimulation) {
    try {
      const updated = await api.updateScheduledSimulation(config.id, { active: !config.active });
      setConfigs((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update scheduled simulation.");
    }
  }

  async function handleSaveEdits(
    configId: string,
    goalEdit: string,
    intervalEdit: ScheduleInterval,
  ): Promise<boolean> {
    try {
      const updated = await api.updateScheduledSimulation(configId, {
        goal: goalEdit,
        interval: intervalEdit,
      });
      setConfigs((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update scheduled simulation.");
      return false;
    }
  }

  async function handleDelete(configId: string) {
    try {
      await api.deleteScheduledSimulation(configId);
      setConfigs((prev) => prev?.filter((c) => c.id !== configId) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete scheduled simulation.");
    }
  }

  async function handlePushScreenshots(configId: string, files: File[]) {
    if (files.length === 0) return;
    try {
      const updated = await api.pushScheduledSimulationScreenshots(configId, files);
      setConfigs((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to push screenshots.");
    }
  }

  return (
    <div className="flex flex-col gap-8 max-w-3xl">
      <div>
        <h1 className="font-headline text-3xl">Scheduled Runs</h1>
        <p className="text-on-surface-variant mt-1">
          Recurring simulations with a friction-score trend across releases.{" "}
          <Link to="/predictive" className="text-primary hover:underline">
            Back to Predictive Engine
          </Link>
        </p>
      </div>

      <section className="bg-surface-container-lowest rounded-xl p-6">
        <h2 className="font-headline text-xl mb-4">New Schedule</h2>
        <form onSubmit={(event) => void handleCreate(event)} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Persona</span>
            <select
              required
              value={personaId}
              onChange={(event) => setPersonaId(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2"
            >
              {personas?.map((persona) => (
                <option key={persona.id} value={persona.id}>
                  {persona.name}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Flow name</span>
            <input
              required
              value={flowName}
              onChange={(event) => setFlowName(event.target.value)}
              placeholder="Checkout Flow"
              className="ghost-border rounded-lg px-3 py-2"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Goal</span>
            <input
              required
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Interval</span>
            <select
              value={scheduleInterval}
              onChange={(event) => setScheduleInterval(event.target.value as ScheduleInterval)}
              className="ghost-border rounded-lg px-3 py-2"
            >
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
              <option value="on_push">On push</option>
            </select>
          </label>

          {error !== null ? (
            <p role="alert" className="text-sm text-error">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={submitting || personas === null || personas.length === 0}
            className="rounded-lg bg-primary py-2.5 text-on-primary font-medium hover:opacity-90 transition disabled:opacity-50"
          >
            {submitting ? "Creating…" : "Create Schedule"}
          </button>
        </form>
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="font-headline text-xl">Schedules</h2>
        {configs === null ? (
          <p className="text-on-surface-variant text-sm">Loading…</p>
        ) : configs.length === 0 ? (
          <p className="text-on-surface-variant text-sm">No scheduled runs yet.</p>
        ) : (
          configs.map((config) => (
            <ScheduledSimulationCard
              key={config.id}
              config={config}
              personaName={
                personas?.find((p) => p.id === config.persona_id)?.name ?? config.persona_id
              }
              trend={trends[config.id] ?? []}
              onToggleActive={() => void handleToggleActive(config)}
              onDelete={() => void handleDelete(config.id)}
              onPushScreenshots={(files) => void handlePushScreenshots(config.id, files)}
              onSaveEdits={(goalEdit, intervalEdit) =>
                handleSaveEdits(config.id, goalEdit, intervalEdit)
              }
            />
          ))
        )}
      </section>
    </div>
  );
}
