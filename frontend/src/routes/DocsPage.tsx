import { Link } from "react-router-dom";

export function DocsPage() {
  return (
    <div className="bg-background text-on-background min-h-screen">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <Link to="/" className="font-headline text-2xl text-primary">
          FlowSage
        </Link>
        <Link
          to="/login"
          className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-on-primary hover:opacity-90 transition"
        >
          Log in
        </Link>
      </header>

      <div className="mx-auto max-w-5xl px-6 py-10 md:grid md:grid-cols-[200px_1fr] md:gap-12">
        <nav className="hidden md:block md:sticky md:top-10 md:self-start">
          <ul className="space-y-2 text-sm">
            <li>
              <a href="#quickstart" className="text-on-surface-variant hover:text-primary">
                Quickstart
              </a>
            </li>
            <li>
              <a href="#events" className="text-on-surface-variant hover:text-primary">
                Send events
              </a>
            </li>
            <li>
              <a href="#webhooks" className="text-on-surface-variant hover:text-primary">
                Webhooks
              </a>
            </li>
            <li>
              <a href="#reference" className="text-on-surface-variant hover:text-primary">
                Full reference
              </a>
            </li>
          </ul>
        </nav>

        <main className="flex flex-col gap-16 max-w-2xl">
          <section id="quickstart">
            <h1 className="font-headline text-3xl text-on-background">Quickstart</h1>
            <ol className="mt-4 list-decimal list-inside space-y-2 text-sm text-on-surface-variant">
              <li>
                <Link to="/login" className="text-primary underline">
                  Log in
                </Link>{" "}
                to your workspace.
              </li>
              <li>
                Create an API key under{" "}
                <Link to="/settings/integrations" className="text-primary underline">
                  Settings → Integrations
                </Link>
                .
              </li>
            </ol>
          </section>

          <section id="events">
            <h2 className="font-headline text-2xl text-on-background">Send events</h2>
            <p className="mt-3 text-sm text-on-surface-variant">
              <code className="rounded bg-surface-container px-1.5 py-0.5">POST /v1/events</code>,
              authenticated with an <code className="rounded bg-surface-container px-1.5 py-0.5">X-API-Key</code>{" "}
              header. Body is a JSON array of events:
            </p>
            <table className="mt-4 w-full text-sm">
              <thead>
                <tr className="text-left text-on-surface-variant">
                  <th className="pb-2">Field</th>
                  <th className="pb-2">Required</th>
                  <th className="pb-2">Notes</th>
                </tr>
              </thead>
              <tbody className="text-on-background">
                <tr>
                  <td className="py-1">
                    <code>session_id</code>
                  </td>
                  <td>yes</td>
                  <td></td>
                </tr>
                <tr>
                  <td className="py-1">
                    <code>screen</code>
                  </td>
                  <td>yes</td>
                  <td></td>
                </tr>
                <tr>
                  <td className="py-1">
                    <code>event</code>
                  </td>
                  <td>yes</td>
                  <td></td>
                </tr>
                <tr>
                  <td className="py-1">
                    <code>timestamp</code>
                  </td>
                  <td>yes</td>
                  <td>ISO 8601</td>
                </tr>
                <tr>
                  <td className="py-1">
                    <code>device</code>
                  </td>
                  <td>no</td>
                  <td>
                    defaults to <code>&quot;unknown&quot;</code>
                  </td>
                </tr>
                <tr>
                  <td className="py-1">
                    <code>cohort</code>
                  </td>
                  <td>no</td>
                  <td>
                    defaults to <code>&quot;unknown&quot;</code>
                  </td>
                </tr>
              </tbody>
            </table>
            <pre className="mt-4 overflow-x-auto rounded-lg bg-surface-container-lowest p-4 text-xs">
              {`curl -X POST https://your-domain/api/v1/events \\
  -H "X-API-Key: fs_..." \\
  -H "Content-Type: application/json" \\
  -d '[{"session_id": "s1", "screen": "landing", "event": "screen_view", "timestamp": "2026-07-30T12:00:00Z"}]'`}
            </pre>
            <p className="mt-3 text-sm text-on-surface-variant">
              Response: <code className="rounded bg-surface-container px-1.5 py-0.5">{`{"ingested": 1}`}</code>.
              Rate limit: <strong>120/minute</strong> per API key.
            </p>
          </section>

          <section id="webhooks">
            <h2 className="font-headline text-2xl text-on-background">Webhooks</h2>
            <p className="mt-3 text-sm text-on-surface-variant">
              Register an endpoint under{" "}
              <Link to="/settings/integrations" className="text-primary underline">
                Settings → Integrations
              </Link>
              . FlowSage delivers exactly one event type today,{" "}
              <code className="rounded bg-surface-container px-1.5 py-0.5">alert.triggered</code>, fired
              when a calibration or churn alert is due:
            </p>
            <pre className="mt-4 overflow-x-auto rounded-lg bg-surface-container-lowest p-4 text-xs">
              {`{
  "event": "alert.triggered",
  "data": {
    "calibration_alerts": [{"persona_name": "...", "screen": "...", "delta": 0.0}],
    "churn_alerts": [{"cohort": "...", "risk_score": 0.0, "top_reason": "..."}]
  }
}`}
            </pre>
            <p className="mt-4 text-sm text-on-surface-variant">
              Every delivery includes an{" "}
              <code className="rounded bg-surface-container px-1.5 py-0.5">X-FlowSage-Signature</code>{" "}
              header: <code className="rounded bg-surface-container px-1.5 py-0.5">sha256=&lt;hex&gt;</code>,
              an HMAC-SHA256 of the raw request body using your webhook&apos;s secret. Verify it before
              trusting the payload:
            </p>
            <pre className="mt-4 overflow-x-auto rounded-lg bg-surface-container-lowest p-4 text-xs">
              {`# Python
import hmac, hashlib

expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
if not hmac.compare_digest(expected, request.headers["X-FlowSage-Signature"]):
    raise ValueError("invalid signature")`}
            </pre>
            <pre className="mt-4 overflow-x-auto rounded-lg bg-surface-container-lowest p-4 text-xs">
              {`// Node
const crypto = require("crypto");

const expected = "sha256=" + crypto.createHmac("sha256", secret).update(rawBody).digest("hex");
if (!crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(req.headers["x-flowsage-signature"]))) {
  throw new Error("invalid signature");
}`}
            </pre>
          </section>

          <section id="reference">
            <h2 className="font-headline text-2xl text-on-background">Full API reference</h2>
            <p className="mt-3 text-sm text-on-surface-variant">
              Every endpoint, request/response schema, and try-it-out console lives at the
              interactive reference.
            </p>
            <a
              href="/api/docs"
              className="mt-4 inline-block rounded-lg bg-primary px-6 py-2.5 text-sm font-medium text-on-primary hover:opacity-90 transition"
            >
              Full API reference →
            </a>
          </section>
        </main>
      </div>
    </div>
  );
}
