import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { AuditLogEntry } from "../../lib/types";

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export function SecurityLogsPage() {
  const [entries, setEntries] = useState<AuditLogEntry[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getAuditLogs()
      .then((page) => {
        setEntries(page.entries);
        setNextCursor(page.next_cursor);
      })
      .catch((err: unknown) => setError(errorMessage(err, "Failed to load security logs.")));
  }, []);

  async function loadMore() {
    if (nextCursor === null) return;
    try {
      const page = await api.getAuditLogs({ cursor: nextCursor });
      setEntries((prev) => [...(prev ?? []), ...page.entries]);
      setNextCursor(page.next_cursor);
    } catch (err) {
      setError(errorMessage(err, "Failed to load more security logs."));
    }
  }

  return (
    <div className="flex flex-col gap-6 p-8">
      <div>
        <h1 className="font-headline text-2xl">Security Logs</h1>
        <p className="text-sm text-on-surface-variant mt-1">
          Audit trail of authentication, membership, and integration changes in this workspace.
        </p>
      </div>
      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}
      {entries === null ? (
        <p className="text-on-surface-variant text-sm">Loading…</p>
      ) : entries.length === 0 ? (
        <p className="text-on-surface-variant text-sm">No security events yet.</p>
      ) : (
        <div className="bg-surface-container-lowest rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-on-surface-variant border-b border-outline-variant">
                <th className="p-3 font-medium">Action</th>
                <th className="p-3 font-medium">Target</th>
                <th className="p-3 font-medium">IP Address</th>
                <th className="p-3 font-medium">Time</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id} className="border-b border-outline-variant last:border-0">
                  <td className="p-3">{entry.action}</td>
                  <td className="p-3">
                    {entry.target_type ? `${entry.target_type}:${entry.target_id ?? ""}` : "—"}
                  </td>
                  <td className="p-3">{entry.ip_address ?? "—"}</td>
                  <td className="p-3">{new Date(entry.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {nextCursor !== null ? (
        <button
          type="button"
          onClick={() => void loadMore()}
          className="self-start rounded-lg ghost-border py-2 px-4 text-sm font-medium"
        >
          Load more
        </button>
      ) : null}
    </div>
  );
}
