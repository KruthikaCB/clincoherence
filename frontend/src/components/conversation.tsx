"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  Account, AcceptedFact, acceptedFact, acceptedFacts,
  CorrectionRequest, correctionRequest, Proposal,
} from "./clinical-types";
import ClinicalHistory from "./clinical-history";
import VerifiedRecords from "./verified-records";
import PatientSelector, { ApiError, exact, request, useDashboardTask, uuid } from "./patient-selector";
import { decodeRecordQuestion } from "./record-questions";
import InvitationPanel from "./invitation-panel";
import ConsentPanel from "./consent-panel";
import { FactSummary } from "./fact-card";

// ─── Icons ─────────────────────────────────────────────────────────────────────
function IcoHeart() { return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>; }
function IcoUser() { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>; }
function IcoPatients() { return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>; }
function IcoHistory() { return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>; }
function IcoPill() { return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10.5 20H4a2 2 0 0 1-2-2V5c0-1.1.9-2 2-2h3.93a2 2 0 0 1 1.66.9l.82 1.2a2 2 0 0 0 1.66.9H20a2 2 0 0 1 2 2v3"/><circle cx="18" cy="18" r="3"/><path d="m22 22-1.5-1.5"/></svg>; }
function IcoShield() { return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>; }
function IcoInvite() { return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.49 12 19.79 19.79 0 0 1 1.44 3.4 2 2 0 0 1 3.44 1h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L7.91 8.4a16 16 0 0 0 6.29 6.29l.8-.8a2 2 0 0 1 2.1-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>; }
function IcoSend() { return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>; }
function IcoLogout() { return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>; }
function IcoRefresh() { return <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>; }
function IcoPlus() { return <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>; }
function IcoCheck() { return <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>; }
function IcoX() { return <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>; }
function IcoQuestion() { return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>; }
function IcoNote() { return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>; }
function IcoVerify() { return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/></svg>; }

// ─── Error message helper ───────────────────────────────────────────────────────
function message(error: unknown): string {
  const status = error instanceof ApiError ? error.status : 0;
  if (status === 401) return "Session unavailable. Sign in again.";
  if (status === 403 || status === 404) return "Patient context unavailable or access denied.";
  if (status === 409) return "This item changed or was already submitted. Server state has been reloaded.";
  if (status === 422) return "Check the submitted values and supported synthetic text format.";
  return "Request could not be completed. Refresh server state before retrying.";
}

type RecordAnswer = { text: string; kind: "result" | "notice" | "error" };
type Turn = { id: string; text?: string; question?: string; answer?: RecordAnswer };
type Profile = { role: Account["role"]; profile_id: string };
type PanelView = "chat" | "history" | "medications" | "consent" | "invitations" | "verified";

// ─── Sidebar ────────────────────────────────────────────────────────────────────
function Sidebar({ session, patients, demoLabels, activePatient, onSelectPatient, onAddPatient, activePanel, onPanel, onSignOut, canAdd }: {
  session: { account: Account } | null;
  patients: string[];
  demoLabels: Record<string, string>;
  activePatient: string;
  onSelectPatient: (id: string) => void;
  onAddPatient: () => void;
  activePanel: PanelView;
  onPanel: (v: PanelView) => void;
  onSignOut: () => void;
  canAdd: boolean;
}) {
  const role = session?.account.role ?? "practitioner";
  const initials = role === "practitioner" ? "Dr" : "Pt";

  return (
    <aside className="cc-sidebar" aria-label="Navigation sidebar">
      {/* Logo */}
      <div className="cc-sidebar-logo">
        <div style={{ color: "#2dd4bf" }}><IcoHeart /></div>
        <div>
          <div className="cc-sidebar-logo-name">ClinCoherence</div>
          <div className="cc-sidebar-logo-tag">T2DM · Synthetic Demo</div>
        </div>
      </div>

      {/* Nav items */}
      {session && (
        <>
          {role === "practitioner" && (
            <>
              <div className="cc-sidebar-section">
                <div className="cc-sidebar-heading">Workspace</div>
                <button className={`cc-sidebar-btn ${activePanel === "chat" ? "active" : ""}`} onClick={() => onPanel("chat")}>
                  <IcoNote /> Clinical Notes / Ask
                </button>
                <button className={`cc-sidebar-btn ${activePanel === "history" ? "active" : ""}`} onClick={() => onPanel("history")} disabled={!activePatient}>
                  <IcoHistory /> Clinical History
                </button>
                <button className={`cc-sidebar-btn ${activePanel === "medications" ? "active" : ""}`} onClick={() => onPanel("medications")} disabled={!activePatient}>
                  <IcoPill /> Medications
                </button>
                <button className={`cc-sidebar-btn ${activePanel === "verified" ? "active" : ""}`} onClick={() => onPanel("verified")} disabled={!activePatient}>
                  <IcoVerify /> Verified Records
                </button>
                <button className={`cc-sidebar-btn ${activePanel === "invitations" ? "active" : ""}`} onClick={() => onPanel("invitations")}>
                  <IcoInvite /> Patient Invitations
                </button>
              </div>
              <div className="cc-sidebar-section" style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
                <div className="cc-sidebar-heading">Patients</div>
                {patients.map(id => (
                  <button key={id} className={`cc-sidebar-patient ${activePatient === id ? "active" : ""}`} onClick={() => onSelectPatient(id)} title={id}>
                    <span style={{ opacity: 0.6, flexShrink: 0 }}><IcoUser /></span>
                    <span className="cc-sidebar-patient-name">
                      {demoLabels[id] ? `Demo · ${demoLabels[id]}` : id.slice(0, 8) + "…"}
                    </span>
                  </button>
                ))}
                {patients.length === 0 && <div style={{ padding: "0.4rem 1.1rem", fontSize: "0.75rem", color: "#6b7280" }}>No patients yet</div>}
                {canAdd && (
                  <button className="cc-sidebar-add" onClick={onAddPatient}>
                    <IcoPlus /> Add Patient
                  </button>
                )}
              </div>
            </>
          )}

          {role === "patient" && (
            <div className="cc-sidebar-section">
              <div className="cc-sidebar-heading">My Care</div>
              <button className={`cc-sidebar-btn ${activePanel === "chat" ? "active" : ""}`} onClick={() => onPanel("chat")}>
                <IcoNote /> My Record
              </button>
              <button className={`cc-sidebar-btn ${activePanel === "consent" ? "active" : ""}`} onClick={() => onPanel("consent")}>
                <IcoShield /> Consent
              </button>
              <button className={`cc-sidebar-btn ${activePanel === "history" ? "active" : ""}`} onClick={() => onPanel("history")} disabled={!activePatient}>
                <IcoHistory /> History
              </button>
              <button className={`cc-sidebar-btn ${activePanel === "verified" ? "active" : ""}`} onClick={() => onPanel("verified")} disabled={!activePatient}>
                <IcoVerify /> Verified Records
              </button>
            </div>
          )}
        </>
      )}

      {/* Footer / user */}
      <div className="cc-sidebar-footer">
        {session ? (
          <div className="cc-user-row">
            <div className="cc-user-avatar">{initials}</div>
            <span className="cc-user-name">{role === "practitioner" ? "Practitioner" : "Patient"} account</span>
            <button className="cc-signout-btn" onClick={onSignOut} title="Sign out" aria-label="Sign out">
              <IcoLogout />
            </button>
          </div>
        ) : (
          <div style={{ fontSize: "0.75rem", color: "#6b7280" }}>Not signed in</div>
        )}
      </div>
    </aside>
  );
}

// ─── Top bar ───────────────────────────────────────────────────────────────────
function TopBar({ title, isDemo, onRefresh, busy, patientId }: {
  title: string; isDemo: boolean; onRefresh: () => void; busy: boolean; patientId: string;
}) {
  return (
    <div className="cc-topbar">
      <div className="cc-topbar-title">{title}</div>
      {patientId && (
        <span className={`cc-topbar-pill ${isDemo ? "demo" : ""}`}>
          {isDemo ? "Synthetic Demo" : "Patient"}
        </span>
      )}
      {patientId && (
        <button className="cc-btn cc-btn-ghost cc-btn-sm" onClick={onRefresh} disabled={busy} aria-label="Refresh server state">
          <IcoRefresh /> Refresh
        </button>
      )}
    </div>
  );
}

// ─── Proposal card ─────────────────────────────────────────────────────────────
function ProposalCard({ proposal, busy, onDecision }: {
  proposal: Proposal; busy: boolean; onDecision: (id: string, action: "confirm" | "reject") => void;
}) {
  return (
    <div className="cc-proposal" aria-label="Extracted fact" aria-busy={busy}>
      <div className="cc-proposal-header">
        <span className="cc-proposal-kind">{proposal.proposed_fact.kind}</span>
        <span className={`cc-proposal-status ${proposal.status}`}>{proposal.status}</span>
      </div>
      <div className="cc-proposal-body">
        <FactSummary fact={proposal.proposed_fact} />
        <div className="cc-fact-row" style={{ marginTop: "0.4rem" }}>
          <span className="cc-fact-label">Observed</span>
          <span className="cc-fact-val"><time dateTime={proposal.observed_at}>{formatDate(proposal.observed_at)}</time></span>
        </div>
        {proposal.source_quote && (
          <blockquote className="cc-fact-quote">&ldquo;{proposal.source_quote}&rdquo;</blockquote>
        )}
      </div>
      {proposal.status === "PENDING" && (
        <div className="cc-proposal-actions">
          <button className="cc-accept-btn" disabled={busy} onClick={() => onDecision(proposal.confirmation_id, "confirm")}>
            <IcoCheck /> Accept
          </button>
          <button className="cc-reject-btn" disabled={busy} onClick={() => onDecision(proposal.confirmation_id, "reject")}>
            <IcoX /> Reject
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Records sidebar panel ─────────────────────────────────────────────────────
function RecordsPanel({ facts, onSelectForHistory }: {
  facts: AcceptedFact[]; onSelectForHistory?: (id: string) => void;
}) {
  const medications = facts.filter(f => f.fact.kind === "medication");
  const labs = facts.filter(f => f.fact.kind === "laboratory");
  const others = facts.filter(f => f.fact.kind !== "medication" && f.fact.kind !== "laboratory");

  if (!facts.length) {
    return (
      <div className="cc-records-panel">
        <div className="cc-records-header">Accepted Records</div>
        <div className="cc-empty" style={{ padding: "2rem 1rem" }}>
          <div className="cc-empty-icon" style={{ width: 40, height: 40 }}><IcoHeart /></div>
          <div style={{ fontSize: "0.78rem", color: "#9ca3af", textAlign: "center" }}>No accepted records yet</div>
        </div>
      </div>
    );
  }

  return (
    <div className="cc-records-panel">
      <div className="cc-records-header">Accepted Records · {facts.length}</div>
      <div className="cc-records-scroll">
        {labs.length > 0 && (
          <>
            <div style={{ fontSize: "0.65rem", fontWeight: 700, color: "#6b7280", textTransform: "uppercase", letterSpacing: "0.07em", padding: "0.25rem 0 0.1rem" }}>Lab Values</div>
            {labs.map(f => <RecordItem key={f.version_id} fact={f} onClick={() => onSelectForHistory?.(f.fact_id)} />)}
          </>
        )}
        {medications.length > 0 && (
          <>
            <div style={{ fontSize: "0.65rem", fontWeight: 700, color: "#6b7280", textTransform: "uppercase", letterSpacing: "0.07em", padding: "0.25rem 0 0.1rem" }}>Medications</div>
            {medications.map(f => <RecordItem key={f.version_id} fact={f} onClick={() => onSelectForHistory?.(f.fact_id)} />)}
          </>
        )}
        {others.length > 0 && (
          <>
            <div style={{ fontSize: "0.65rem", fontWeight: 700, color: "#6b7280", textTransform: "uppercase", letterSpacing: "0.07em", padding: "0.25rem 0 0.1rem" }}>Other</div>
            {others.map(f => <RecordItem key={f.version_id} fact={f} onClick={() => onSelectForHistory?.(f.fact_id)} />)}
          </>
        )}
      </div>
    </div>
  );
}

function RecordItem({ fact, onClick }: { fact: AcceptedFact; onClick?: () => void }) {
  const isDemo = fact.attribution === "practitioner_managed";
  return (
    <div className="cc-record-card" role="button" tabIndex={0} onClick={onClick} onKeyDown={e => e.key === "Enter" && onClick?.()}>
      <div className="cc-record-kind">{fact.fact.kind}</div>
      <div className="cc-record-value"><FactSummary fact={fact.fact} compact /></div>
      <div className="cc-record-meta">{formatDate(fact.observed_at)}</div>
      <span className={`cc-record-attr ${isDemo ? "practitioner" : "patient"}`}>
        {isDemo ? "practitioner" : "patient"}
      </span>
    </div>
  );
}

// ─── Date formatter ─────────────────────────────────────────────────────────────
function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  } catch { return iso; }
}

// ─── Empty / placeholder states ────────────────────────────────────────────────
function EmptyState({ icon, title, sub }: { icon: React.ReactNode; title: string; sub?: string }) {
  return (
    <div className="cc-empty">
      <div className="cc-empty-icon">{icon}</div>
      <div className="cc-empty-title">{title}</div>
      {sub && <div className="cc-empty-sub">{sub}</div>}
    </div>
  );
}

// ─── Loading spinner inline ─────────────────────────────────────────────────────
function Spinner() { return <span className="cc-spinner" aria-hidden="true" />; }

// ─── Add Patient Modal (shown inline in chat area) ──────────────────────────────
function AddPatientPanel({ onDone, token, onSelect, onDenied }: {
  onDone: () => void; token: string; onSelect: (id: string) => void; onDenied: (s: number) => void;
}) {
  // Delegate entirely to PatientSelector which owns the creation logic.
  return (
    <div style={{ padding: "1.5rem", maxWidth: 440 }}>
      <h2 style={{ marginBottom: "1rem" }}>Add Synthetic Demo Patient</h2>
      <PatientSelector token={token} value="" onSelect={id => { if (id) { onSelect(id); onDone(); } }} onDenied={onDenied} />
      <button className="cc-btn cc-btn-ghost cc-btn-sm" style={{ marginTop: "0.75rem" }} onClick={onDone}>Cancel</button>
    </div>
  );
}

// ─── Main Conversation (dashboard shell) ───────────────────────────────────────
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
  const [activePanel, setActivePanel] = useState<PanelView>("chat");
  const [showAddPatient, setShowAddPatient] = useState(false);
  const [inputTab, setInputTab] = useState<"note" | "ask">("note");

  // Patient list state (lifted from PatientSelector for sidebar)
  const [patientList, setPatientList] = useState<string[]>([]);
  const [demoLabels, setDemoLabels] = useState<Record<string, string>>({});
  const [canAdd, setCanAdd] = useState(false);

  const working = useRef(false);
  const epoch = useRef(0);
  const context = useRef(new AbortController());
  const draft = useRef<{ id: string; text: string } | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);

  const clearRecord = useCallback(() => {
    setProposals([]); setAccepted([]); setHistoryFocus(null); setTurns([]); setText(""); draft.current = null;
  }, []);
  const startQuestion = useCallback((id: string, question: string) => {
    setTurns(previous => [...previous, { id, question }]);
  }, []);
  const answerQuestion = useCallback((id: string, answer: RecordAnswer) => {
    setTurns(previous => previous.map(turn => turn.id === id ? { ...turn, answer } : turn));
  }, []);
  const previousQuestions = turns.filter(turn => turn.question && turn.answer && turn.answer.kind !== "error")
    .map(turn => turn.question as string).slice(-6);
  const cancelRequests = useCallback(() => {
    epoch.current++; context.current.abort(); context.current = new AbortController();
  }, []);
  useEffect(() => cancelRequests, [cancelRequests]);
  const clearClinical = useCallback(() => {
    cancelRequests(); working.current = false; setBusy(false); clearRecord(); setContextVersion(v => v + 1);
  }, [cancelRequests, clearRecord]);
  const resetContext = useCallback(() => {
    clearClinical(); setPatient(""); setError(""); setNotice("");
  }, [clearClinical]);
  const fail = useCallback((cause: unknown) => {
    clearClinical(); setPatient(""); setError(message(cause)); setNotice("");
    if (cause instanceof ApiError && cause.status === 401) { setSession(null); setProfile(null); }
  }, [clearClinical]);
  const onDenied = useCallback((status: number) => fail(new ApiError(status)), [fail]);

  function start() { if (working.current) return false; working.current = true; setBusy(true); setError(""); setNotice(""); return true; }
  function finish(ticket: number) { if (ticket === epoch.current) { working.current = false; setBusy(false); } }

  const load = useCallback(async (id: string, active: NonNullable<typeof session>, ticket: number, signal: AbortSignal) => {
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
      for (const item of items) if (!merged.some(t => t.id === item.source_message_id)) merged.push({ id: item.source_message_id });
      return merged;
    });
  }, []);

  // Auto-scroll chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, proposals]);

  // ── Reload patient list when session changes ──
  const loadPatientList = useCallback(async (tok: string, signal: AbortSignal) => {
    try {
      const result = await request<unknown>("/patients", tok, undefined, signal);
      if (!Array.isArray(result)) return;
      const ids = result.map((row: unknown) => {
        const item = exact(row as Record<string, unknown>, "patient_id");
        return typeof item.patient_id === "string" && uuid.test(item.patient_id) ? item.patient_id : null;
      }).filter((id): id is string => id !== null);

      const metadata: Record<string, string> = {};
      let addable = false;
      try {
        const demoRows = await request<unknown>("/demo-patients", tok, undefined, signal);
        if (Array.isArray(demoRows)) {
          addable = true;
          for (const row of demoRows) {
            const item = exact(row as Record<string, unknown>, "identifier,label,patient_id");
            if (typeof item.patient_id === "string" && typeof item.identifier === "string") {
              metadata[item.patient_id] = item.identifier;
            }
          }
        }
      } catch (e) {
        if (e instanceof ApiError && [401, 403].includes(e.status)) return;
      }
      if (!signal.aborted) {
        setPatientList(ids);
        setDemoLabels(metadata);
        setCanAdd(addable);
      }
    } catch { /* non-fatal */ }
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
      } else {
        // Load patient list for practitioner sidebar
        await loadPatientList(active.token, signal);
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
      try {
        await load(id, session, ticket, signal);
        if (ticket === epoch.current) { setPatient(id); setActivePanel("chat"); }
      } catch (cause) { if (ticket === epoch.current) fail(cause); }
      finally { if (ticket === epoch.current) { working.current = false; setBusy(false); } }
    })();
  }, [session, profile, resetContext, fail, load]);

  async function refresh(historyChanged = false) {
    if (!session || !patient || !start()) return;
    const ticket = epoch.current;
    setProposals([]); setAccepted([]); setHistoryFocus(null);
    try {
      await load(patient, session, ticket, context.current.signal);
      if (historyChanged && ticket === epoch.current) setNotice("Clinical record changed; accepted records refreshed.");
    } catch (cause) { if (ticket === epoch.current) fail(cause); }
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
      setAccepted([]); setProposals([]);
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
      }
      await load(id, active, ticket, signal);
      if (ticket !== epoch.current || signal.aborted) return;
      setHistoryFocus(fact.fact_id);
      setNotice(acknowledgement === "saved" ? "Correction saved and records refreshed."
        : acknowledgement === "stale" ? "Correction was stale (409). Records reloaded — review history before retrying."
        : acknowledgement === "invalid" ? "Correction values rejected. Records reloaded."
        : "Correction outcome uncertain. No automatic retry — check history before retrying.");
    } catch (cause) {
      if (ticket !== epoch.current || signal.aborted) return;
      fail(cause);
      if (validInput) setNotice(acknowledgement === "saved"
        ? "Correction acknowledged, but refresh unavailable. Reauthorize and check history."
        : "Correction outcome uncertain. No automatic retry — reauthorize and check history.");
    } finally { finish(ticket); }
  }

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !patient || !text.trim() || !start()) return;
    const ticket = epoch.current;
    if (!draft.current || draft.current.text !== text) draft.current = { id: crypto.randomUUID(), text };
    const turn = draft.current;
    try {
      const signal = context.current.signal;
      const result = await request<{ confirmations: Proposal[] }>(`/patients/${patient}/extractions`, session.token, { source_message_id: turn.id, text: turn.text, data_origin: "synthetic" }, signal, 310000);
      if (ticket !== epoch.current) return;
      setTurns(previous => [...previous.filter(t => t.id !== turn.id), turn]);
      setText(""); draft.current = null;
      await load(patient, session, ticket, signal);
      if (ticket === epoch.current && result.confirmations.length === 0) setNotice("No supported facts extracted from this note.");
    } catch (cause) {
      if (ticket !== epoch.current) return;
      if (cause instanceof ApiError && cause.status === 409) {
        try {
          await load(patient, session, ticket, context.current.signal);
          if (ticket === epoch.current) { setText(""); draft.current = null; setNotice(message(cause)); }
        } catch (reloadError) { if (ticket === epoch.current) fail(reloadError); }
      } else if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) fail(cause);
      else if (cause instanceof ApiError && cause.status === 422) setError("Check the note and include an explicit observation timestamp with timezone, for example 2026-01-15T10:00:00Z.");
      else if (cause instanceof ApiError && cause.status === 502) setError("The model output failed source or structure validation. No clinical records were accepted. Review the note before another explicit attempt.");
      else if (cause instanceof ApiError && [503, 504].includes(cause.status)) setError("Local extraction is unavailable or timed out. Check Ollama and qwen3:0.6b. No automatic retry was made; refresh server state before retrying.");
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
      if (ticket === epoch.current) setNotice("Decision saved.");
    } catch (cause) {
      if (ticket !== epoch.current) return;
      if (cause instanceof ApiError && cause.status === 409) {
        try {
          await load(patient, session, ticket, context.current.signal);
          if (ticket === epoch.current) setNotice(message(cause));
        } catch (reloadError) { if (ticket === epoch.current) fail(reloadError); }
      } else fail(cause);
    } finally { finish(ticket); }
  }

  // ── Derived state ──
  const patientLabel = patient ? (demoLabels[patient] ? `Demo · ${demoLabels[patient]}` : patient.slice(0, 8) + "…") : "No patient selected";
  const panelTitle: Record<PanelView, string> = {
    chat: patient ? `Patient Record · ${patientLabel}` : "Dashboard",
    history: "Clinical History",
    medications: "Medications",
    consent: "Practitioner Consent",
    invitations: "Patient Invitations",
    verified: "Verified Records",
  };

  // ── Login screen ──
  if (!session) {
    return (
      <div className="cc-login-wrap">
        <div className="cc-login-box">
          <div className="cc-login-logo">
            <div className="cc-login-logo-icon"><IcoHeart /></div>
            <div>
              <h1>ClinCoherence</h1>
              <div style={{ fontSize: "0.78rem", color: "#6b7280" }}>Longitudinal T2DM Record Prototype</div>
            </div>
          </div>
          <h2>Sign in</h2>
          <div className="cc-login-sub">Use an existing synthetic practitioner or patient account.</div>
          {error && <div className="cc-banner error" style={{ marginBottom: "1rem" }} role="alert">{error}</div>}
          <form onSubmit={login} aria-busy={busy}>
            <label className="cc-label" style={{ marginBottom: "0.75rem" }}>
              Account identity
              <input name="identity" className="cc-input" autoComplete="username" required maxLength={320} disabled={busy} placeholder="doctor-demo@synthetic.invalid" />
            </label>
            <label className="cc-label" style={{ marginBottom: "1.25rem" }}>
              Password
              <input name="password" type="password" className="cc-input" autoComplete="current-password" required maxLength={1024} disabled={busy} />
            </label>
            <button className="cc-btn cc-btn-primary" style={{ width: "100%", justifyContent: "center", padding: "0.65rem" }} disabled={busy}>
              {busy ? <><Spinner /> Signing in…</> : "Sign in"}
            </button>
          </form>
          <p style={{ marginTop: "1rem", fontSize: "0.78rem", textAlign: "center", color: "#6b7280" }}>
            <a href="/register">Create a demo practitioner account</a>
          </p>
          <p style={{ fontSize: "0.72rem", color: "#9ca3af", marginTop: "1.25rem", lineHeight: 1.5 }}>
            Synthetic research prototype. Patients need an invitation — practitioners are unverified demo identities, not professionally credentialed practitioners.
          </p>
        </div>
      </div>
    );
  }

  // ── Dashboard ──
  return (
    <div className="cc-shell">
      <Sidebar
        session={session}
        patients={patientList}
        demoLabels={demoLabels}
        activePatient={patient}
        onSelectPatient={selectPatient}
        onAddPatient={() => setShowAddPatient(true)}
        activePanel={activePanel}
        onPanel={v => { setActivePanel(v); setShowAddPatient(false); }}
        onSignOut={() => { resetContext(); setSession(null); setProfile(null); setPatientList([]); setDemoLabels({}); setCanAdd(false); }}
        canAdd={canAdd}
      />

      <div className="cc-main">
        <TopBar
          title={showAddPatient ? "Add Patient" : panelTitle[activePanel]}
          isDemo={!!demoLabels[patient]}
          onRefresh={() => void refresh()}
          busy={busy}
          patientId={patient}
        />

        {/* Banners */}
        {error && (
          <div className="cc-banner error" role="alert" style={{ margin: "0.75rem 1.25rem 0", flexShrink: 0 }}>
            {error}
            <button className="cc-btn cc-btn-xs cc-btn-ghost" style={{ marginLeft: "auto" }} onClick={() => setError("")}>Dismiss</button>
          </div>
        )}
        {notice && (
          <div className="cc-banner notice" role="status" style={{ margin: "0.5rem 1.25rem 0", flexShrink: 0 }}>
            {notice}
            <button className="cc-btn cc-btn-xs cc-btn-ghost" style={{ marginLeft: "auto" }} onClick={() => setNotice("")}>×</button>
          </div>
        )}

        <div className="cc-body">
          {/* ── Add Patient ── */}
          {showAddPatient && session.account.role === "practitioner" && (
            <div className="cc-chat-col">
              <div className="cc-chat-scroll">
                <AddPatientPanel
                  token={session.token}
                  onDone={() => setShowAddPatient(false)}
                  onSelect={id => {
                    setPatientList(prev => Array.from(new Set([...prev, id])));
                    selectPatient(id);
                  }}
                  onDenied={onDenied}
                />
              </div>
            </div>
          )}

          {/* ── Patient mode ── */}
          {!showAddPatient && session.account.role === "patient" && (
            <>
              {activePanel === "consent" && (
                <div className="cc-chat-col">
                  <div className="cc-chat-scroll">
                    <ConsentPanel key={contextVersion} token={session.token} onDenied={onDenied} onMutation={clearClinical} />
                  </div>
                </div>
              )}
              {activePanel !== "consent" && activePanel !== "history" && activePanel !== "verified" && (
                <>
                  <div className="cc-chat-col">
                    {!patient && !busy && (
                      <div className="cc-empty" style={{ flex: 1 }}>
                        <EmptyState icon={<IcoHeart />} title="Open your record" sub="Your patient record will appear here." />
                        {profile && (
                          <button className="cc-btn cc-btn-primary" style={{ marginTop: "0.5rem" }} onClick={() => selectPatient(profile.profile_id)}>
                            Open my record
                          </button>
                        )}
                      </div>
                    )}
                    {busy && !patient && (
                      <div className="cc-empty"><Spinner /> <span style={{ color: "#6b7280" }}>Loading…</span></div>
                    )}
                    {patient && (
                      <>
                        <div className="cc-chat-scroll" role="log" aria-label="Patient conversation">
                          <ChatArea
                            turns={turns} proposals={proposals} busy={busy}
                            onDecision={decide} inputTab={inputTab} chatEndRef={chatEndRef}
                          />
                        </div>
                        <ChatInput
                          tab={inputTab} onTab={setInputTab}
                          text={text} onText={setText}
                          busy={busy} patient={patient}
                          onSend={send}
                          token={session.token} onDenied={onDenied}
                          contextVersion={contextVersion}
                          previousQuestions={previousQuestions} onQuestion={startQuestion} onAnswer={answerQuestion}
                        />
                      </>
                    )}
                  </div>
                  {patient && <RecordsPanel facts={accepted} />}
                </>
              )}
              {activePanel === "history" && patient && (
                <div className="cc-chat-col">
                  <div className="cc-chat-scroll">
                    <ClinicalHistory
                      key={`${session.account.id}:${contextVersion}:${patient}`}
                      token={session.token} patient={patient}
                      role={session.account.role} facts={accepted}
                      contextSignal={context.current.signal}
                      autoRoot={historyFocus} onDenied={onDenied}
                      onChanged={() => void refresh(true)}
                      onCorrection={(fact, payload) => void correct(fact, payload)}
                    />
                  </div>
                </div>
              )}
              {activePanel === "verified" && patient && (
                <div className="cc-chat-col">
                  <div className="cc-chat-scroll">
                    <VerifiedRecords
                      key={`${contextVersion}:${patient}`}
                      token={session.token} patient={patient} facts={accepted} onDenied={onDenied}
                    />
                  </div>
                </div>
              )}
            </>
          )}

          {/* ── Practitioner mode ── */}
          {!showAddPatient && session.account.role === "practitioner" && (
            <>
              {activePanel === "invitations" && (
                <div className="cc-chat-col">
                  <div className="cc-chat-scroll">
                    <InvitationPanel key={contextVersion} token={session.token} onDenied={onDenied} />
                  </div>
                </div>
              )}

              {activePanel === "chat" && (
                <>
                  {!patient && (
                    <div className="cc-chat-col">
                      <EmptyState
                        icon={<IcoPatients />}
                        title="Select a patient"
                        sub="Choose a patient from the sidebar or add a synthetic demo patient."
                      />
                    </div>
                  )}
                  {busy && !patient && (
                    <div className="cc-chat-col">
                      <div className="cc-empty"><Spinner /><span style={{ color: "#6b7280" }}>Loading patient…</span></div>
                    </div>
                  )}
                  {patient && (
                    <>
                      <div className="cc-chat-col">
                        <div className="cc-chat-scroll" role="log" aria-label="Patient conversation">
                          <ChatArea
                            turns={turns} proposals={proposals} busy={busy}
                            onDecision={decide} inputTab={inputTab} chatEndRef={chatEndRef}
                          />
                        </div>
                        <ChatInput
                          tab={inputTab} onTab={setInputTab}
                          text={text} onText={setText}
                          busy={busy} patient={patient}
                          onSend={send}
                          token={session.token} onDenied={onDenied}
                          contextVersion={contextVersion}
                          previousQuestions={previousQuestions} onQuestion={startQuestion} onAnswer={answerQuestion}
                        />
                      </div>
                      <RecordsPanel facts={accepted} onSelectForHistory={id => { setHistoryFocus(id); setActivePanel("history"); }} />
                    </>
                  )}
                </>
              )}

              {activePanel === "history" && patient && (
                <div className="cc-chat-col">
                  <div className="cc-chat-scroll">
                    <ClinicalHistory
                      key={`${session.account.id}:${contextVersion}:${patient}`}
                      token={session.token} patient={patient}
                      role={session.account.role} facts={accepted}
                      contextSignal={context.current.signal}
                      autoRoot={historyFocus} onDenied={onDenied}
                      onChanged={() => void refresh(true)}
                      onCorrection={(fact, payload) => void correct(fact, payload)}
                    />
                  </div>
                </div>
              )}
              {activePanel === "history" && !patient && (
                <div className="cc-chat-col">
                  <EmptyState icon={<IcoHistory />} title="No patient selected" sub="Select a patient to view clinical history." />
                </div>
              )}

              {activePanel === "medications" && patient && (
                <div className="cc-chat-col">
                  <div className="cc-chat-scroll">
                    <MedicationsPanel facts={accepted.filter(f => f.fact.kind === "medication")} />
                  </div>
                </div>
              )}
              {activePanel === "medications" && !patient && (
                <div className="cc-chat-col">
                  <EmptyState icon={<IcoPill />} title="No patient selected" sub="Select a patient to view medications." />
                </div>
              )}

              {activePanel === "verified" && patient && (
                <div className="cc-chat-col">
                  <div className="cc-chat-scroll">
                    <VerifiedRecords
                      key={`${contextVersion}:${patient}`}
                      token={session.token} patient={patient} facts={accepted} onDenied={onDenied}
                    />
                  </div>
                </div>
              )}
              {activePanel === "verified" && !patient && (
                <div className="cc-chat-col">
                  <EmptyState icon={<IcoVerify />} title="No patient selected" sub="Select a patient to request verified records." />
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Chat area ─────────────────────────────────────────────────────────────────
function ChatArea({ turns, proposals, busy, onDecision, inputTab, chatEndRef }: {
  turns: Turn[]; proposals: Proposal[]; busy: boolean;
  onDecision: (id: string, action: "confirm" | "reject") => void;
  inputTab: "note" | "ask";
  chatEndRef: React.RefObject<HTMLDivElement>;
}) {
  if (!turns.length && !busy) {
    return (
      <EmptyState
        icon={<IcoNote />}
        title={inputTab === "note" ? "Add a clinical note" : "Ask about records"}
        sub={inputTab === "note"
          ? "Type a synthetic clinical note below. Extracted facts will appear here for review."
          : "Ask a question about this patient's accepted records below."}
      />
    );
  }

  return (
    <>
      {turns.map(turn => turn.question ? (
        <div key={turn.id} className="cc-turn" aria-label="Record question turn">
          <div className="cc-msg-user" aria-label="Your record question">{turn.question}</div>
          <div className={`cc-msg-assistant ${turn.answer?.kind ?? "pending"}`}
            aria-label="Record assistant response" aria-busy={!turn.answer}
            role={turn.answer?.kind === "error" ? "alert" : "status"}>
            <strong className="cc-assistant-label">{turn.answer?.kind === "result" ? "Verified record response" : "Assistant"}</strong>
            {turn.answer ? turn.answer.text : <><Spinner /> Checking accepted records…</>}
          </div>
        </div>
      ) : (
        <div key={turn.id} className="cc-turn">
          {turn.text && (
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <div className="cc-msg-user">{turn.text}</div>
            </div>
          )}
          {!turn.text && (
            <div style={{ fontSize: "0.72rem", color: "#9ca3af", textAlign: "right", marginBottom: "0.25rem" }}>Earlier note</div>
          )}
          <div className="cc-proposals-group">
            {proposals.filter(p => p.source_message_id === turn.id).map(p => (
              <ProposalCard key={p.confirmation_id} proposal={p} busy={busy} onDecision={onDecision} />
            ))}
          </div>
        </div>
      ))}
      {busy && (
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", color: "#6b7280", fontSize: "0.82rem" }}>
          <Spinner /> Processing…
        </div>
      )}
      <div ref={chatEndRef} />
    </>
  );
}

// ─── Chat input ────────────────────────────────────────────────────────────────
function ChatInput({ tab, onTab, text, onText, busy, patient, onSend, token, onDenied, contextVersion, previousQuestions, onQuestion, onAnswer }: {
  tab: "note" | "ask"; onTab: (t: "note" | "ask") => void;
  text: string; onText: (v: string) => void;
  busy: boolean; patient: string;
  onSend: (e: FormEvent<HTMLFormElement>) => Promise<void>;
  token: string; onDenied: (s: number) => void;
  contextVersion: number;
  previousQuestions: string[];
  onQuestion: (id: string, question: string) => void;
  onAnswer: (id: string, answer: RecordAnswer) => void;
}) {
  return (
    <div className="cc-input-area">
      <div className="cc-tabs">
        <button className={`cc-tab ${tab === "note" ? "active" : ""}`} onClick={() => onTab("note")} type="button">
          <span style={{ display: "inline", marginRight: 4, verticalAlign: "middle" }}><IcoNote /></span>
          Add Clinical Note
        </button>
        <button className={`cc-tab ${tab === "ask" ? "active" : ""}`} onClick={() => onTab("ask")} type="button">
          <span style={{ display: "inline", marginRight: 4, verticalAlign: "middle" }}><IcoQuestion /></span>
          Ask Patient Records
        </button>
      </div>

      {tab === "note" && (
        <form onSubmit={onSend}>
          <div className="cc-input-row">
            <textarea
              className="cc-textarea"
              value={text}
              onChange={e => onText(e.target.value)}
              required
              maxLength={16000}
              rows={2}
              disabled={busy}
              placeholder="Enter a synthetic clinical note, e.g. HbA1c: 7.1% at 2026-01-01T10:00:00Z"
              aria-label="Clinical note"
            />
            <button className="cc-send-btn" type="submit" disabled={busy || !text.trim()} aria-label="Submit note for extraction">
              <IcoSend />
            </button>
          </div>
          <div className="cc-input-hint">
            Synthetic text only. Example: <code>HbA1c: 7.1 % at 2026-01-01T10:00:00Z</code>
          </div>
        </form>
      )}

      <div hidden={tab !== "ask"}>
        <RecordQuestionsInput
          key={`rq:${contextVersion}:${patient}`}
          token={token}
          patient={patient}
          onDenied={onDenied}
          previousQuestions={previousQuestions} onQuestion={onQuestion} onAnswer={onAnswer}
        />
      </div>
    </div>
  );
}

// ─── Record questions input (inline in chat) ───────────────────────────────────
function RecordQuestionsInput({ token, patient, onDenied, previousQuestions, onQuestion, onAnswer }: {
  token: string; patient: string; onDenied: (s: number) => void;
  previousQuestions: string[];
  onQuestion: (id: string, question: string) => void;
  onAnswer: (id: string, answer: RecordAnswer) => void;
}) {
  const [question, setQuestion] = useState("");
  const pendingQuestion = useRef<string | null>(null);
  const { begin, busy } = useDashboardTask();
  useEffect(() => () => {
    if (pendingQuestion.current) onAnswer(pendingQuestion.current, {
      kind: "error", text: "Request cancelled. Its outcome may be uncertain; no response was displayed. No automatic retry was made.",
    });
  }, [onAnswer]);

  async function ask(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!question.trim()) return;
    const task = begin(); if (!task) return;
    const asked = question.trim();
    const operationId = crypto.randomUUID();
    pendingQuestion.current = operationId;
    onQuestion(operationId, asked); setQuestion("");
    try {
      const result = decodeRecordQuestion(await request<unknown>(`/patients/${patient}/record-questions`, token,
        { question: asked, operation_id: operationId,
          ...(previousQuestions.length ? { previous_questions: previousQuestions } : {}) }, task.signal, 310000), false);
      if (!task.current() || task.signal.aborted) return;
      onAnswer(operationId, result.records.length ? { text: result.records.map(r => r.text).join("\n\n"), kind: "result" }
        : { text: result.notice, kind: "notice" });
    } catch (cause) {
      if (!task.current() || task.signal.aborted) return;
      if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) onDenied(cause.status);
      else onAnswer(operationId, { kind: "error", text: "No verified answer is available. Check Ollama or refresh changed records. No automatic retry was made; ask again explicitly." });
    } finally {
      pendingQuestion.current = null;
      task.done();
    }
  }

  return (
    <div>
      <form onSubmit={ask}>
        <div className="cc-input-row">
          <input
            className="cc-textarea"
            style={{ borderRadius: "var(--radius)", padding: "0.6rem 0.75rem", height: "auto" }}
            value={question}
            onChange={e => setQuestion(e.target.value)}
            required
            maxLength={500}
            disabled={busy}
            placeholder="e.g. What is my latest recorded HbA1c?"
            aria-label="Record question"
          />
          <button className="cc-send-btn" type="submit" disabled={busy || !question.trim()} aria-label="Ask">
            {busy ? <Spinner /> : <IcoSend />}
          </button>
        </div>
      </form>
      <div className="cc-input-hint">
        Ask naturally and follow up about the same patient. Answers use verified accepted records, not medical advice or treatment recommendations.
      </div>
      <details className="cc-advanced" style={{ marginTop: "0.6rem" }}>
        <summary>Supported question examples</summary>
        <div className="cc-advanced-body">
          <ul style={{ paddingLeft: "1rem", margin: 0 }}>
            <li>What confirmed clinical information is recorded?</li>
            <li>What is my latest recorded value for hba1c?</li>
            <li>What medications are currently recorded?</li>
            <li>What symptoms or relevant history have been recorded?</li>
          </ul>
          <p style={{ marginTop: "0.5rem" }}>Supported lab names: hba1c, glucose, creatinine, egfr, urine albumin creatinine ratio.</p>
        </div>
      </details>
    </div>
  );
}

// ─── Medications panel ─────────────────────────────────────────────────────────
function MedicationsPanel({ facts }: { facts: AcceptedFact[] }) {
  if (!facts.length) {
    return <EmptyState icon={<IcoPill />} title="No medications recorded" sub="Medications extracted from clinical notes will appear here." />;
  }
  return (
    <div style={{ padding: "1rem" }}>
      <div style={{ marginBottom: "0.75rem", fontSize: "0.8rem", color: "#6b7280" }}>
        {facts.length} medication{facts.length !== 1 ? "s" : ""} recorded. Recorded medications do not establish what is currently being taken.
      </div>
      {facts.map(f => (
        <div key={f.version_id} className="cc-card" style={{ marginBottom: "0.75rem" }}>
          <div className="cc-card-head">
            <div>
              <div style={{ fontSize: "0.65rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "#6b7280", marginBottom: "0.2rem" }}>Medication</div>
              <div style={{ fontWeight: 600, fontSize: "0.95rem" }}>
                {f.fact.kind === "medication" ? f.fact.medication_name : "—"}
              </div>
            </div>
            <span className={`cc-record-attr ${f.attribution === "practitioner_managed" ? "practitioner" : "patient"}`}>
              v{f.version}
            </span>
          </div>
          <div className="cc-card-body" style={{ fontSize: "0.82rem", color: "#374151" }}>
            {f.fact.kind === "medication" && (
              <>
                {f.fact.dose_value && <div><span style={{ color: "#6b7280" }}>Dose:</span> {f.fact.dose_value} {f.fact.dose_unit}</div>}
                {f.fact.frequency && <div><span style={{ color: "#6b7280" }}>Frequency:</span> {f.fact.frequency}</div>}
              </>
            )}
            <div style={{ marginTop: "0.4rem" }}><span style={{ color: "#6b7280" }}>Observed:</span> {formatDate(f.observed_at)}</div>
          </div>
        </div>
      ))}
    </div>
  );
}
