import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "./AuthContext";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background text-on-surface-variant">
        Loading…
      </div>
    );
  }

  if (user === null) {
    return <Navigate to="/login" replace />;
  }

  return children;
}
