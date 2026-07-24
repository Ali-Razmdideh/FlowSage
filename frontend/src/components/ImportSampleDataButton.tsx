import { useState } from "react";
import { api, ApiError } from "../lib/api";

export function ImportSampleDataButton({ onImported }: { onImported?: () => void }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleClick() {
    setLoading(true);
    setError(null);
    try {
      await api.importSampleData();
      onImported?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to import sample data.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col items-center gap-2">
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={loading}
        className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-on-primary hover:opacity-90 transition disabled:opacity-50"
      >
        <span className="material-symbols-outlined text-lg">download</span>
        {loading ? "Importing…" : "Import Sample Data"}
      </button>
      {error !== null ? (
        <p role="alert" className="text-xs text-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
