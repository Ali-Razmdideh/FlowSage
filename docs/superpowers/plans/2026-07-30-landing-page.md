# Landing Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give FlowSage a real public front door — an unauthenticated `/` landing page (hero, three pillars, pricing, footer) instead of the current unconditional redirect to the authed dashboard — per `docs/superpowers/specs/2026-07-30-landing-page-design.md`.

**Architecture:** `App.tsx`'s `/` route becomes a `HomeRoute` component that mirrors `RequireAuth`'s existing loading-state pattern: authenticated users still land on `/dashboard`, unauthenticated users see the new `LandingPage` (`frontend/src/routes/LandingPage.tsx`). No backend changes — this is frontend-only.

**Tech Stack:** React 19 + TypeScript strict, React Router, Tailwind v4 (`@theme` tokens already defined in `frontend/src/index.css`), Vitest + Testing Library.

## Global Constraints

- Reuse existing Alexandria design tokens (`font-headline`, `font-label`, `bg-background`, `ghost-border`, `primary`/`primary-container` gradient CTAs) — no new fonts, colors, or design tokens.
- No self-serve signup exists — every CTA on this page links to `/login`, not a signup form.
- Pricing card numbers must match `backend/src/flowsage_backend/billing.py`'s `TIER_LIMITS` exactly (re-check at implementation time, don't trust the spec's copy blindly in case limits changed).
- `npx oxlint`, `npm run typecheck`, `npm test`, `npm run build` must all stay clean (this repo's `rtk` CLI wrapper has a known false-failure on plain oxlint output — use `npx oxlint` directly, per project memory).

---

### Task 1: `HomeRoute` routing + `LandingPage` hero

**Files:**
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/routes/LandingPage.tsx`
- Create: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: `useAuth()` from `frontend/src/auth/AuthContext.tsx` (same `{ user, loading }` shape `RequireAuth` already consumes).
- Produces: `HomeRoute` (declared inline in `App.tsx`, not exported — routing shim only), `LandingPage` exported from `frontend/src/routes/LandingPage.tsx` for Task 2 to extend.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/App.test.tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { AuthContext, type AuthState } from "./auth/AuthContext";

function renderAppAt(path: string, user: AuthState["user"]) {
  const state: AuthState = {
    user,
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    switchWorkspace: vi.fn(),
  };
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthContext.Provider value={state}>
        <App />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

describe("HomeRoute", () => {
  it("shows the landing page at / when logged out", () => {
    renderAppAt("/", null);
    expect(screen.getAllByRole("link", { name: /log in/i }).length).toBeGreaterThan(0);
  });

  it("redirects authenticated users away from / to the dashboard", () => {
    renderAppAt("/", {
      id: "u1",
      email: "admin@flowsage.dev",
      created_at: "now",
      workspace_id: "w1",
      role: "admin",
      workspaces: [{ id: "w1", name: "Workspace 1" }],
    });
    expect(screen.queryByRole("link", { name: /log in/i })).not.toBeInTheDocument();
  });
});
```

Note: `App.tsx` currently wraps its `<Routes>` in its own `<AuthProvider>` (see the file as it exists today) — that wrapper must be removed from `App` and hoisted to `main.tsx` for this test to be able to inject a mock `AuthContext.Provider` around `App`. Check `frontend/src/main.tsx` first; if `AuthProvider` isn't already there, move it there as part of Step 3 below (one-line change, not a new architectural decision — `main.tsx` is the correct place for app-wide providers and every other provider-less test in this codebase already assumes `App` has no built-in providers of its own).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- App.test.tsx`
Expected: FAIL — either `LandingPage`/`HomeRoute` doesn't exist yet, or (if `AuthProvider` is still hard-coded inside `App`) the injected mock `AuthContext.Provider` has no effect because the real `AuthProvider` shadows it.

- [ ] **Step 3: Implement `HomeRoute` + hero-only `LandingPage`**

If `frontend/src/main.tsx` doesn't already wrap `<App />` in `<AuthProvider>`, move the wrapping there now (remove it from `App.tsx`).

```tsx
// frontend/src/routes/LandingPage.tsx
import { Link } from "react-router-dom";

export function LandingPage() {
  return (
    <div className="bg-background text-on-background">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <span className="font-headline text-2xl text-primary">FlowSage</span>
        <Link
          to="/login"
          className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-on-primary hover:opacity-90 transition"
        >
          Log in
        </Link>
      </header>

      <section className="mx-auto max-w-3xl px-6 py-24 text-center">
        <h1 className="font-headline text-5xl leading-tight text-on-background">
          Predictive &amp; Observed UX Intelligence
        </h1>
        <p className="mt-6 text-lg text-on-surface-variant">
          FlowSage predicts where users will struggle before launch, measures where they
          actually struggle after, and converges the two over time.
        </p>
        <Link
          to="/login"
          className="mt-10 inline-block rounded-lg bg-primary px-8 py-3 font-medium text-on-primary hover:opacity-90 transition"
        >
          Log in
        </Link>
      </section>
    </div>
  );
}
```

```tsx
// frontend/src/App.tsx -- replace the `/` Route and add HomeRoute
import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { Shell } from "./components/Shell";
import { LandingPage } from "./routes/LandingPage";
import { LoginPage } from "./routes/LoginPage";
// ...(other existing route imports unchanged)

function HomeRoute() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background text-on-surface-variant">
        Loading…
      </div>
    );
  }

  if (user !== null) {
    return <Navigate to="/dashboard" replace />;
  }

  return <LandingPage />;
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<HomeRoute />} />
      <Route path="/login" element={<LoginPage />} />
      {/* ...(existing RequireAuth-wrapped Route block unchanged)... */}
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
```

Remove the top-level `<AuthProvider>` wrapper from `App`'s returned JSX (it now lives in `main.tsx`) — keep every other existing route exactly as-is, only the `/` route and the removed `AuthProvider` wrapper change.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- App.test.tsx`
Expected: PASS (both cases)

- [ ] **Step 5: Run the full existing frontend test suite to confirm nothing else broke**

Run: `cd frontend && npm test`
Expected: PASS — moving `AuthProvider` to `main.tsx` must not break any other test file that renders `<App />` or relies on a provider being present inside it.

- [ ] **Step 6: Lint/typecheck**

Run: `cd frontend && npx oxlint && npm run typecheck`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/routes/LandingPage.tsx frontend/src/main.tsx
git commit -m "feat: add public landing page route at /"
```

---

### Task 2: Pillars + pricing + footer sections

**Files:**
- Modify: `frontend/src/routes/LandingPage.tsx`
- Create: `frontend/src/routes/LandingPage.test.tsx`

**Interfaces:**
- Consumes: `LandingPage` (Task 1) — this task extends the same file/component, no new exports.
- Consumes (data, not code): tier limits from `backend/src/flowsage_backend/billing.py`'s `TIER_LIMITS` — re-read that file at implementation time and use its live values, not the numbers quoted in the spec, in case they've drifted.

- [ ] **Step 1: Invoke the hallmark skill for visual design guidance**

Before writing the pillars/pricing/footer markup, invoke the `hallmark` skill (greenfield landing page design guidance) so the section layout, spacing rhythm, and card treatment read as intentional rather than generic AI-template output. Apply its guidance within the constraints already locked in the spec (Alexandria tokens only, no new fonts/colors) — hallmark should inform layout and composition choices, not override the established design system.

- [ ] **Step 2: Write the failing test**

```tsx
// frontend/src/routes/LandingPage.test.tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { LandingPage } from "./LandingPage";

describe("LandingPage", () => {
  it("renders all three pillar headings", () => {
    render(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /predictive engine/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /observational engine/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /calibration loop/i })).toBeInTheDocument();
  });

  it("renders all three pricing tiers with correct limits and links each to /login", () => {
    render(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>,
    );
    expect(screen.getByText(/1,000 events\/mo/i)).toBeInTheDocument();
    expect(screen.getByText(/\$49/)).toBeInTheDocument();
    expect(screen.getByText(/50,000 events\/mo/i)).toBeInTheDocument();
    expect(screen.getByText(/\$199/)).toBeInTheDocument();
    expect(screen.getByText(/500,000 events\/mo/i)).toBeInTheDocument();

    const loginLinks = screen.getAllByRole("link", { name: /log in/i });
    expect(loginLinks.length).toBeGreaterThanOrEqual(4); // header + hero + 3 pricing cards, minus dedupe by name
    for (const link of loginLinks) {
      expect(link).toHaveAttribute("href", "/login");
    }
  });

  it("renders a footer with the current year", () => {
    render(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>,
    );
    expect(screen.getByText(new RegExp(`© ${new Date().getFullYear()} FlowSage`))).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npm test -- LandingPage.test.tsx`
Expected: FAIL — pillar/pricing/footer content doesn't exist yet (only the hero from Task 1 does).

- [ ] **Step 4: Extend `LandingPage.tsx` with the three sections**

Add below the hero `<section>` from Task 1, inside the same root `<div>`:

```tsx
      <section className="mx-auto max-w-5xl px-6 py-16 grid gap-8 md:grid-cols-3">
        <div className="rounded-xl bg-surface-container-lowest p-8">
          <h2 className="font-headline text-xl text-on-background">Predictive engine</h2>
          <p className="mt-3 text-sm text-on-surface-variant">
            Multimodal LLM personas walk Figma exports, screenshots, or a live staging URL and
            produce a structured friction report before a single real user touches the flow.
          </p>
        </div>
        <div className="rounded-xl bg-surface-container-lowest p-8">
          <h2 className="font-headline text-xl text-on-background">Observational engine</h2>
          <p className="mt-3 text-sm text-on-surface-variant">
            Real user event streams become a temporal journey graph, surfacing drop-off,
            rage-loops, and backtracking automatically — no manual funnel definitions.
          </p>
        </div>
        <div className="rounded-xl bg-surface-container-lowest p-8">
          <h2 className="font-headline text-xl text-on-background">Calibration loop</h2>
          <p className="mt-3 text-sm text-on-surface-variant">
            Every pre-launch prediction is scored against post-launch reality, and
            miscalibrated personas retrain on real behavioral data over time.
          </p>
        </div>
      </section>

      <section className="mx-auto max-w-5xl px-6 py-16">
        <h2 className="font-headline text-3xl text-center text-on-background">Pricing</h2>
        <div className="mt-10 grid gap-8 md:grid-cols-3">
          <div className="rounded-xl bg-surface-container-lowest p-8 flex flex-col">
            <h3 className="font-headline text-lg text-on-background">Free</h3>
            <p className="mt-2 text-3xl font-headline text-on-background">$0</p>
            <ul className="mt-6 flex-1 space-y-2 text-sm text-on-surface-variant">
              <li>1 seat</li>
              <li>1,000 events/mo</li>
              <li>5 simulation runs/mo</li>
            </ul>
            <Link
              to="/login"
              className="mt-6 rounded-lg bg-surface-container px-4 py-2 text-center text-sm font-medium text-primary hover:opacity-90 transition"
            >
              Log in
            </Link>
          </div>
          <div className="rounded-xl bg-surface-container-lowest p-8 flex flex-col">
            <h3 className="font-headline text-lg text-on-background">Pro</h3>
            <p className="mt-2 text-3xl font-headline text-on-background">$49<span className="text-base font-sans">/mo</span></p>
            <ul className="mt-6 flex-1 space-y-2 text-sm text-on-surface-variant">
              <li>10 seats</li>
              <li>50,000 events/mo</li>
              <li>100 simulation runs/mo</li>
            </ul>
            <Link
              to="/login"
              className="mt-6 rounded-lg bg-primary px-4 py-2 text-center text-sm font-medium text-on-primary hover:opacity-90 transition"
            >
              Log in
            </Link>
          </div>
          <div className="rounded-xl bg-surface-container-lowest p-8 flex flex-col">
            <h3 className="font-headline text-lg text-on-background">Team</h3>
            <p className="mt-2 text-3xl font-headline text-on-background">$199<span className="text-base font-sans">/mo</span></p>
            <ul className="mt-6 flex-1 space-y-2 text-sm text-on-surface-variant">
              <li>Unlimited seats</li>
              <li>500,000 events/mo</li>
              <li>1,000 simulation runs/mo</li>
            </ul>
            <Link
              to="/login"
              className="mt-6 rounded-lg bg-surface-container px-4 py-2 text-center text-sm font-medium text-primary hover:opacity-90 transition"
            >
              Log in
            </Link>
          </div>
        </div>
      </section>

      <footer className="mx-auto max-w-6xl px-6 py-10 text-center text-xs text-on-surface-variant">
        <p>
          © {new Date().getFullYear()} FlowSage
        </p>
      </footer>
```

Apply whatever spacing/composition refinements the hallmark-skill pass (Step 1) recommended within this markup, as long as the token usage, text content, and every `/login` link target stay exactly as specified above — those are what Step 2's test asserts on.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npm test -- LandingPage.test.tsx`
Expected: PASS

- [ ] **Step 6: Lint/typecheck**

Run: `cd frontend && npx oxlint && npm run typecheck`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/LandingPage.tsx frontend/src/routes/LandingPage.test.tsx
git commit -m "feat: add pillars, pricing, and footer sections to landing page"
```

---

### Task 3: Page metadata + full verification

**Files:**
- Modify: `frontend/src/routes/LandingPage.tsx`

**Interfaces:** none new — this task only adds a `useEffect` side effect to the existing component.

- [ ] **Step 1: Add title/meta description**

Add to `LandingPage.tsx`:

```tsx
import { useEffect } from "react";
```

Inside the `LandingPage` function body, before the `return`:

```tsx
  useEffect(() => {
    document.title = "FlowSage — Predictive & Observed UX Intelligence";
    const meta = document.querySelector('meta[name="description"]') ?? document.createElement("meta");
    meta.setAttribute("name", "description");
    meta.setAttribute(
      "content",
      "FlowSage predicts where users will struggle before launch and measures where they actually struggle after, converging the two over time.",
    );
    if (meta.parentElement === null) {
      document.head.appendChild(meta);
    }
  }, []);
```

- [ ] **Step 2: Full frontend verification**

Run: `cd frontend && npx oxlint && npm run typecheck && npm test && npm run build`
Expected: all clean

- [ ] **Step 3: Manual verification against the running local stack**

The deploy from the previous VPS-deploy chunk is already running on this machine (`DOMAIN=127.0.0.1`, `docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml`). Rebuild and restart just the frontend service to pick up this change:

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml --env-file infra/.env.prod up -d --build frontend
```

Then use a real browser (Playwright or Chrome DevTools MCP) to navigate to `https://127.0.0.1/` logged out and take a screenshot, confirming: hero renders, all three pillar cards render, all three pricing cards render with correct numbers, footer renders, and clicking "Log in" navigates to `/login`. `curl`/`openssl s_client` already proved the TLS chain works in the deploy chunk — this step is specifically about confirming the *visual* result, which only a real browser can do.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/LandingPage.tsx
git commit -m "feat: add title/meta description to landing page"
```

---

## Self-Review Notes (for the plan author, not a task)

- Spec coverage: `HomeRoute` routing (Task 1), hero (Task 1), pillars/pricing/footer (Task 2), meta tags (Task 3), manual + automated verification (Task 3) — all spec deliverables covered.
- Spec's "out of scope" list (signup flow, docs site, SEO beyond title/description, changes to `/login`/`RequireAuth`) — confirmed no task touches any of them.
- The `AuthProvider`-relocation detail in Task 1 was discovered while writing this plan (current `App.tsx` hard-codes it), not in the original spec — flagged explicitly in Task 1 rather than silently assumed, since it's a real structural change beyond "add a route."
