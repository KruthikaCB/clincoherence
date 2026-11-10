"use client";

import { FormEvent, useRef, useState } from "react";
import { AcceptedFact, CorrectionRequest, Fact, correctionRequest } from "./clinical-types";
import { FactDetails } from "./fact-card";

const blank = (value: string | null | undefined) => value?.trim() ? value : null;
function replacement(kind: Fact["kind"]): Fact {
  switch (kind) {
    case "laboratory":  return { kind, code: "hba1c", value: "", unit: "%" };
    case "diagnosis":   return { kind, code: "type_2_diabetes", description: "" };
    case "medication":  return { kind, medication_name: "", dose_value: "", dose_unit: "", frequency: "" };
    case "symptom":     return { kind, code: "other", description: "" };
    case "history":     return { kind, category: "other", description: "" };
  }
}

export default function CorrectionForm({ current, onSubmit, onCancel }: {
  current: AcceptedFact; onSubmit: (request: CorrectionRequest) => void; onCancel: () => void;
}) {
  const [draft, setDraft] = useState<Fact>(current.fact);
  const [observed, setObserved] = useState(current.observed_at);
  const [source, setSource] = useState(current.provenance.source);
  const [reference, setReference] = useState(current.provenance.reference ?? "");
  const [origin, setOrigin] = useState(current.provenance.data_origin);
  const [reason, setReason] = useState("");
  const [review, setReview] = useState<CorrectionRequest | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const sent = useRef(false);
  const reviewed = useRef<CorrectionRequest | null>(null);

  function edit(key: string, value: string) { reviewed.current = null; setDraft(previous => ({ ...previous, [key]: value })); setReview(null); }
  function prepare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (sent.current) return;
    reviewed.current = null; setReview(null); setError("");
    try {
      const fact: Fact = draft.kind === "medication"
        ? { ...draft, dose_value: blank(draft.dose_value), dose_unit: blank(draft.dose_unit), frequency: blank(draft.frequency) }
        : draft.kind === "diagnosis" ? { ...draft, description: blank(draft.description) } : draft;
      const prepared = correctionRequest({ fact, observed_at: observed.trim(), provenance: { source: source.trim(), reference: reference.trim() || null, data_origin: origin }, expected_version_id: current.version_id, correction_reason: reason.trim() });
      reviewed.current = prepared; setReview(prepared);
    } catch { setError("Check the replacement fields, supported code/unit, decimal precision, timezone-aware timestamp, provenance and nonempty correction reason."); }
  }
  function submit() {
    const prepared = reviewed.current;
    if (!prepared || sent.current) return;
    reviewed.current = null;
    sent.current = true; setSubmitting(true);
    onSubmit(prepared);
  }
  const invalidate = () => { reviewed.current = null; setReview(null); setError(""); };

  return (
    <div className="cc-card" role="region" aria-label="Structured correction">
      <div className="cc-card-head">
        <div>
          <h3 style={{ margin: 0 }}>Correct recorded information</h3>
          <div style={{ fontSize: "0.72rem", color: "#6b7280", marginTop: "0.15rem" }}>
            Creates a new accepted version. Original and prior versions are preserved.
          </div>
        </div>
        <button className="cc-btn cc-btn-ghost cc-btn-xs" onClick={onCancel}>Cancel</button>
      </div>
      <div className="cc-card-body">
        {/* Current version summary */}
        <div style={{ padding: "0.6rem 0.75rem", background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: "var(--radius)", marginBottom: "1rem", fontSize: "0.82rem" }}>
          <div style={{ fontSize: "0.7rem", fontWeight: 600, color: "#6b7280", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "0.25rem" }}>Current version {current.version}</div>
          <FactDetails fact={current.fact} />
        </div>

        <form onSubmit={prepare} onChange={invalidate} aria-label="Correction form" aria-busy={submitting}>
          <fieldset disabled={submitting} style={{ border: "none", padding: 0, margin: 0 }}>
            <label className="cc-label" style={{ marginBottom: "0.65rem" }}>
              Replacement fact type
              <select className="cc-select" value={draft.kind} onChange={e => setDraft(replacement(e.target.value as Fact["kind"]))}>
                {(["laboratory", "diagnosis", "medication", "symptom", "history"] as const).map(kind => (
                  <option key={kind} value={kind}>{kind}</option>
                ))}
              </select>
            </label>

            {draft.kind === "laboratory" && (
              <>
                <label className="cc-label" style={{ marginBottom: "0.5rem" }}>
                  Laboratory code
                  <select className="cc-select" value={draft.code} onChange={e => edit("code", e.target.value)}>
                    {["hba1c", "glucose", "creatinine", "egfr", "urine_albumin_creatinine_ratio"].map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </label>
                <label className="cc-label" style={{ marginBottom: "0.5rem" }}>
                  Value
                  <input type="text" inputMode="decimal" className="cc-input" value={draft.value} onChange={e => edit("value", e.target.value)} required maxLength={200} />
                </label>
                <label className="cc-label" style={{ marginBottom: "0.65rem" }}>
                  Unit
                  <input className="cc-input" value={draft.unit} onChange={e => edit("unit", e.target.value)} required maxLength={400} />
                </label>
              </>
            )}
            {draft.kind === "diagnosis" && (
              <label className="cc-label" style={{ marginBottom: "0.65rem" }}>
                Description (optional)
                <span style={{ fontSize: "0.72rem", color: "#9ca3af", fontWeight: 400 }}>Diagnosis code: type_2_diabetes</span>
                <textarea className="cc-input" value={draft.description ?? ""} onChange={e => edit("description", e.target.value)} maxLength={4000} rows={2} />
              </label>
            )}
            {draft.kind === "medication" && (
              <>
                <label className="cc-label" style={{ marginBottom: "0.5rem" }}>Medication name<input className="cc-input" value={draft.medication_name} onChange={e => edit("medication_name", e.target.value)} required maxLength={400} /></label>
                <label className="cc-label" style={{ marginBottom: "0.5rem" }}>Dose value (optional)<input type="text" inputMode="decimal" className="cc-input" value={draft.dose_value ?? ""} onChange={e => edit("dose_value", e.target.value)} maxLength={200} /></label>
                <label className="cc-label" style={{ marginBottom: "0.5rem" }}>Dose unit (optional)<input className="cc-input" value={draft.dose_unit ?? ""} onChange={e => edit("dose_unit", e.target.value)} maxLength={400} /></label>
                <label className="cc-label" style={{ marginBottom: "0.65rem" }}>Frequency (optional)<input className="cc-input" value={draft.frequency ?? ""} onChange={e => edit("frequency", e.target.value)} maxLength={400} /></label>
              </>
            )}
            {draft.kind === "symptom" && (
              <>
                <label className="cc-label" style={{ marginBottom: "0.5rem" }}>Symptom code<select className="cc-select" value={draft.code} onChange={e => edit("code", e.target.value)}>{["polyuria", "polydipsia", "fatigue", "other"].map(c => <option key={c} value={c}>{c}</option>)}</select></label>
                <label className="cc-label" style={{ marginBottom: "0.65rem" }}>Description<textarea className="cc-input" value={draft.description} onChange={e => edit("description", e.target.value)} required maxLength={4000} rows={2} /></label>
              </>
            )}
            {draft.kind === "history" && (
              <>
                <label className="cc-label" style={{ marginBottom: "0.5rem" }}>Category<select className="cc-select" value={draft.category} onChange={e => edit("category", e.target.value)}>{["family_history", "allergy", "smoking", "prior_event", "other"].map(c => <option key={c} value={c}>{c}</option>)}</select></label>
                <label className="cc-label" style={{ marginBottom: "0.65rem" }}>Description<textarea className="cc-input" value={draft.description} onChange={e => edit("description", e.target.value)} required maxLength={4000} rows={2} /></label>
              </>
            )}

            <label className="cc-label" style={{ marginBottom: "0.5rem" }}>
              Observed at (ISO timestamp with timezone)
              <input className="cc-input" value={observed} onChange={e => setObserved(e.target.value)} required maxLength={100} placeholder="2026-01-01T10:00:00Z" />
            </label>

            {/* Provenance in advanced section */}
            <details className="cc-advanced" style={{ marginBottom: "0.65rem" }}>
              <summary>Provenance (source, reference, data origin)</summary>
              <div className="cc-advanced-body" style={{ marginTop: "0.4rem" }}>
                <label className="cc-label" style={{ marginBottom: "0.4rem" }}>Provenance source<input className="cc-input" value={source} onChange={e => setSource(e.target.value)} required maxLength={400} /></label>
                <label className="cc-label" style={{ marginBottom: "0.4rem" }}>Reference (optional)<textarea className="cc-input" value={reference} onChange={e => setReference(e.target.value)} maxLength={4000} rows={2} /></label>
                <label className="cc-label">Data origin<select className="cc-select" value={origin} onChange={e => setOrigin(e.target.value as typeof origin)}><option value="synthetic">synthetic</option><option value="de_identified">de_identified</option></select></label>
                {/* Expected version for auditability */}
                <label className="cc-label" style={{ marginTop: "0.4rem" }}>Expected current version ID (read-only)<input readOnly className="cc-input" value={current.version_id} style={{ fontSize: "0.75rem", color: "#6b7280" }} /></label>
              </div>
            </details>

            <label className="cc-label" style={{ marginBottom: "0.75rem" }}>
              Correction reason (required)
              <textarea className="cc-input" value={reason} onChange={e => setReason(e.target.value)} required maxLength={4000} rows={2} placeholder="Describe why this record is being corrected…" />
            </label>

            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button className="cc-btn cc-btn-primary cc-btn-sm">Review correction</button>
              <button type="button" className="cc-btn cc-btn-ghost cc-btn-sm" onClick={onCancel}>Cancel</button>
            </div>
          </fieldset>
        </form>

        {error && <div className="cc-banner error" role="alert" aria-label="Correction validation error" style={{ marginTop: "0.65rem" }}>{error}</div>}

        {review && (
          <div style={{ marginTop: "0.75rem", padding: "0.75rem", background: "#f0fdf4", border: "1px solid #86efac", borderRadius: "var(--radius)" }} role="region" aria-label="Review new version">
            <div style={{ fontWeight: 600, fontSize: "0.82rem", marginBottom: "0.5rem", color: "#15803d" }}>Review new version</div>
            <FactDetails fact={review.fact} />
            <div style={{ fontSize: "0.78rem", color: "#374151", marginTop: "0.4rem" }}>
              <div>Observed: {review.observed_at}</div>
              <div>Reason: {review.correction_reason}</div>
            </div>
            <details className="cc-advanced" style={{ marginTop: "0.4rem" }}>
              <summary>Provenance</summary>
              <div className="cc-advanced-body">
                <div>Source: {review.provenance.source}</div>
                {review.provenance.reference && <div>Reference: {review.provenance.reference}</div>}
                <div>Data origin: {review.provenance.data_origin}</div>
              </div>
            </details>
            <div style={{ fontSize: "0.75rem", color: "#6b7280", margin: "0.5rem 0" }}>
              Explicit submission accepts validated structured information. No LLM extraction or automatic treatment action occurs.
            </div>
            <button className="cc-btn cc-btn-primary cc-btn-sm" disabled={submitting} onClick={submit}>
              {submitting ? <><span className="cc-spinner" /> Submitting…</> : "Submit correction"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
