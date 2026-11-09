"use client";

import { FormEvent, useRef, useState } from "react";
import FactCard, { FactDetails } from "./fact-card";
import { Account, AcceptedFact, attribution, Proposal } from "./clinical-types";
import VerifiedRecords from "./verified-records";

class ApiError extends Error { constructor(public status: number) { super("Request failed"); } }
async function request<T>(path: string, token?: string, body?: object): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store", credentials: "omit", redirect: "error", referrerPolicy: "no-referrer",
  });
  if (!response.ok) throw new ApiError(response.status);
  return response.json();
}
function message(error: unknown): string {
  const status = error instanceof ApiError ? error.status : 0;
  if (status === 401) return "Session unavailable. Sign in again.";
  if (status === 403 || status === 404) return "Patient context unavailable or access denied. No record is displayed.";
  if (status === 409) return "This item changed or was already submitted. Server state has been reloaded.";
  if (status === 422) return "Check the submitted values and supported synthetic text format.";
  return "Request could not be completed. Refresh server state before retrying; a decision may already have saved.";
}
type Turn = { id: string; text?: string };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default function Conversation() {
  const [session, setSession] = useState<{ token: string; account: Account } | null>(null);
  const [patient, setPatient] = useState("");
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [accepted, setAccepted] = useState<AcceptedFact[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // Synchronous guard prevents double clicks before React renders disabled controls.
  const working = useRef(false);
  const epoch = useRef(0);
  const draft = useRef<{ id: string; text: string } | null>(null);
  function clearRecord() { setProposals([]); setAccepted([]); setTurns([]); setText(""); draft.current = null; }
  function resetContext() { epoch.current++; working.current = false; setBusy(false); setPatient(""); clearRecord(); setError(""); setNotice(""); }
  function fail(cause: unknown) {
    setError(message(cause));
    clearRecord();
    if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
      setPatient("");
      if (cause.status === 401) setSession(null);
    }
  }
  function start() { if (working.current) return false; working.current = true; setBusy(true); setError(""); setNotice(""); return true; }
  function finish(ticket: number) { if (ticket === epoch.current) { working.current = false; setBusy(false); } }
  async function load(id: string, active: NonNullable<typeof session>, ticket: number) {
    // Show neither half of the snapshot if either protected read fails.
    const [items, facts] = await Promise.all([
      request<Proposal[]>(`/patients/${id}/confirmations`, active.token),
      request<AcceptedFact[]>(`/patients/${id}/facts`, active.token),
    ]);
    if (ticket !== epoch.current) return;
    if (items.some(p => p.patient_id !== id || p.speaker_id !== active.account.id || p.speaker_role !== active.account.role) || facts.some(f => f.patient_id !== id || f.status !== "CONFIRMED")) {
      throw new ApiError(403);
    }
    setProposals(items); setAccepted(facts);
    setTurns(previous => {
      const merged = [...previous];
      for (const item of items) if (!merged.some(turn => turn.id === item.source_message_id)) merged.push({ id: item.source_message_id });
      return merged;
    });
  }
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!start()) return;
    const ticket = epoch.current;
    const form = event.currentTarget;
    const fields = new FormData(form);
    const identity = String(fields.get("identity"));
    const password = String(fields.get("password"));
    form.reset();
    try {
      const { access_token } = await request<{ access_token: string }>("/auth/login", undefined, { identity, password });
      const account = await request<Account>("/auth/me", access_token);
      if (ticket === epoch.current) setSession({ token: access_token, account });
    } catch (cause) { if (ticket === epoch.current) fail(cause); }
    finally { finish(ticket); }
  }
  async function open(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!session || !start()) return;
    const id = String(new FormData(event.currentTarget).get("patient")).trim().toLowerCase();
    const ticket = ++epoch.current; clearRecord(); setPatient("");
    try {
      if (!uuid.test(id)) { setError("Enter a valid patient profile UUID."); return; }
      await load(id, session, ticket);
      if (ticket === epoch.current) setPatient(id);
    } catch (cause) { if (ticket === epoch.current) fail(cause); }
    finally { finish(ticket); }
  }
  async function refresh() {
    if (!session || !patient || !start()) return;
    const ticket = epoch.current;
    setProposals([]); setAccepted([]);
    try { await load(patient, session, ticket); }
    catch (cause) { if (ticket === epoch.current) fail(cause); }
    finally { finish(ticket); }
  }
  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!session || !patient || !text.trim() || !start()) return;
    const ticket = epoch.current;
    // Reuse an ID on an uncertain retry of the same message; never auto-resubmit.
    if (!draft.current || draft.current.text !== text) draft.current = { id: crypto.randomUUID(), text };
    const turn = draft.current;
    try {
      const result = await request<{ confirmations: Proposal[] }>(`/patients/${patient}/extractions`, session.token, { source_message_id: turn.id, text: turn.text, data_origin: "synthetic" });
      if (ticket !== epoch.current) return;
      setTurns(previous => [...previous.filter(t => t.id !== turn.id), turn]);
      setText(""); draft.current = null;
      await load(patient, session, ticket);
      if (ticket === epoch.current && result.confirmations.length === 0) setNotice("No supported facts extracted. Nothing was added to accepted state.");
    } catch (cause) {
      if (ticket !== epoch.current) return;
      if (cause instanceof ApiError && cause.status === 409) {
        try { await load(patient, session, ticket); if (ticket === epoch.current) { setText(""); draft.current = null; setNotice(message(cause)); } }
        catch (reloadError) { if (ticket === epoch.current) fail(reloadError); }
      } else if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) fail(cause);
      else setError(message(cause));
    } finally { finish(ticket); }
  }
  async function decide(id: string, action: "confirm" | "reject") {
    if (!session || !patient || !start()) return;
    const ticket = epoch.current;
    try {
      await request(`/patients/${patient}/confirmations/${id}/${action}`, session.token, {});
      if (ticket !== epoch.current) return;
      await load(patient, session, ticket);
      if (ticket === epoch.current) setNotice("Decision saved. Display refreshed from server state.");
    } catch (cause) {
      if (ticket !== epoch.current) return;
      if (cause instanceof ApiError && cause.status === 409) {
        try { await load(patient, session, ticket); if (ticket === epoch.current) setNotice(message(cause)); }
        catch (reloadError) { if (ticket === epoch.current) fail(reloadError); }
      } else fail(cause);
    } finally { finish(ticket); }
  }
  return <main className="workspace">
    <header><h1>ClinCoherence</h1><p>Synthetic text research prototype - T2DM</p>
      <p>Patient-specific records, supervised AI extraction and technically verified record restatements. No medical advice, diagnosis or prescribing.</p></header>
    {error && <p role="alert" aria-label="Request error" className="error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!session ? <section className="panel"><h2>Sign in</h2><p>Use an existing activated Patient or Practitioner account.</p>
      <form onSubmit={login} aria-busy={busy}>
        <label>Account identity<input name="identity" autoComplete="username" required maxLength={320} /></label>
        <label>Password<input name="password" type="password" autoComplete="current-password" required maxLength={1024} /></label>
        <button disabled={busy}>{busy ? "Signing in..." : "Sign in"}</button>
      </form></section> : <>
      <section className="panel"><div className="actions"><h2>{session.account.role === "patient" ? "Patient" : "Practitioner"} mode</h2>
        <button className="secondary" onClick={() => { resetContext(); setSession(null); }}>Sign out</button></div>
        <p>Tokens and text stay in memory and are cleared on sign out, context change, or page reload.</p>
        {!patient ? <form onSubmit={open} aria-busy={busy}>
          <label>Patient profile UUID<input name="patient" required autoComplete="off" /></label>
          <p>{session.account.role === "patient" ? "Enter your own patient profile UUID (not your account ID)." : "Enter a synthetic patient's profile UUID. Current authorization and consent are required."}</p>
          <button disabled={busy}>{busy ? "Loading..." : "Open patient conversation"}</button>
        </form> : <><p>Patient context: {patient}</p><div className="actions">
          <button className="secondary" onClick={resetContext}>Change patient context</button>
          <button onClick={refresh} disabled={busy}>Refresh server state</button>
        </div></>}
      </section>
      {patient && !busy && <VerifiedRecords token={session.token} patient={patient} facts={accepted} onDenied={status => fail(new ApiError(status))} />}
      {patient && <div className="conversation-layout">
        <section className="panel" aria-label="Your conversation"><h2>Your conversation</h2>
          <p>Only your own source messages and proposals are shown. Shared record consent does not share transcripts.</p>
          {turns.map(turn => <section key={turn.id} className="turn" aria-label="Source message">
            <h3>Your source message</h3>
            {turn.text ? <p className="source-text">{turn.text}</p> : <p>Earlier message - exact supporting quotes appear on each card.</p>}
            {proposals.filter(p => p.source_message_id === turn.id).map(p => <FactCard key={p.confirmation_id} proposal={p} busy={busy} onDecision={decide} />)}
          </section>)}
          {!turns.length && <p>No source messages loaded.</p>}
          <form onSubmit={send} aria-busy={busy}>
            <label>Synthetic message<textarea value={text} onChange={e => setText(e.target.value)} required maxLength={16000} rows={5} disabled={busy} /></label>
            <p>Use synthetic text only. Mock example: <code>HbA1c: 7.1 % at 2026-01-01T10:00:00Z</code></p>
            <button disabled={busy || !text.trim()}>{busy ? "Working..." : "Send for extraction"}</button>
          </form>
        </section>
        <section className="panel" aria-label="Accepted longitudinal facts"><h2>Accepted longitudinal facts</h2>
          <p>Current accepted shared record, loaded separately from proposals. Confirmation does not establish clinical correctness.</p>
          {!accepted.length && <p>No accepted facts loaded.</p>}
          {accepted.map(f => <article className="fact-card" key={f.version_id} aria-label="Accepted fact">
            <strong>{attribution(f.attribution)}</strong><FactDetails fact={f.fact} /><p>Observed: <time dateTime={f.observed_at}>{f.observed_at}</time></p>
          </article>)}
        </section>
      </div>}
    </>}
  </main>;
}
