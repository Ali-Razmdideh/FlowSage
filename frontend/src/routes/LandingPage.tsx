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
