import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { Member, Role } from "../../lib/types";

const ROLE_OPTIONS: Role[] = ["admin", "researcher", "viewer"];

export function TeamSettingsPage() {
  const [members, setMembers] = useState<Member[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showInvite, setShowInvite] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<Role>("viewer");
  const [inviting, setInviting] = useState(false);

  function load() {
    api
      .getMembers()
      .then(setMembers)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load members.");
      });
  }

  useEffect(load, []);

  async function handleInvite() {
    setError(null);
    setInviting(true);
    try {
      await api.addMember({ email: inviteEmail, role: inviteRole });
      setInviteEmail("");
      setShowInvite(false);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add member.");
    } finally {
      setInviting(false);
    }
  }

  async function handleRoleChange(member: Member, role: Role) {
    setError(null);
    try {
      await api.updateMemberRole(member.id, role);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update role.");
    }
  }

  async function handleRemove(member: Member) {
    setError(null);
    try {
      await api.removeMember(member.id);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to remove member.");
    }
  }

  if (error !== null && members === null) {
    return <p className="text-error text-sm">{error}</p>;
  }

  if (members === null) {
    return <p className="text-on-surface-variant text-sm">Loading…</p>;
  }

  return (
    <div className="flex flex-col gap-8 max-w-3xl">
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div>
          <h1 className="font-headline text-3xl">Team Access</h1>
          <p className="text-on-surface-variant mt-1">
            {members.length} team member{members.length === 1 ? "" : "s"}.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowInvite(true)}
          className="rounded-lg bg-primary py-2.5 px-6 text-on-primary font-medium hover:opacity-90 transition"
        >
          Invite Member
        </button>
      </div>

      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}

      {showInvite ? (
        <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-4">
          <h2 className="font-headline text-xl">Invite Member</h2>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Email</span>
            <input
              type="email"
              value={inviteEmail}
              onChange={(event) => setInviteEmail(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2 bg-transparent"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Role</span>
            <select
              value={inviteRole}
              onChange={(event) => setInviteRole(event.target.value as Role)}
              className="ghost-border rounded-lg px-3 py-2 bg-transparent"
            >
              {ROLE_OPTIONS.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
          </label>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => void handleInvite()}
              disabled={inviting || inviteEmail.length === 0}
              className="rounded-lg bg-primary py-2 px-4 text-on-primary font-medium disabled:opacity-50"
            >
              {inviting ? "Adding…" : "Add to Workspace"}
            </button>
            <button
              type="button"
              onClick={() => setShowInvite(false)}
              className="rounded-lg ghost-border py-2 px-4 font-medium"
            >
              Cancel
            </button>
          </div>
        </section>
      ) : null}

      <section className="bg-surface-container-lowest rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="text-left text-on-surface-variant border-b border-outline-variant">
            <tr>
              <th className="px-6 py-3 font-medium">Team Member</th>
              <th className="px-6 py-3 font-medium">Role</th>
              <th className="px-6 py-3 font-medium">Joined</th>
              <th className="px-6 py-3 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {members.map((member) => {
              const isLastAdmin =
                member.role === "admin" && members.filter((m) => m.role === "admin").length === 1;
              return (
                <tr key={member.id} className="border-b border-outline-variant last:border-0">
                  <td className="px-6 py-3">{member.email}</td>
                  <td className="px-6 py-3">
                    <select
                      value={member.role}
                      onChange={(event) => void handleRoleChange(member, event.target.value as Role)}
                      disabled={isLastAdmin}
                      className="ghost-border rounded-lg px-2 py-1 bg-transparent disabled:opacity-50"
                    >
                      {ROLE_OPTIONS.map((role) => (
                        <option key={role} value={role}>
                          {role}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-6 py-3 text-on-surface-variant">
                    {new Date(member.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-6 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => void handleRemove(member)}
                      disabled={isLastAdmin}
                      className="text-error text-xs font-medium hover:underline disabled:opacity-50 disabled:no-underline"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}
