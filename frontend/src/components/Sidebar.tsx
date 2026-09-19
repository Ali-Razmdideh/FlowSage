import { useState } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const NAV_ITEMS = [
  { to: "/getting-started", label: "Getting Started", icon: "flag" },
  { to: "/dashboard", label: "Dashboard", icon: "dashboard" },
  { to: "/predictive", label: "Predictive Engine", icon: "psychology" },
  { to: "/journey", label: "Journey Graph", icon: "timeline" },
  { to: "/calibration", label: "Calibration", icon: "tune" },
  { to: "/settings/general", label: "General Settings", icon: "settings" },
  { to: "/settings/billing", label: "Billing", icon: "credit_card" },
  { to: "/settings/team", label: "Team Access", icon: "group" },
  { to: "/settings/model-calibration", label: "Model Calibration", icon: "tune" },
  { to: "/settings/integrations", label: "Integrations", icon: "hub" },
  { to: "/settings/security", label: "Security", icon: "shield" },
] as const;

export function Sidebar() {
  const { user, logout, switchWorkspace } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const canRunSimulations = user?.role === "admin" || user?.role === "researcher";

  const navigationLinks = (onNavigate?: () => void) => (
    <ul className="flex flex-col gap-1">
      {NAV_ITEMS.map((item) => (
        <li key={item.to}>
          <NavLink
            to={item.to}
            onClick={onNavigate}
            className={({ isActive }) =>
              `flex items-center gap-3 rounded-lg px-3 py-2 transition ${
                isActive
                  ? "bg-primary-container/20 text-primary font-medium"
                  : "text-on-surface-variant hover:bg-surface-container"
              }`
            }
          >
            <span className="material-symbols-outlined text-lg">{item.icon}</span>
            {item.label}
          </NavLink>
        </li>
      ))}
    </ul>
  );

  const simulationAction = (onNavigate?: () => void) =>
    canRunSimulations ? (
      <NavLink
        to="/predictive"
        onClick={onNavigate}
        aria-label="New Simulation"
        className="mb-4 flex items-center justify-center gap-2 rounded-xl bg-primary py-2.5 text-on-primary font-medium hover:opacity-90 transition"
      >
        <span className="material-symbols-outlined text-lg">add</span>
        New Simulation
      </NavLink>
    ) : (
      <p className="mb-4 rounded-xl bg-surface-container p-3 text-xs text-on-surface-variant">
        Researcher access required to run simulations.
      </p>
    );

  return (
    <>
      <header className="md:hidden fixed inset-x-0 top-0 z-40 flex h-16 items-center justify-between border-b border-outline-variant/30 bg-surface-container-low px-4">
        <p className="font-headline text-xl text-primary">FlowSage</p>
        <button
          type="button"
          aria-label="Open navigation"
          aria-expanded={mobileOpen}
          onClick={() => setMobileOpen(true)}
          className="rounded-lg p-2 text-on-surface hover:bg-surface-container"
        >
          <span className="material-symbols-outlined">menu</span>
        </button>
      </header>
      {mobileOpen ? (
        <div className="md:hidden fixed inset-0 z-50 bg-black/40" role="presentation">
          <nav
            aria-label="Mobile navigation"
            className="h-full w-[min(20rem,85vw)] overflow-y-auto bg-surface-container-low p-5 text-sm shadow-xl"
          >
            <div className="mb-6 flex items-center justify-between">
              <p className="font-headline text-xl text-primary">FlowSage</p>
              <button
                type="button"
                aria-label="Close navigation"
                onClick={() => setMobileOpen(false)}
                className="rounded-lg p-2 text-on-surface hover:bg-surface-container"
              >
                <span className="material-symbols-outlined">close</span>
              </button>
            </div>
            {simulationAction(() => setMobileOpen(false))}
            {navigationLinks(() => setMobileOpen(false))}
            <div className="mt-6 border-t border-outline-variant/30 pt-4">
              <p className="text-xs text-on-surface-variant truncate">{user?.email}</p>
              <button
                type="button"
                onClick={() => void logout()}
                className="mt-2 text-xs font-medium text-primary hover:underline"
              >
                Sign out
              </button>
            </div>
          </nav>
        </div>
      ) : null}
      <nav className="hidden md:flex h-screen w-72 flex-col fixed left-0 top-0 bg-surface-container-low text-sm z-40">
        <div className="flex flex-col p-6 space-y-2 h-full">
        <div className="mb-6">
          <p className="font-headline text-xl text-primary">FlowSage</p>
          <p className="font-label text-xs uppercase tracking-wide text-on-surface-variant mt-1">
            Global UX Intelligence
          </p>
        </div>

        {simulationAction()}
        {navigationLinks()}

        <div className="mt-auto flex flex-col gap-3">
          {user && user.workspaces.length > 1 ? (
            <select
              value={user.workspace_id}
              onChange={(event) => {
                // Every route fetches its data on mount with no shared cache/
                // invalidation layer, so a full reload is the only way to get
                // every currently-mounted page to refetch under the new
                // workspace's session -- otherwise stale, wrong-tenant data
                // stays on screen after switching.
                void switchWorkspace(event.target.value).then(() => {
                  window.location.reload();
                });
              }}
              className="ghost-border rounded-lg px-3 py-2 bg-transparent text-sm"
              aria-label="Switch workspace"
            >
              {user.workspaces.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.name}
                </option>
              ))}
            </select>
          ) : null}
          <div className="ghost-border rounded-lg p-3">
            <p className="text-xs text-on-surface-variant truncate">{user?.email}</p>
            <button
              type="button"
              onClick={() => void logout()}
              className="mt-1 text-xs font-medium text-primary hover:underline"
            >
              Sign out
            </button>
          </div>
        </div>
        </div>
      </nav>
    </>
  );
}
