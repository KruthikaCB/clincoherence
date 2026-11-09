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
    onMutation(); // Invalidate in-flight clinical work before any mutation request.
    try {
      if (!uuid.test(id)) { setError("Enter a practitioner profile UUID, not an account UUID. Refresh relationships before trying again."); return; }
      const result = consent(await request<unknown>(revoke ? `/patients/me/consents/${id}/revoke` : "/patients/me/consents", token, revoke ? {} : { practitioner_id: id }, task.signal));
      if (result.practitioner_id !== id || result.status !== (revoke ? "revoked" : "granted")) throw new Error();
      if (!task.current()) return;
      const refreshed = relationships(await request<unknown>("/patients/me/consents", token, undefined, task.signal));
      if (task.current()) { setRows(refreshed); setLoaded(true); }
    } catch (cause) {
      if (task.current()) { setError("Consent action or refresh was not confirmed; the outcome may be uncertain. No automatic retry. Refresh relationships explicitly before another change."); denied(cause, onDenied); }
    } finally { task.done(); }
  }
  function grant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const id = String(new FormData(event.currentTarget).get("practitioner")).trim().toLowerCase();
    event.currentTarget.reset(); void change(id, false);
  }
  return <section className="panel" aria-label="Practitioner consent">
    <h2>Your practitioner consent</h2>
    <p>Only you can explicitly grant or revoke access to your record. Activation and invitations grant no consent. Revocation blocks subsequent protected requests but cannot recall information already viewed.</p>
    <p>Obtain and verify the practitioner PROFILE UUID through your trusted prototype contact. There is no practitioner directory or production credential verification here.</p>
    <form onSubmit={grant} aria-busy={busy}>
      <label>Practitioner profile UUID<input name="practitioner" autoComplete="off" required disabled={busy || !loaded} /></label>
      <button disabled={busy || !loaded}>Grant practitioner access</button>
    </form>
    <button className="secondary" onClick={() => void load()} disabled={busy}>Refresh consent relationships</button>
    {busy && <p role="status">Loading consent state...</p>}
    {loaded && !rows.length && <p>No practitioner consent relationships.</p>}
    {rows.map(row => <section className="fact-card" key={row.consent_id} aria-label="Consent relationship">
      <h3 className="break-all">Practitioner profile: {row.practitioner_id}</h3><p>Status: {row.status}</p>
      <p>Granted: <time dateTime={row.granted_at}>{row.granted_at}</time></p>
      {row.revoked_at && <p>Revoked: <time dateTime={row.revoked_at}>{row.revoked_at}</time></p>}
      {row.status === "granted" && <button className="secondary" disabled={busy} onClick={() => setReview(row.practitioner_id)}>Review access revocation</button>}
    </section>)}
    {review && <div role="group" aria-label="Confirm consent revocation"><p className="break-all">Revoke access for practitioner profile {review}?</p><div className="actions"><button disabled={busy} onClick={() => void change(review, true)}>Confirm revoke access</button><button className="secondary" onClick={() => setReview(null)}>Cancel</button></div></div>}
    {error && <p className="error" role="alert" aria-label="Consent error">{error}</p>}
  </section>;
}
