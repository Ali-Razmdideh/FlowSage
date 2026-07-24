import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ImportSampleDataButton } from "../components/ImportSampleDataButton";
import { api, ApiError } from "../lib/api";
import type { OnboardingStatus } from "../lib/types";

const CHECKLIST: { key: keyof OnboardingStatus; label: string; to: string }[] = [
  { key: "has_api_key", label: "Create an API key", to: "/settings/integrations" },
  { key: "has_events", label: "Ingest your first event", to: "/getting-started" },
  { key: "has_completed_simulation", label: "Run your first simulation", to: "/predictive" },
  { key: "has_multiple_members", label: "Invite a teammate", to: "/settings/team" },
];

export function GettingStartedPage() {
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = useCallback(() => {
    api
      .getOnboardingStatus()
      .then(setStatus)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load onboarding status.");
      });
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  return (
    <div className="flex flex-col gap-6 p-8 max-w-2xl">
      <div>
        <h1 className="font-headline text-2xl">Getting Started</h1>
        <p className="text-sm text-on-surface-variant mt-1">
          Four steps to get FlowSage fully wired up for your team.
        </p>
      </div>

      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}

      {status === null ? (
        <p className="text-on-surface-variant text-sm">Loading…</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {CHECKLIST.map((item) => (
            <li key={item.key}>
              <Link
                to={item.to}
                className="flex items-center gap-3 ghost-border rounded-lg p-4 hover:bg-surface-container transition"
              >
                <span className="material-symbols-outlined text-lg text-primary">
                  {status[item.key] ? "check_circle" : "radio_button_unchecked"}
                </span>
                <span className={status[item.key] ? "text-on-surface-variant line-through" : ""}>
                  {item.label}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}

      <div className="bg-surface-container-lowest rounded-xl p-6 flex flex-col items-center gap-3">
        <p className="text-sm text-on-surface-variant text-center max-w-sm">
          Not ready to connect real data yet? Load a demo checkout flow to see the Journey Graph
          and Predictive Engine populated in one click.
        </p>
        <ImportSampleDataButton onImported={loadStatus} />
      </div>
    </div>
  );
}
