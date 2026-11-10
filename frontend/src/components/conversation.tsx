"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import FactCard from "./fact-card";
import { Account, AcceptedFact, acceptedFact, acceptedFacts, CorrectionRequest, correctionRequest, Proposal } from "./clinical-types";
import ClinicalHistory from "./clinical-history";
import VerifiedRecords from "./verified-records";
import RecordQuestions from "./record-questions";
import PatientSelector, { ApiError, exact, request, uuid } from "./patient-selector";
import InvitationPanel from "./invitation-panel";
import ConsentPanel from "./consent-panel";

function message(error: unknown): string {
  const status = error instanceof ApiError ? error.status : 0;
  if (status === 401) return "Session unavailable. Sign in again.";
  if (status === 403 || status === 404) return "Patient context unavailable or access denied. No record is displayed.";
  if (status === 409) return "This item changed or was already submitted. Server state has been reloaded.";
  if (status === 422) return "Check the submitted values and supported synthetic text format.";
  return "Request could not be completed. Refresh server state before retrying; a decision may already have saved.";
}
type Turn = { id: string; text?: string };
type Profile = { role: Account["role"]; profile_id: string };

export default function Conversation() {
  const [session, setSession] = useState<{ token: string; account: Account } | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [contextVersion, setContextVersion] = useState(0);
  const [patient, setPatient] = useState("");
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [accepted, setAccepted] = useState<AcceptedFact[]>([]);
  const [historyFocus, setHistoryFocus] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // Synchronous guard prevents double clicks before React renders disabled controls.
  const working = useRef(false);
  const epoch = useRef(0);
  const context = useRef(new AbortController());
  const draft = useRef<{ id: string; text: string } | null>(null);
  const clearRecord = useCallback(() => { setProposals([]); setAccepted([]); setHistoryFocus(null); setTurns([]); setText(""); draft.current = null; }, []);
  const cancelRequests = useCallback(() => { epoch.current++; context.current.abort(); context.current = new AbortController(); }, []);
  useEffect(() => cancelRequests, [cancelRequests]);
  const clearClinical = useCallback(() => { cancelRequests(); working.current = false; setBusy(false); clearRecord(); setContextVersion(value => value + 1); }, [cancelRequests, clearRecord]);
  const resetContext = useCallback(() => { clearClinical(); setPatient(""); setError(""); setNotice(""); }, [clearClinical]);
  const fail = useCallback((cause: unknown) => {
    clearClinical(); setPatient(""); setError(message(cause)); setNotice("");
    if (cause instanceof ApiError && cause.status === 401) { setSession(null); setProfile(null); }
  }, [clearClinical]);
  const onDenied = useCallback((status: number) => fail(new ApiError(status)), [fail]);
  function start() { if (working.current) return false; working.current = true; setBusy(true); setError(""); setNotice(""); return true; }
  function finish(ticket: number) { if (ticket === epoch.current) { working.current = false; setBusy(false); } }
  const load = useCallback(async (id: string, active: NonNullable<typeof session>, ticket: number, signal: AbortSignal) => {
    // Show neither half of the snapshot if either protected read fails.
    const [items, facts] = await Promise.all([
      request<Proposal[]>(`/patients/${id}/confirmations`, active.token, undefined, signal),
      request<unknown>(`/patients/${id}/facts`, active.token, undefined, signal),
    ]);
    if (ticket !== epoch.current || signal.aborted) return;
    if (!Array.isArray(items) || items.some(p => p.patient_id !== id || p.speaker_id !== active.account.id || p.speaker_role !== active.account.role)) {
      throw new ApiError(403);
    }
    const acceptedSnapshot = acceptedFacts(facts, id);
    setProposals(items); setAccepted(acceptedSnapshot);
    setTurns(previous => {
      const merged = [...previous];
      for (const item of items) if (!merged.some(turn => turn.id === item.source_message_id)) merged.push({ id: item.source_message_id });
      return merged;
    });
  }, []);
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!start()) return;
    const ticket = epoch.current;
    const form = event.currentTarget;
    const fields = new FormData(form);
    const identity = String(fields.get("identity"));
    const password = String(fields.get("password"));
    form.reset();
    try {
      const signal = context.current.signal;
      const auth = exact(await request<unknown>("/auth/login", undefined, { identity, password }, signal), "access_token,token_type");
      if (typeof auth.access_token !== "string" || !auth.access_token || auth.token_type !== "bearer") throw new ApiError(401);
      const me = exact(await request<unknown>("/auth/me", auth.access_token, undefined, signal), "id,role");
      if (typeof me.id !== "string" || !uuid.test(me.id) || (me.role !== "patient" && me.role !== "practitioner")) throw new ApiError(401);
      const active = { token: auth.access_token, account: { id: me.id, role: me.role } as Account };
      if (ticket !== epoch.current) return;
      setSession(active);
      const own = exact(await request<unknown>("/auth/profile", active.token, undefined, signal), "profile_id,role");
      if (own.role !== active.account.role || typeof own.profile_id !== "string" || !uuid.test(own.profile_id)) throw new ApiError(403);
      if (ticket !== epoch.current) return;
      setProfile({ profile_id: own.profile_id, role: active.account.role });
      if (active.account.role === "patient") {
        await load(own.profile_id, active, ticket, signal);
        if (ticket === epoch.current) setPatient(own.profile_id);
      }
    } catch (cause) { if (ticket === epoch.current) fail(cause); }
    finally { finish(ticket); }
  }
  const selectPatient = useCallback((id: string) => {
    if (!session || !profile) return;
    resetContext();
    if (!id) return;
    if (!uuid.test(id) || (session.account.role === "patient" && id !== profile.profile_id)) { fail(new ApiError(403)); return; }
    const ticket = epoch.current;
    const signal = context.current.signal;
    working.current = true; setBusy(true);
    void (async () => {
      try { await load(id, session, ticket, signal); if (ticket === epoch.current) setPatient(id); }
      catch (cause) { if (ticket === epoch.current) fail(cause); }
      finally { if (ticket === epoch.current) { working.current = false; setBusy(false); } }
    })();
  }, [session, profile, resetContext, fail, load]);
  async function refresh(historyChanged = false) {
    if (!session || !patient || !start()) return;
    const ticket = epoch.current;
    setProposals([]); setAccepted([]); setHistoryFocus(null);
    try {
      await load(patient, session, ticket, context.current.signal);
      if (historyChanged && ticket === epoch.current) setNotice("Clinical record changed; current accepted records were refreshed. Open details/history again.");
    }
    catch (cause) { if (ticket === epoch.current) fail(cause); }
    finally { finish(ticket); }
  }
  async function correct(fact: AcceptedFact, input: CorrectionRequest) {
    if (!session || !patient || !start()) return;
    const ticket = epoch.current, id = patient, active = session, signal = context.current.signal;
    let acknowledgement = "uncertain";
    let validInput = false;
    setHistoryFocus(null);
    try {
      const current = accepted.find(row => row.fact_id === fact.fact_id);
      const payload = correctionRequest(input);
      const category = active.account.role === "patient" ? "patient_reported" : "practitioner_managed";
      if (!current || current.patient_id !== id || current.version_id !== payload.expected_version_id || current.attribution !== category) throw new ApiError(403);
      validInput = true;
      setAccepted([]); setProposals([]); // Existing Verified Records is unmounted by busy before handoff.
      try {
        const value = await request<unknown>(`/patients/${id}/facts/${fact.fact_id}/corrections`, active.token, payload, signal);
        if (ticket !== epoch.current || signal.aborted) return;
        const result = acceptedFact(value, id);
        if (result.fact_id !== fact.fact_id || result.previous_version_id !== payload.expected_version_id || result.version <= current.version || result.version_id === current.version_id || result.author_id !== active.account.id || result.author_role !== active.account.role || result.attribution !== category || result.entry_method !== "STRUCTURED") throw new Error();
        acknowledgement = "saved";
      } catch (cause) {
        if (ticket !== epoch.current || signal.aborted) return;
        if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) throw cause;
        if (cause instanceof ApiError && cause.status === 409) acknowledgement = "stale";
        else if (cause instanceof ApiError && cause.status === 422) acknowledgement = "invalid";
        // Other errors (including malformed acknowledgements) may follow a committed write.
      }
      await load(id, active, ticket, signal); // Read recovery only; never replay the mutation.
      if (ticket !== epoch.current || signal.aborted) return;
      setHistoryFocus(fact.fact_id);
      setNotice(acknowledgement === "saved"
        ? "Correction acknowledged. Current accepted records and history are being refreshed; prior versions are preserved."
        : acknowledgement === "stale"
          ? "Correction was stale (409). Current records were reloaded. Review fresh history and explicitly prepare a new correction."
          : acknowledgement === "invalid"
            ? "Correction values were rejected. Current records were reloaded. Review fresh history before a new explicit attempt."
            : "Correction acknowledgement is uncertain; it may already have been committed. No automatic retry. Review fresh history before any new attempt.");
    } catch (cause) {
      if (ticket !== epoch.current || signal.aborted) return;
      fail(cause);
      if (validInput) setNotice(acknowledgement === "saved"
        ? "Correction was acknowledged, but record refresh is unavailable. Reauthorize and check fresh history."
        : "Correction acknowledgement or refresh is unavailable; the correction may already have been committed. No automatic retry. Reauthorize and check fresh history before any new attempt.");
    } finally { finish(ticket); }
  }
  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!session || !patient || !text.trim() || !start()) return;
    const ticket = epoch.current;
    // Reuse an ID on an uncertain retry of the same message; never auto-resubmit.
    if (!draft.current || draft.current.text !== text) draft.current = { id: crypto.randomUUID(), text };
    const turn = draft.current;
    try {
      const signal = context.current.signal;
      const result = await request<{ confirmations: Proposal[] }>(`/patients/${patient}/extractions`, session.token, { source_message_id: turn.id, text: turn.text, data_origin: "synthetic" }, signal);
      if (ticket !== epoch.current) return;
      setTurns(previous => [...previous.filter(t => t.id !== turn.id), turn]);
      setText(""); draft.current = null;
      await load(patient, session, ticket, signal);
      if (ticket === epoch.current && result.confirmations.length === 0) setNotice("No supported facts extracted. Nothing was added to accepted state.");
    } catch (cause) {
      if (ticket !== epoch.current) return;
      if (cause instanceof ApiError && cause.status === 409) {
        try { await load(patient, session, ticket, context.current.signal); if (ticket === epoch.current) { setText(""); draft.current = null; setNotice(message(cause)); } }
        catch (reloadError) { if (ticket === epoch.current) fail(reloadError); }
      } else if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) fail(cause);
      else setError(message(cause));
    } finally { finish(ticket); }
  }
  async function decide(id: string, action: "confirm" | "reject") {
    if (!session || !patient || !start()) return;
    const ticket = epoch.current;
    try {
      const signal = context.current.signal;
      await request(`/patients/${patient}/confirmations/${id}/${action}`, session.token, {}, signal);
      if (ticket !== epoch.current) return;
      await load(patient, session, ticket, signal);
      if (ticket === epoch.current) setNotice("Decision saved. Display refreshed from server state.");
    } catch (cause) {
      if (ticket !== epoch.current) return;
      if (cause instanceof ApiError && cause.status === 409) {
        try { await load(patient, session, ticket, context.current.signal); if (ticket === epoch.current) setNotice(message(cause)); }
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
        <button className="secondary" onClick={() => { resetContext(); setSession(null); setProfile(null); }}>Sign out</button></div>
        <p>Tokens stay in memory until sign out or reload. Patient-specific content and invitation secrets are cleared on context changes.</p>
        {profile ? <p className="break-all">Your {profile.role} profile: {profile.profile_id}</p> : <p>Profile discovery unavailable or loading. Sign out and sign in again if discovery fails.</p>}
        {busy && <p role="status">Loading patient workspace...</p>}
        {profile?.role === "patient" && !patient && !busy && <button onClick={() => selectPatient(profile.profile_id)}>Open my record</button>}
        {patient && <><p className="break-all">Patient context: {patient}</p><div className="actions">
          {session.account.role === "practitioner" && <button className="secondary" onClick={resetContext}>Change patient context</button>}
          <button onClick={() => void refresh()} disabled={busy}>Refresh server state</button>
        </div></>}
      </section>
      {profile && <div className="conversation-layout">
        {profile.role === "practitioner" ? <>
          <PatientSelector token={session.token} value={patient} onSelect={selectPatient} onDenied={onDenied} />
          <InvitationPanel key={contextVersion} token={session.token} onDenied={onDenied} />
        </> : <ConsentPanel token={session.token} onDenied={onDenied} onMutation={clearClinical} />}
      </div>}
      {patient && !busy && <VerifiedRecords key={`${contextVersion}:${patient}`} token={session.token} patient={patient} facts={accepted} onDenied={onDenied} />}
      {patient && !busy && <RecordQuestions key={`questions:${session.account.id}:${contextVersion}:${patient}`} token={session.token} patient={patient} onDenied={onDenied} />}
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
        {busy ? <section className="panel" aria-label="Clinical History"><h2>Clinical History</h2><p role="status">Loading accepted clinical records...</p></section>
          : <ClinicalHistory key={`${session.account.id}:${contextVersion}:${patient}`} token={session.token} patient={patient} role={session.account.role} facts={accepted} contextSignal={context.current.signal} autoRoot={historyFocus} onDenied={onDenied} onChanged={() => void refresh(true)} onCorrection={(fact, payload) => void correct(fact, payload)} />}
      </div>}
    </>}
  </main>;
}
