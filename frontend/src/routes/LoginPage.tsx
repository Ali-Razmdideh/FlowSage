import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../lib/api";

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (user !== null) {
    const from = (location.state as { from?: string } | null)?.from ?? "/dashboard";
    return <Navigate to={from} replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <p className="font-headline text-3xl text-primary text-center mb-1">FlowSage</p>
        <p className="font-label text-xs uppercase tracking-wide text-on-surface-variant text-center mb-8">
          Predictive &amp; Observed UX Intelligence
        </p>

        <form
          onSubmit={(event) => void handleSubmit(event)}
          className="bg-surface-container-lowest rounded-xl p-8 shadow-sm flex flex-col gap-4"
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Email</span>
            <input
              type="email"
              required
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2 focus:outline-2 focus:outline-primary"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Password</span>
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2 focus:outline-2 focus:outline-primary"
            />
          </label>

          {error !== null ? (
            <p role="alert" className="text-sm text-error">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={submitting}
            className="mt-2 rounded-lg bg-primary py-2.5 text-on-primary font-medium hover:opacity-90 transition disabled:opacity-50"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
