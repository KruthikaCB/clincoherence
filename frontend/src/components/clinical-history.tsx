"use client";

import { useEffect, useRef, useState } from "react";
import { Account, AcceptedFact, acceptedHistory, attribution, CorrectionRequest, sameAcceptedVersion } from "./clinical-types";
import CorrectionForm from "./correction-form";
import { FactDetails } from "./fact-card";
import { ApiError, request, useDashboardTask } from "./patient-selector";

function formatDate(iso: string): string {
  try { return new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }); }
  catch { return iso; }
}

function VersionDetails({ version, current }: { version: AcceptedFact; current: boolean }) {
  return (
    <div className="cc-history-version">
      <div className="cc-history-version-head">
        <div>
          <span style={{ fontWeight: 600, fontSize: "0.82rem" }}>Version {version.version}</span>
          {" "}
          <span style={{ fontSize: "0.72rem", padding: "0.1rem 0.45rem", borderRadius: 999, background: current ? "#dcfce7" : "#f1f5f9", color: current ? "#15803d" : "#64748b", fontWeight: 600 }}>
            {current ? "Current" : "Superseded"}
          </span>
        </div>
        <span style={{ fontSize: "0.72rem", color: "#6b7280" }}>{attribution(version.attribution)}</span>
      </div>
      <div className="cc-history-version-body">
        <FactDetails fact={version.fact} />
        <div style={{ marginTop: "0.4rem" }}>
          <div className="cc-fact-row"><span className="cc-fact-label">Observed</span><span className="cc-fact-val"><time dateTime={version.observed_at}>{formatDate(version.observed_at)}</time></span></div>
          <div className="cc-fact-row"><span className="cc-fact-label">Entered</span><span className="cc-fact-val"><time dateTime={version.entered_at}>{formatDate(version.entered_at)}</time></span></div>
          <div className="cc-fact-row"><span className="cc-fact-label">Method</span><span className="cc-fact-val">{version.entry_method}</span></div>
          {version.correction_reason && <div className="cc-fact-row"><span className="cc-fact-label">Reason</span><span className="cc-fact-val">{version.correction_reason}</span></div>}
        </div>
        {/* Advanced: UUIDs and provenance collapsed */}
        <details className="cc-advanced" style={{ marginTop: "0.5rem" }}>
          <summary>Technical details</summary>
          <div className="cc-advanced-body">
            <dl>
              <dt>Patient ID</dt><dd>{version.patient_id}</dd>
              <dt>Fact root ID</dt><dd>{version.fact_id}</dd>
              <dt>Version ID</dt><dd>{version.version_id}</dd>
              <dt>Predecessor</dt><dd>{version.previous_version_id ?? "None (original)"}</dd>
              <dt>Author role</dt><dd>{version.author_role}</dd>
              <dt>Author ID</dt><dd>{version.author_id}</dd>
              <dt>Source</dt><dd>{version.provenance.source}</dd>
              <dt>Data origin</dt><dd>{version.provenance.data_origin}</dd>
              {version.provenance.reference && <><dt>Reference</dt><dd>{version.provenance.reference}</dd></>}
              {version.confirmed_by_id && <><dt>Confirmed by</dt><dd>{version.confirmed_by_id}</dd><dt>Confirmed at</dt><dd>{version.confirmed_at}</dd></>}
            </dl>
          </div>
        </details>
      </div>
    </div>
  );
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
      const selected = versions.find(v => v.version_id === fact.version_id);
      if (!selected || !sameAcceptedVersion(selected, fact)) throw new Error();
      const last = versions[versions.length - 1];
      if (last.version_id !== fact.version_id) { onChanged(); return; }
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

  const loader = useRef(show);
  loader.current = show;
  useEffect(() => {
    if (autoRoot) {
      const fact = facts.find(r => r.fact_id === autoRoot);
      if (fact) void loader.current(fact);
    }
  }, [autoRoot, facts]);

  return (
    <div style={{ padding: "1rem" }} aria-label="Clinical History">
      <div style={{ marginBottom: "0.75rem" }}>
        <div style={{ fontSize: "0.75rem", color: "#6b7280", lineHeight: 1.5 }}>
          Current accepted shared records. Acceptance is not medical correctness.
          Technically verified restatements are available in Verified Records.
        </div>
      </div>

      {!facts.length && (
        <div style={{ padding: "2rem", textAlign: "center", color: "#9ca3af", fontSize: "0.82rem" }}>
          No accepted facts loaded for this patient.
        </div>
      )}

      {facts.map(fact => (
        <div key={fact.version_id} className="cc-card" style={{ marginBottom: "0.65rem" }}>
          <div className="cc-card-head">
            <div>
              <div style={{ fontSize: "0.65rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "#6b7280", marginBottom: "0.15rem" }}>
                {fact.fact.kind}
              </div>
              <FactDetails fact={fact.fact} />
            </div>
            <div style={{ textAlign: "right", flexShrink: 0 }}>
              <span className={`cc-record-attr ${fact.attribution === "practitioner_managed" ? "practitioner" : "patient"}`}>
                v{fact.version}
              </span>
            </div>
          </div>
          <div className="cc-card-foot" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "0.4rem" }}>
            <span style={{ fontSize: "0.72rem", color: "#6b7280" }}>
              Observed: <time dateTime={fact.observed_at}>{formatDate(fact.observed_at)}</time>
            </span>
            <div style={{ display: "flex", gap: "0.4rem" }}>
              <button
                className="cc-btn cc-btn-ghost cc-btn-xs"
                disabled={busy}
                onClick={() => void show(fact)}
                aria-expanded={expanded?.snapshot === facts && expanded.root === fact.fact_id}
              >
                {expanded?.snapshot === facts && expanded.root === fact.fact_id ? "Hide history" : "View history"}
              </button>
              {canCorrect(fact) && (
                <button className="cc-btn cc-btn-ghost cc-btn-xs" disabled={busy} onClick={() => void show(fact, true)}>
                  Correct
                </button>
              )}
            </div>
          </div>
        </div>
      ))}

      {busy && (
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", color: "#6b7280", fontSize: "0.82rem", padding: "0.5rem 0" }}>
          <span className="cc-spinner" /> Loading history…
        </div>
      )}
      {error && <div className="cc-banner error" role="alert">{error}</div>}

      {editing && expanded?.snapshot === facts && (
        <div style={{ marginTop: "0.75rem" }}>
          <CorrectionForm
            key={editing.version_id}
            current={editing}
            onSubmit={payload => onCorrection(editing, payload)}
            onCancel={() => setEditing(null)}
          />
        </div>
      )}

      {expanded?.snapshot === facts && (
        <div style={{ marginTop: "0.75rem" }} aria-label="Accepted correction history" aria-live="polite">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "0.65rem" }}>
            <h3 style={{ margin: 0, fontSize: "0.82rem" }}>Correction history</h3>
            <button className="cc-btn cc-btn-ghost cc-btn-xs" disabled={busy} onClick={() => setExpanded(null)}>Close</button>
          </div>
          {expanded.versions.map((version, index) => (
            <VersionDetails key={version.version_id} version={version} current={index === expanded.versions.length - 1} />
          ))}
        </div>
      )}
    </div>
  );
}
