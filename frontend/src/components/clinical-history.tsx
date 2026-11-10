"use client";

import { useEffect, useRef, useState } from "react";
import { Account, AcceptedFact, acceptedHistory, attribution, CorrectionRequest, sameAcceptedVersion } from "./clinical-types";
import CorrectionForm from "./correction-form";
import { FactDetails } from "./fact-card";
import { ApiError, request, useDashboardTask } from "./patient-selector";

function VersionDetails({ version, current }: { version: AcceptedFact; current: boolean }) {
  return <article className="fact-card" aria-label="Clinical fact version">
    <h4>Version {version.version} — {current ? "Current accepted version" : "Superseded"}</h4>
    <strong>{attribution(version.attribution)}</strong>
    <FactDetails fact={version.fact} />
    <dl className="fact-details break-all">
      <div><dt>Patient ID</dt><dd>{version.patient_id}</dd></div>
      <div><dt>Fact root ID</dt><dd>{version.fact_id}</dd></div>
      <div><dt>Version ID</dt><dd>{version.version_id}</dd></div>
      <div><dt>Predecessor version ID</dt><dd>{version.previous_version_id ?? "None (original version)"}</dd></div>
      <div><dt>Observation timestamp</dt><dd><time dateTime={version.observed_at}>{version.observed_at}</time></dd></div>
      <div><dt>Entry timestamp</dt><dd><time dateTime={version.entered_at}>{version.entered_at}</time></dd></div>
      <div><dt>Author role</dt><dd>{version.author_role}</dd></div>
      <div><dt>Author account ID</dt><dd>{version.author_id}</dd></div>
      <div><dt>Entry method</dt><dd>{version.entry_method}</dd></div>
      <div><dt>Source</dt><dd>{version.provenance.source}</dd></div>
      <div><dt>Source reference (metadata only)</dt><dd>{version.provenance.reference ?? "None"}</dd></div>
      <div><dt>Data origin declaration</dt><dd>{version.provenance.data_origin}</dd></div>
      <div><dt>Correction reason</dt><dd>{version.correction_reason ?? "Original entry"}</dd></div>
      {version.confirmed_by_id && <>
        <div><dt>Confirmed by account ID</dt><dd>{version.confirmed_by_id}</dd></div>
        <div><dt>Confirmation timestamp</dt><dd><time dateTime={version.confirmed_at!}>{version.confirmed_at}</time></dd></div>
      </>}
    </dl>
    {version.entry_method === "STRUCTURED" && <p>Accepted structured entry; no LLM-extraction confirmation step applies.</p>}
  </article>;
}

export default function ClinicalHistory({ token, patient, role, facts, contextSignal, autoRoot, onDenied, onChanged, onCorrection }: {
  token: string; patient: string; facts: AcceptedFact[];
  role: Account["role"]; autoRoot: string | null;
  contextSignal: AbortSignal;
  onDenied: (status: number) => void; onChanged: () => void;
  onCorrection: (fact: AcceptedFact, request: CorrectionRequest) => void;
}) {
  const [expanded, setExpanded] = useState<{ snapshot: AcceptedFact[]; root: string; versions: AcceptedFact[] } | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<AcceptedFact | null>(null);
  const { begin, busy } = useDashboardTask();
  const canCorrect = (fact: AcceptedFact) => fact.attribution === (role === "patient" ? "patient_reported" : "practitioner_managed");
  async function show(fact: AcceptedFact, edit = false) {
    const task = begin(); if (!task) return;
    const controller = new AbortController();
    const abort = () => controller.abort();
    task.signal.addEventListener("abort", abort, { once: true });
    contextSignal.addEventListener("abort", abort, { once: true });
    if (task.signal.aborted || contextSignal.aborted) abort();
    const current = () => task.current() && !controller.signal.aborted;
    setExpanded(null); setEditing(null); setError("");
    try {
      const value = await request<unknown>(`/patients/${patient}/facts/${fact.fact_id}/history`, token, undefined, controller.signal);
      if (!current()) return;
      const versions = acceptedHistory(value, patient, fact.fact_id);
      const selected = versions.find(version => version.version_id === fact.version_id);
      if (!selected || !sameAcceptedVersion(selected, fact)) throw new Error();
      const last = versions[versions.length - 1];
      if (last.version_id !== fact.version_id) {
        // Never mix a newer history chain with the older current/verified-record views.
        onChanged(); return;
      }
      setExpanded({ snapshot: facts, root: fact.fact_id, versions });
      if (edit && canCorrect(fact)) setEditing(fact);
    } catch (cause) {
      if (!current()) return;
      setExpanded(null);
      if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) onDenied(cause.status);
      else setError("Clinical history unavailable or inconsistent. Refresh server state before trying again.");
    } finally {
      task.signal.removeEventListener("abort", abort);
      contextSignal.removeEventListener("abort", abort);
      task.done();
    }
  }
  // Effect replays are safe GETs. Writes only come from explicit reviewed form submission.
  const loader = useRef(show);
  loader.current = show;
  useEffect(() => {
    if (autoRoot) {
      const fact = facts.find(row => row.fact_id === autoRoot);
      if (fact) void loader.current(fact);
    }
  }, [autoRoot, facts]);
  return <section className="panel" aria-label="Clinical History">
    <h2>Clinical History</h2>
    <p>Current accepted shared records. Pending and rejected proposals remain in your conversation. Acceptance is not medical correctness; technically verified server restatements are shown separately in Verified Records.</p>
    <p>Observation and entry times are distinct. Source references are metadata only and do not grant access to private conversations.</p>
    {!facts.length && <p>No accepted facts loaded.</p>}
    {facts.map(fact => <article className="fact-card" key={fact.version_id} aria-label="Accepted fact">
      <strong>{attribution(fact.attribution)}</strong><FactDetails fact={fact.fact} />
      <p>Observed: <time dateTime={fact.observed_at}>{fact.observed_at}</time></p>
      <p>Current accepted version {fact.version}</p>
      <button className="secondary" disabled={busy} onClick={() => void show(fact)} aria-expanded={expanded?.snapshot === facts && expanded.root === fact.fact_id} aria-controls={`history-${fact.fact_id}`}>View details/history</button>
      {canCorrect(fact) && <button className="secondary" disabled={busy} onClick={() => void show(fact, true)}>Correct record</button>}
    </article>)}
    {busy && <p role="status">Loading clinical history...</p>}
    {error && <p className="error" role="alert" aria-label="Clinical history error">{error}</p>}
    {editing && expanded?.snapshot === facts && <CorrectionForm key={editing.version_id} current={editing} onSubmit={payload => onCorrection(editing, payload)} onCancel={() => setEditing(null)} />}
    {expanded?.snapshot === facts && <section id={`history-${expanded.root}`} aria-label="Accepted correction history" aria-live="polite">
      <h3>Accepted correction history</h3>
      <button className="secondary" disabled={busy} onClick={() => setExpanded(null)}>Close details/history</button>
      {expanded.versions.map((version, index) => <VersionDetails key={version.version_id} version={version} current={index === expanded.versions.length - 1} />)}
    </section>}
  </section>;
}
