"use client";

import { FormEvent, useState } from "react";
import { denied, exact, request, useDashboardTask, uuid } from "./patient-selector";

type InvitationState = { invitation_id: string; patient_id: string; status: "pending" | "consumed" | "revoked"; expires_at: string };
type Created = Omit<InvitationState, "status"> & { invitation_url: string; prototype_verification_code: string; prototype_notice: string };
function timestamp(value: unknown): value is string { return typeof value === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)); }
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
      if (!/^[a-z0-9][a-z0-9._+-]{0,63}@synthetic\.invalid$/.test(identity)) { setError("Enter the intended synthetic.invalid identity."); return; }
      const result = creation(await request<unknown>("/patient-invitations", token, { intended_identity: identity }, task.signal));
      if (task.current()) { setCreated(result); setKnown(result.invitation_id); setCurrent({ invitation_id: result.invitation_id, patient_id: result.patient_id, status: "pending", expires_at: result.expires_at }); }
    } catch (cause) {
      if (task.current()) { setError("Invitation creation was not confirmed; the outcome may be uncertain. No automatic retry. Secrets cannot be recovered after reload."); denied(cause, onDenied); }
    } finally { task.done(); }
  }
  async function inspect(revoke: boolean) {
    const task = begin(); if (!task) return;
    const id = known.trim().toLowerCase(); setError(""); setConfirm(false); setCurrent(null);
    // Status lookup cannot recover secrets; revocation attempts immediately hide them.
    if (revoke || created?.invitation_id !== id) setCreated(null);
    try {
      if (!uuid.test(id)) { setError("Enter a valid known invitation UUID."); return; }
      const result = state(await request<unknown>(`/patient-invitations/${id}${revoke ? "/revoke" : ""}`, token, revoke ? {} : undefined, task.signal), id);
      if (task.current()) { setCurrent(result); if (result.status !== "pending") setCreated(null); }
    } catch (cause) {
      if (task.current()) { setCreated(null); setError("Invitation status unavailable or action not confirmed. Refresh status explicitly; do not automatically repeat an uncertain mutation."); denied(cause, onDenied); }
    } finally { task.done(); }
  }
  return <section className="panel" aria-label="Practitioner invitations">
    <h2>Patient invitations</h2>
    <p>Prototype-only synthetic-user verification, not production identity assurance. Invitation issuance and activation never grant record access; the patient must separately consent.</p>
    <form onSubmit={create} aria-busy={busy}>
      <label>Invited synthetic identity<input name="identity" type="email" autoComplete="off" required maxLength={320} disabled={busy} /></label>
      <button disabled={busy}>Create patient invitation</button>
    </form>
    {created && <div>
      <p>{created.prototype_notice}</p>
      <section className="fact-card" aria-label="Invitation link"><h3>Share the invitation link</h3><label>Copyable invitation link<input readOnly autoComplete="off" value={created.invitation_url} onFocus={event => event.currentTarget.select()} /></label><p>Select and copy this link explicitly. It is not opened or sent automatically.</p></section>
      <section className="fact-card" aria-label="Separate verification code"><h3>Deliver the code separately</h3><details><summary>Reveal separate verification code</summary><label>Copyable separate verification code<input readOnly autoComplete="off" value={created.prototype_verification_code} onFocus={event => event.currentTarget.select()} /></label></details><p>Use a separate channel from the link. Both secrets disappear on sign-out, patient switching or reload.</p></section>
    </div>}
    <form onSubmit={event => { event.preventDefault(); void inspect(false); }}>
      <label>Known invitation UUID<input value={known} onChange={e => { setKnown(e.target.value); setCreated(null); setCurrent(null); setConfirm(false); }} autoComplete="off" required disabled={busy} /></label>
      <button className="secondary" disabled={busy}>Check invitation status</button>
    </form>
    <p>No invitation listing or secret recovery is available. Retain the non-secret invitation ID separately if later status lookup is needed.</p>
    {current && <div aria-live="polite"><p>Invitation status: {current.status}</p><p>Expires: <time dateTime={current.expires_at}>{current.expires_at}</time></p>
      <p>{current.status === "consumed" ? "Invitation redeemed; current eligibility and consent are still required for access." : current.status === "revoked" ? "Revoked invitations cannot activate an account." : "Pending status does not override expiration or grant clinical access."}</p>
      {current.status === "pending" && <button className="secondary" disabled={busy} onClick={() => setConfirm(true)}>Review invitation revocation</button>}
    </div>}
    {confirm && <div role="group" aria-label="Confirm invitation revocation"><p>Revoke this invitation? It will no longer permit activation.</p><div className="actions"><button disabled={busy} onClick={() => void inspect(true)}>Revoke invitation</button><button className="secondary" onClick={() => setConfirm(false)}>Cancel</button></div></div>}
    {busy && <p role="status">Working on invitation...</p>}
    {error && <p className="error" role="alert" aria-label="Invitation error">{error}</p>}
  </section>;
}
