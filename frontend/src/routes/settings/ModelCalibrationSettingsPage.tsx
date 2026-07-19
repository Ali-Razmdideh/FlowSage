import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { CalibrationSettings, DigestFrequency } from "../../lib/types";

const DIGEST_OPTIONS: { value: DigestFrequency; label: string }[] = [
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
];

export function ModelCalibrationSettingsPage() {
  const [settings, setSettings] = useState<CalibrationSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .getModelCalibrationSettings()
      .then(setSettings)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load settings.");
      });
  }, []);

  function update<K extends keyof CalibrationSettings>(key: K, value: CalibrationSettings[K]) {
    setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
    setSaved(false);
  }

  async function handleSave() {
    if (!settings) return;
    setError(null);
    setSaving(true);
    try {
      const updated = await api.updateModelCalibrationSettings(settings);
      setSettings(updated);
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save settings.");
    } finally {
      setSaving(false);
    }
  }

  if (error !== null && settings === null) {
    return <p className="text-error text-sm">{error}</p>;
  }

  if (settings === null) {
    return <p className="text-on-surface-variant text-sm">Loading…</p>;
  }

  return (
    <div className="flex flex-col gap-8 max-w-3xl">
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div>
          <h1 className="font-headline text-3xl">Model Calibration</h1>
          <p className="text-on-surface-variant mt-1">
            Thresholds that decide what counts as a calibration anomaly or a churn-risk alert,
            and how the system reacts to them.
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
      {saved ? <p className="text-sm text-primary">Settings saved.</p> : null}

      <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-headline text-xl">Calibration Anomaly Threshold</h2>
            <p className="text-sm text-on-surface-variant mt-1">
              Minimum |predicted − observed| drop-off delta on a screen before it's flagged as a
              calibration anomaly on the Calibration Insights page.
            </p>
          </div>
          <span className="font-headline text-2xl text-primary">
            {Math.round(settings.anomaly_threshold * 100)}%
          </span>
        </div>
        <input
          type="range"
          min={0}
          max={100}
          value={Math.round(settings.anomaly_threshold * 100)}
          onChange={(event) => update("anomaly_threshold", Number(event.target.value) / 100)}
          className="w-full mt-2"
          aria-label="Calibration anomaly threshold"
        />
        <div className="flex justify-between text-xs text-on-surface-variant">
          <span>Sensitive (more anomalies flagged)</span>
          <span>Lenient (fewer anomalies flagged)</span>
        </div>
      </section>

      <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-headline text-xl">Churn Risk Alert Threshold</h2>
            <p className="text-sm text-on-surface-variant mt-1">
              Minimum churn-risk score for a cohort segment before it's surfaced as an alert on
              the Dashboard banner and included in the digest.
            </p>
          </div>
          <span className="font-headline text-2xl text-primary">
            {Math.round(settings.churn_risk_alert_threshold * 100)}%
          </span>
        </div>
        <input
          type="range"
          min={0}
          max={100}
          value={Math.round(settings.churn_risk_alert_threshold * 100)}
          onChange={(event) =>
            update("churn_risk_alert_threshold", Number(event.target.value) / 100)
          }
          className="w-full mt-2"
          aria-label="Churn risk alert threshold"
        />
      </section>

      <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-4">
        <h2 className="font-headline text-xl">Retraining Triggers</h2>
        <label className="flex items-start justify-between gap-4 cursor-pointer">
          <span>
            <span className="font-medium block">Auto-retrain on Anomaly</span>
            <span className="text-sm text-on-surface-variant">
              When enabled, personas with an open calibration anomaly are automatically queued for
              retraining by the daily digest job, instead of requiring a manual "Retrain" click on
              Calibration Insights.
            </span>
          </span>
          <input
            type="checkbox"
            checked={settings.auto_retrain_on_anomaly}
            onChange={(event) => update("auto_retrain_on_anomaly", event.target.checked)}
            className="mt-1 h-5 w-5 accent-primary"
          />
        </label>
      </section>

      <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-4">
        <div>
          <h2 className="font-headline text-xl">Digest Frequency</h2>
          <p className="text-sm text-on-surface-variant mt-1">
            How often the Slack/Jira alerts digest is sent (the only recurring background job in
            the system today).
          </p>
        </div>
        <div className="grid grid-cols-2 gap-3">
          {DIGEST_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => update("digest_frequency", option.value)}
              className={`rounded-lg py-3 text-center font-medium transition ${
                settings.digest_frequency === option.value
                  ? "bg-primary text-on-primary"
                  : "ghost-border text-on-surface-variant hover:bg-surface-container"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
