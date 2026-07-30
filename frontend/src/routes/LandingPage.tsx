import { useEffect } from "react";
import { Link } from "react-router-dom";

export function LandingPage() {
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

      {/* Calibration loop is called out in README.md as "the differentiator" --
          it gets a full-width featured treatment instead of sitting as an
          equal third column, breaking the generic 3-col icon/heading/paragraph
          grid that every LLM-generated feature section defaults to. */}
      <section className="mx-auto max-w-5xl px-6 py-16">
        <div className="rounded-xl bg-primary-container p-8 md:p-12">
          <h2 className="font-headline text-2xl text-on-primary-container">Calibration loop</h2>
          <p className="mt-3 max-w-2xl text-on-primary-container">
            Every pre-launch prediction is scored against post-launch reality, and
            miscalibrated personas retrain on real behavioral data over time — the loop that
            makes the other two engines converge on the truth.
          </p>
        </div>

        <div className="mt-8 grid gap-8 md:grid-cols-2">
          <div>
            <h2 className="font-headline text-xl text-on-background">Predictive engine</h2>
            <p className="mt-3 text-sm text-on-surface-variant">
              Multimodal LLM personas walk Figma exports, screenshots, or a live staging URL and
              produce a structured friction report before a single real user touches the flow.
            </p>
          </div>
          <div>
            <h2 className="font-headline text-xl text-on-background">Observational engine</h2>
            <p className="mt-3 text-sm text-on-surface-variant">
              Real user event streams become a temporal journey graph, surfacing drop-off,
              rage-loops, and backtracking automatically — no manual funnel definitions.
            </p>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-5xl px-6 py-16">
        <h2 className="font-headline text-3xl text-center text-on-background">Pricing</h2>
        <div className="mt-10 grid gap-8 md:grid-cols-3">
          <div className="rounded-xl bg-surface-container-lowest p-8 flex flex-col">
            <h3 className="font-headline text-lg text-on-background">Free</h3>
            <p className="mt-2 font-headline text-3xl text-on-background">$0</p>
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
          <div className="rounded-xl bg-primary-container p-8 flex flex-col md:-my-4 md:py-12">
            <h3 className="font-headline text-lg text-on-primary-container">Pro</h3>
            <p className="mt-2 font-headline text-3xl text-on-primary-container">
              $49<span className="font-sans text-base">/mo</span>
            </p>
            <ul className="mt-6 flex-1 space-y-2 text-sm text-on-primary-container">
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
            <p className="mt-2 font-headline text-3xl text-on-background">
              $199<span className="font-sans text-base">/mo</span>
            </p>
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
        <p>© {new Date().getFullYear()} FlowSage</p>
      </footer>
    </div>
  );
}
