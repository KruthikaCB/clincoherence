"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { denied, exact, request, useDashboardTask, uuid } from "./patient-selector";

type Consent = { consent_id: string; practitioner_id: string; status: "granted" | "revoked"; granted_at: string; revoked_at: string | null };
function consent(value: unknown): Consent {
  const row = exact(value, "consent_id,granted_at,practitioner_id,revoked_at,status");
  const date = (v: unknown): v is string => typeof v === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(v) && Number.isFinite(Date.parse(v));
  if (typeof row.consent_id !== "string" || !uuid.test(row.consent_id) || typeof row.practitioner_id !== "string" || !uuid.test(row.practitioner_id) || !date(row.granted_at) || !(row.status === "granted" ? row.revoked_at === null : row.status === "revoked" && date(row.revoked_at) && Date.parse(row.revoked_at) >= Date.parse(row.granted_at))) throw new Error();
  return row as Consent;
}
function relationships(value: unknown): Consent[] {
  if (!Array.isArray(value)) throw new Error();
  const rows = value.map(consent);
  if (new Set(rows.map(row => row.practitioner_id)).size !== rows.length) throw new Error();
  return rows;
}

function formatDate(iso: string): string {
  try { return new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return iso; }
}

export default function ConsentPanel({ token, onDenied, onMutation }: {
  token: string; onDenied: (status: number) => void; onMutation: () => void;
}) {
  const [rows, setRows] = useState<Consent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [review, setReview] = useState<string | null>(null);
  const [error, setError] = useState("");
  const { begin, busy } = useDashboardTask();

  const load = useCallback(async () => {
    const task = begin(); if (!task) return;
    setRows([]); setLoaded(false); setReview(null); setError("");
    try {
      const result = relationships(await request<unknown>("/patients/me/consents", token, undefined, task.signal));
      if (task.current()) { setRows(result); setLoaded(true); }
    } catch (cause) {
      if (task.current()) { setError("Consent relationships unavailable. Refresh explicitly before making changes."); denied(cause, onDenied); }
    } finally { task.done(); }
  }, [begin, token, onDenied]);

  useEffect(() => { void load(); }, [load]);

  async function change(id: string, revoke: boolean) {
    if (!loaded) return;
    const task = begin(); if (!task) return;
    setRows([]); setLoaded(false); setReview(null); setError("");
    onMutation();
    try {
      if (!uuid.test(id)) { setError("Enter a practitioner profile UUID, not an account UUID."); return; }
      const result = consent(await request<unknown>(revoke ? `/patients/me/consents/${id}/revoke` : "/patients/me/consents", token, revoke ? {} : { practitioner_id: id }, task.signal));
      if (result.practitioner_id !== id || result.status !== (revoke ? "revoked" : "granted")) throw new Error();
      if (!task.current()) return;
      const refreshed = relationships(await request<unknown>("/patients/me/consents", token, undefined, task.signal));
      if (task.current()) { setRows(refreshed); setLoaded(true); }
    } catch (cause) {
      if (task.current()) { setError("Consent action or refresh was not confirmed. No automatic retry — refresh explicitly before another change."); denied(cause, onDenied); }
    } finally { task.done(); }
  }

  function grant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const id = String(new FormData(event.currentTarget).get("practitioner")).trim().toLowerCase();
    event.currentTarget.reset(); void change(id, false);
  }

  return (
    <div style={{ padding: "1rem" }} role="region" aria-label="Practitioner consent">
      <div style={{ marginBottom: "1rem", padding: "0.75rem", background: "#fffbeb", border: "1px solid #fde68a", borderRadius: "var(--radius)", fontSize: "0.78rem", color: "#78350f", lineHeight: 1.6 }}>
        Only you can explicitly grant or revoke access to your record. Activation and invitations grant no consent.
        Revocation blocks subsequent requests but cannot recall information already viewed.
      </div>

      <div className="cc-card" style={{ marginBottom: "1rem" }}>
        <div className="cc-card-head"><h3 style={{ margin: 0 }}>Grant access</h3></div>
        <div className="cc-card-body">
          <div style={{ fontSize: "0.78rem", color: "#6b7280", marginBottom: "0.75rem" }}>
            Obtain the practitioner <strong>profile UUID</strong> through your trusted prototype contact.
          </div>
          <form onSubmit={grant} aria-busy={busy}>
            <label className="cc-label" style={{ marginBottom: "0.65rem" }}>
              Practitioner profile UUID
              <input name="practitioner" className="cc-input" autoComplete="off" required disabled={busy || !loaded} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />
            </label>
            <button className="cc-btn cc-btn-primary cc-btn-sm" disabled={busy || !loaded}>Grant access</button>
          </form>
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "0.5rem" }}>
        <h3 style={{ margin: 0, fontSize: "0.82rem" }}>Active relationships {loaded ? `· ${rows.length}` : ""}</h3>
        <button className="cc-btn cc-btn-ghost cc-btn-xs" onClick={() => void load()} disabled={busy}>
          Refresh
        </button>
      </div>

      {busy && <div style={{ fontSize: "0.78rem", color: "#6b7280", display: "flex", gap: "0.4rem", alignItems: "center" }}><span className="cc-spinner" /> Loading…</div>}
      {loaded && !rows.length && <div style={{ padding: "1rem", textAlign: "center", color: "#9ca3af", fontSize: "0.82rem" }}>No practitioner consent relationships.</div>}

      {rows.map(row => (
        <div key={row.consent_id} className="cc-consent-card">
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "0.5rem", marginBottom: "0.4rem" }}>
            <div>
              <span style={{ fontSize: "0.72rem", fontWeight: 700, padding: "0.1rem 0.45rem", borderRadius: 999, background: row.status === "granted" ? "#dcfce7" : "#fee2e2", color: row.status === "granted" ? "#15803d" : "#991b1b" }}>
                {row.status}
              </span>
            </div>
            {row.status === "granted" && (
              <button className="cc-btn cc-btn-danger cc-btn-xs" disabled={busy} onClick={() => setReview(row.practitioner_id)}>Revoke</button>
            )}
          </div>
          <div className="cc-consent-id" style={{ marginBottom: "0.3rem" }}>Practitioner: {row.practitioner_id}</div>
          <div style={{ fontSize: "0.72rem", color: "#6b7280" }}>
            Granted: {formatDate(row.granted_at)}
            {row.revoked_at && <span> · Revoked: {formatDate(row.revoked_at)}</span>}
          </div>
        </div>
      ))}

      {review && (
        <div style={{ padding: "0.75rem", background: "#fef2f2", border: "1px solid #fecaca", borderRadius: "var(--radius)", marginTop: "0.5rem" }} role="group" aria-label="Confirm revocation">
          <div style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: "0.5rem" }}>Revoke access for this practitioner?</div>
          <div className="cc-consent-id" style={{ marginBottom: "0.6rem" }}>{review}</div>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button className="cc-btn cc-btn-danger cc-btn-sm" disabled={busy} onClick={() => void change(review, true)}>Confirm revoke</button>
            <button className="cc-btn cc-btn-ghost cc-btn-sm" onClick={() => setReview(null)}>Cancel</button>
          </div>
        </div>
      )}

      {error && <div className="cc-banner error" role="alert" aria-label="Consent error" style={{ marginTop: "0.75rem" }}>{error}</div>}
    </div>
  );
}
