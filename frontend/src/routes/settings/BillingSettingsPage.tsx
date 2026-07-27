import { useEffect, useState } from "react";
import { useAuth } from "../../auth/AuthContext";
import { api, ApiError } from "../../lib/api";
import type { UsageSnapshot } from "../../lib/types";

const TIER_LABELS: Record<UsageSnapshot["tier"], string> = {
  free: "Free",
  pro: "Pro",
  team: "Team",
};

function UsageBar({ label, used, limit }: { label: string; used: number; limit: number }) {
  const unlimited = limit === -1;
  const pct = unlimited ? 0 : Math.min(100, (used / limit) * 100);
  const color = pct >= 100 ? "bg-error" : pct >= 80 ? "bg-tertiary" : "bg-primary";

  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-sm">
        <span className="text-on-surface-variant">{label}</span>
        <span>{unlimited ? `${used.toLocaleString()} / Unlimited` : `${used.toLocaleString()} / ${limit.toLocaleString()}`}</span>
      </div>
      {!unlimited ? (
        <div className="h-2 rounded-full bg-surface-container overflow-hidden">
          <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
        </div>
      ) : null}
    </div>
  );
}

export function BillingSettingsPage() {
  const { user } = useAuth();
  // POST /billing/checkout and /billing/portal require Role.ADMIN on the
  // backend; hide the controls rather than let non-admins click into a 403.
  const isAdmin = user?.role === "admin";
  const [usage, setUsage] = useState<UsageSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [redirecting, setRedirecting] = useState(false);

  useEffect(() => {
    api
      .getBillingUsage()
      .then(setUsage)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load billing usage.");
      });
  }, []);

  async function handleUpgrade(tier: "pro" | "team") {
    setError(null);
    setRedirecting(true);
    try {
      const result = await api.startCheckout(tier);
      window.location.assign(result.url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start checkout.");
      setRedirecting(false);
    }
  }

  async function handleManageBilling() {
    setError(null);
    setRedirecting(true);
    try {
      const result = await api.openBillingPortal();
      window.location.assign(result.url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to open billing portal.");
      setRedirecting(false);
    }
  }

  if (usage === null) {
    return error !== null ? (
      <p className="text-error text-sm">{error}</p>
    ) : (
      <p className="text-on-surface-variant text-sm">Loading…</p>
    );
  }

  return (
    <div className="flex flex-col gap-8 max-w-3xl">
      <div>
        <h1 className="font-headline text-3xl">Billing</h1>
        <p className="text-on-surface-variant mt-1">
          Current plan: <span className="font-medium">{TIER_LABELS[usage.tier]}</span>
        </p>
      </div>

      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}

      <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-4">
        <h2 className="font-headline text-xl">Usage This Month</h2>
        <UsageBar label="Events ingested" used={usage.events_used} limit={usage.events_limit} />
        <UsageBar label="Simulation runs" used={usage.runs_used} limit={usage.runs_limit} />
        <UsageBar label="Seats" used={usage.seats_used} limit={usage.seats_limit} />
      </section>

      <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-4">
        <h2 className="font-headline text-xl">Manage Plan</h2>
        {!isAdmin ? (
          <p className="text-on-surface-variant text-sm">
            Only workspace admins can change the plan or manage billing.
          </p>
        ) : (
          <div className="flex gap-3 flex-wrap">
            {usage.tier !== "pro" && usage.tier !== "team" ? (
              <button
                type="button"
                onClick={() => void handleUpgrade("pro")}
                disabled={redirecting}
                className="rounded-lg bg-primary py-2.5 px-6 text-on-primary font-medium hover:opacity-90 transition disabled:opacity-50"
              >
                Upgrade to Pro
              </button>
            ) : null}
            {usage.tier !== "team" ? (
              <button
                type="button"
                onClick={() => void handleUpgrade("team")}
                disabled={redirecting}
                className="rounded-lg ghost-border py-2.5 px-6 font-medium hover:bg-surface-container transition disabled:opacity-50"
              >
                Upgrade to Team
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => void handleManageBilling()}
              disabled={redirecting}
              className="rounded-lg ghost-border py-2.5 px-6 font-medium hover:bg-surface-container transition disabled:opacity-50"
            >
              Manage Billing
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
