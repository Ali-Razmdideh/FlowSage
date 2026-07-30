# Landing Page Design Spec

**Phase:** 4, item 4 (hardening) — third of 3 sub-chunks (CI hardening ✓ → deploy ✓ → **landing/docs**, each its own spec/plan/worktree cycle). This spec covers the landing page only; the docs site is a separate follow-up spec.

## Problem

The frontend has no public-facing page at all. `App.tsx`'s `/` route unconditionally redirects to `/dashboard`, and `/dashboard` (like every other route) is behind `RequireAuth`. The only unauthenticated route is `/login`. FlowSage has no real "front door" — an unauthenticated visitor hitting the production domain gets bounced straight to a login form with no explanation of what the product is, who it's for, or what it costs.

## Design

### Architecture

`App.tsx`'s `/` route changes from `<Navigate to="/dashboard" replace />` to a new `HomeRoute` component:

```tsx
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
```

This mirrors `RequireAuth`'s existing loading-state pattern exactly (`frontend/src/auth/RequireAuth.tsx`) so there's no new loading-UI convention introduced. `/login` and the catch-all `*` route are unchanged. `HomeRoute` is declared directly in `App.tsx` (not its own file) since it's a 15-line routing shim, not a reusable component — `LandingPage` is the actual content and gets its own file.

### Content — `frontend/src/routes/LandingPage.tsx`

Uses the existing Alexandria design tokens already established by `LoginPage.tsx`/`Shell.tsx`/`design-hifi-prototypes/alexandria/DESIGN.md` — `font-headline` (Noto Serif), `font-label` (Public Sans), `bg-background`/`text-on-background`, `ghost-border`, gradient `primary` → `primary-container` CTAs, no 1px borders, minimum `sm` corner roundness. No new design tokens or fonts introduced. Actual pixel-level visual craft (spacing rhythm, gradient treatment, responsive breakpoints) is a `hallmark`-skill pass at implementation time, layered on top of this structural spec — this section defines *what* ships, not exact Tailwind classes.

Four sections, one file, in order:

1. **Hero** — wordmark ("FlowSage"), one-line positioning pulled from `README.md`'s framing ("Predictive & Observed UX Intelligence Platform" / "predicting friction before launch, and measuring it after"), primary CTA button → `/login`.
2. **Three pillars** — one card each for Predictive engine, Observational engine, Calibration loop, condensed from `README.md`'s existing feature bullets (no new copywriting invented beyond trimming to card length).
3. **Pricing** — three cards, numbers sourced from `backend/src/flowsage_backend/billing.py`'s `TIER_LIMITS` (the single source of truth already enforced server-side) plus the `$49`/`$199` monthly prices from `docs/superpowers/specs/2026-07-27-stripe-billing-design.md` (not currently represented anywhere in code, since `TierLimits` only carries usage caps, not price — this page is the first place a dollar figure appears in the codebase):
   - Free: 1 seat, 1,000 events/mo, 5 runs/mo
   - Pro, $49/mo: 10 seats, 50,000 events/mo, 100 runs/mo
   - Team, $199/mo: unlimited seats, 500,000 events/mo, 1,000 runs/mo
   Each card's CTA → `/login` (no self-serve signup exists yet — out of scope, see below).
4. **Footer** — wordmark + `© {current year} FlowSage`, nothing else (no social links, no sitemap — none exist yet).

`<title>` and a `<meta name="description">` are set via a `useEffect` in `LandingPage` (no `react-helmet`-style dependency — this codebase has no precedent for one, and one tag doesn't justify adding a new dependency).

### Testing

- `frontend/src/routes/LandingPage.test.tsx` — renders and asserts the CTA link's `href` is `/login` (mirrors the existing test shape in `LoginPage.test.tsx`).
- `frontend/src/App.test.tsx` (new, or added to an existing App-level test file if one exists) — two cases for `HomeRoute`: authenticated user renders at `/dashboard` (redirect happened), unauthenticated user sees `LandingPage` content at `/`.

### Out of scope

- Signup/registration flow (CTA links to existing `/login` only, per explicit decision this session).
- The docs site (separate spec, next chunk).
- SEO beyond `<title>`/`<meta description>` (no sitemap, no structured data, no OG tags).
- Any change to `/login`, `RequireAuth`, or any authenticated route.

## Verification

- `npm run typecheck && npm test && npm run build` in `frontend/` all clean.
- Manual: visit `/` logged out → see `LandingPage`; log in → visiting `/` redirects to `/dashboard`; visiting `/login` while already logged in still redirects per its own existing logic (unchanged).
- Pricing card numbers cross-checked against `backend/src/flowsage_backend/billing.py`'s `TIER_LIMITS` at implementation time, not copied from this spec verbatim, in case the tier limits have changed since this doc was written.
