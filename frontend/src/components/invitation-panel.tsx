"use client";

import { FormEvent, useState } from "react";
import { denied, exact, request, useDashboardTask, uuid } from "./patient-selector";

type InvitationState = { invitation_id: string; patient_id: string; status: "pending" | "consumed" | "revoked"; expires_at: string };
type Created = Omit<InvitationState, "status"> & { invitation_url: string; prototype_verification_code: string; prototype_notice: string };

function timestamp(value: unknown): value is string {
  return typeof value === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
}
function state(value: unknown, id: string): InvitationState {
  const row = exact(value, "expires_at,invitation_id,patient_id,status");
  if (row.invitation_id !== id || typeof row.patient_id !== "string" || !uuid.test(row.patient_id) || !timestamp(row.expires_at) || typeof row.status !== "string" || !["pending", "consumed", "revoked"].includes(row.status)) throw new Error();
  return row as InvitationState;
}
function creation(value: unknown): Created {
  const row = exact(value, "expires_at,invitation_id,invitation_url,patient_id,prototype_notice,prototype_verification_code");
  if (typeof row.invitation_id !== "string" || !uuid.test(row.invitation_id) || typeof row.patient_id !== "string" || !uuid.test(row.patient_id) || !timestamp(row.expires_at) || typeof row.prototype_notice !== "string" || !row.prototype_notice.trim() || row.prototype_notice.length > 2000 || typeof row.prototype_verification_code !== "string" || !/^[A-Za-z0-9_-]{32}$/.test(row.prototype_verification_code) || typeof row.invitation_url !== "string") throw new Error();
  const url = new URL(row.invitation_url);
  const fragment = new URLSearchParams(url.hash.slice(1));
  if (!(url.protocol === "https:" || (url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))) || url.username || url.password || url.pathname !== "/activate" || url.search || Array.from(fragment.keys()).join() !== "token" || !/^[A-Za-z0-9_-]{43}$/.test(fragment.get("token") ?? "")) throw new Error();
  return row as Created;
}

function formatDate(iso: string): string {
  try { return new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return iso; }
}

const statusColors: Record<InvitationState["status"], { bg: string; color: string }> = {
  pending:  { bg: "#fef3c7", color: "#92400e" },
  consumed: { bg: "#dcfce7", color: "#15803d" },
  revoked:  { bg: "#fee2e2", color: "#991b1b" },
};

export default function InvitationPanel({ token, onDenied }: { token: string; onDenied: (status: number) => void }) {
  const [created, setCreated] = useState<Created | null>(null);
  const [known, setKnown] = useState("");
  const [current, setCurrent] = useState<InvitationState | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState("");
  const { begin, busy } = useDashboardTask();

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const task = begin(); if (!task) return;
    const identity = String(new FormData(event.currentTarget).get("identity")).trim().toLowerCase();
    event.currentTarget.reset(); setCreated(null); setCurrent(null); setKnown(""); setConfirm(false); setError("");
    try {
      if (!/^[a-z0-9][a-z0-9._+-]{0,63}@synthetic\.invalid$/.test(identity)) { setError("Enter a valid synthetic.invalid identity."); return; }
      const result = creation(await request<unknown>("/patient-invitations", token, { intended_identity: identity }, task.signal));
      if (task.current()) { setCreated(result); setKnown(result.invitation_id); setCurrent({ invitation_id: result.invitation_id, patient_id: result.patient_id, status: "pending", expires_at: result.expires_at }); }
    } catch (cause) {
      if (task.current()) { setError("Invitation creation was not confirmed. No automatic retry — secrets cannot be recovered after reload."); denied(cause, onDenied); }
    } finally { task.done(); }
  }

  async function inspect(revoke: boolean) {
    const task = begin(); if (!task) return;
    const id = known.trim().toLowerCase(); setError(""); setConfirm(false); setCurrent(null);
    if (revoke || created?.invitation_id !== id) setCreated(null);
    try {
      if (!uuid.test(id)) { setError("Enter a valid invitation UUID."); return; }
      const result = state(await request<unknown>(`/patient-invitations/${id}${revoke ? "/revoke" : ""}`, token, revoke ? {} : undefined, task.signal), id);
      if (task.current()) { setCurrent(result); if (result.status !== "pending") setCreated(null); }
    } catch (cause) {
      if (task.current()) { setCreated(null); setError("Invitation status unavailable or action not confirmed. Do not automatically repeat an uncertain mutation."); denied(cause, onDenied); }
    } finally { task.done(); }
  }

  return (
    <div style={{ padding: "1rem" }} role="region" aria-label="Patient invitations">
      <div style={{ marginBottom: "1rem", padding: "0.65rem 0.75rem", background: "#fffbeb", border: "1px solid #fde68a", borderRadius: "var(--radius)", fontSize: "0.78rem", color: "#78350f", lineHeight: 1.5 }}>
        Prototype-only synthetic-user verification, not production identity assurance.
        Invitation issuance and activation never grant record access — the patient must separately consent.
      </div>

      {/* Create invitation */}
      <div className="cc-card" style={{ marginBottom: "1rem" }}>
        <div className="cc-card-head"><h3 style={{ margin: 0 }}>Create invitation</h3></div>
        <div className="cc-card-body">
          <form onSubmit={create} aria-busy={busy}>
            <label className="cc-label" style={{ marginBottom: "0.65rem" }}>
              Intended synthetic identity
              <input name="identity" type="email" className="cc-input" autoComplete="off" required maxLength={320} disabled={busy} placeholder="patient@synthetic.invalid" />
            </label>
            <button className="cc-btn cc-btn-primary cc-btn-sm" disabled={busy}>Create invitation</button>
          </form>
        </div>
      </div>

      {/* Created invitation secrets */}
      {created && (
        <div className="cc-card" role="region" aria-label="Invitation link" style={{ marginBottom: "1rem", borderColor: "#86efac" }}>
          <div className="cc-card-head" style={{ background: "#f0fdf4" }}>
            <h3 style={{ margin: 0, color: "#15803d" }}>Invitation created</h3>
            <span style={{ fontSize: "0.72rem", color: "#6b7280" }}>Secrets disappear on page reload</span>
          </div>
          <div className="cc-card-body">
            <p style={{ fontSize: "0.78rem", color: "#374151", marginBottom: "0.75rem" }}>{created.prototype_notice}</p>
            <label className="cc-label" style={{ marginBottom: "0.75rem" }}>
              Invitation link (share with patient)
              <input readOnly className="cc-input" autoComplete="off" value={created.invitation_url} onFocus={e => e.currentTarget.select()} style={{ fontSize: "0.78rem" }} />
            </label>
            <details>
              <summary style={{ fontSize: "0.78rem", cursor: "pointer", color: "#6b7280", marginBottom: "0.4rem" }}>Reveal separate verification code</summary>
              <label className="cc-label">
                Verification code (deliver via separate channel)
                <input readOnly className="cc-input" autoComplete="off" value={created.prototype_verification_code} onFocus={e => e.currentTarget.select()} style={{ fontFamily: "monospace" }} />
              </label>
              <div style={{ fontSize: "0.72rem", color: "#9ca3af", marginTop: "0.3rem" }}>Use a different channel from the link.</div>
            </details>
          </div>
        </div>
      )}

      {/* Check / revoke existing */}
      <div className="cc-card">
        <div className="cc-card-head"><h3 style={{ margin: 0 }}>Check or revoke invitation</h3></div>
        <div className="cc-card-body">
          <form onSubmit={e => { e.preventDefault(); void inspect(false); }}>
            <label className="cc-label" style={{ marginBottom: "0.65rem" }}>
              Known invitation UUID
              <input
                className="cc-input"
                value={known}
                onChange={e => { setKnown(e.target.value); setCreated(null); setCurrent(null); setConfirm(false); }}
                autoComplete="off" required disabled={busy}
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
              />
            </label>
            <button className="cc-btn cc-btn-ghost cc-btn-sm" disabled={busy}>Check status</button>
          </form>
          <div style={{ fontSize: "0.72rem", color: "#9ca3af", marginTop: "0.4rem" }}>
            No invitation listing or secret recovery is available.
          </div>

          {current && (
            <div style={{ marginTop: "0.75rem" }} aria-live="polite">
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.4rem" }}>
                <span style={{ fontSize: "0.75rem", fontWeight: 700, padding: "0.15rem 0.5rem", borderRadius: 999, ...statusColors[current.status] }}>
                  {current.status}
                </span>
                <span style={{ fontSize: "0.72rem", color: "#6b7280" }}>Expires: {formatDate(current.expires_at)}</span>
              </div>
              <div style={{ fontSize: "0.75rem", color: "#6b7280", marginBottom: "0.5rem" }}>
                {current.status === "consumed" ? "Invitation redeemed. Eligibility and consent still required for access."
                  : current.status === "revoked" ? "Revoked invitations cannot activate an account."
                  : "Pending status does not override expiration or grant clinical access."}
              </div>
              {current.status === "pending" && (
                <button className="cc-btn cc-btn-danger cc-btn-sm" disabled={busy} onClick={() => setConfirm(true)}>Revoke invitation</button>
              )}
            </div>
          )}

          {confirm && (
            <div style={{ marginTop: "0.65rem", padding: "0.65rem", background: "#fef2f2", border: "1px solid #fecaca", borderRadius: "var(--radius)" }} role="group">
              <div style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: "0.5rem" }}>Revoke this invitation?</div>
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <button className="cc-btn cc-btn-danger cc-btn-sm" disabled={busy} onClick={() => void inspect(true)}>Confirm revoke</button>
                <button className="cc-btn cc-btn-ghost cc-btn-sm" onClick={() => setConfirm(false)}>Cancel</button>
              </div>
            </div>
          )}
        </div>
      </div>

      {busy && <div style={{ fontSize: "0.78rem", color: "#6b7280", display: "flex", gap: "0.4rem", alignItems: "center", marginTop: "0.5rem" }}><span className="cc-spinner" /> Working…</div>}
      {error && <div className="cc-banner error" role="alert" aria-label="Invitation error" style={{ marginTop: "0.75rem" }}>{error}</div>}
    </div>
  );
}
