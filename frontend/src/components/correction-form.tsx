"use client";

import { FormEvent, useRef, useState } from "react";
import { AcceptedFact, CorrectionRequest, Fact, correctionRequest } from "./clinical-types";
import { FactDetails } from "./fact-card";

const blank = (value: string | null | undefined) => value?.trim() ? value : null;
function replacement(kind: Fact["kind"]): Fact {
  switch (kind) {
    case "laboratory": return { kind, code: "hba1c", value: "", unit: "%" };
    case "diagnosis": return { kind, code: "type_2_diabetes", description: "" };
    case "medication": return { kind, medication_name: "", dose_value: "", dose_unit: "", frequency: "" };
    case "symptom": return { kind, code: "other", description: "" };
    case "history": return { kind, category: "other", description: "" };
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
      const fact: Fact = draft.kind === "medication" ? { ...draft, dose_value: blank(draft.dose_value), dose_unit: blank(draft.dose_unit), frequency: blank(draft.frequency) }
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
    onSubmit(prepared); // Parent owns the write, context cancellation and uncertain-outcome recovery.
  }
  const invalidate = () => { reviewed.current = null; setReview(null); setError(""); };
  return <section className="fact-card" aria-label="Structured correction">
    <h3>Correct recorded information</h3>
    <p>This creates a new accepted version. The original and all previous versions remain preserved. This is record correction, not diagnosis, prescribing or a treatment change.</p>
    <p>Current accepted version {current.version}; fact root <span className="break-all">{current.fact_id}</span></p>
    <FactDetails fact={current.fact} />
    <form onSubmit={prepare} onChange={invalidate} aria-label="Correction form" aria-busy={submitting}>
      <fieldset disabled={submitting}>
        <label>Expected current version ID<input readOnly value={current.version_id} /></label>
        <label>Replacement fact type<select value={draft.kind} onChange={e => setDraft(replacement(e.target.value as Fact["kind"]))}>
          {(["laboratory", "diagnosis", "medication", "symptom", "history"] as const).map(kind => <option key={kind} value={kind}>{kind}</option>)}
        </select></label>
        {draft.kind === "laboratory" && <>
          <label>Laboratory code<select value={draft.code} onChange={e => edit("code", e.target.value)}>{["hba1c", "glucose", "creatinine", "egfr", "urine_albumin_creatinine_ratio"].map(code => <option key={code} value={code}>{code}</option>)}</select></label>
          <label>Laboratory value<input type="text" inputMode="decimal" value={draft.value} onChange={e => edit("value", e.target.value)} required maxLength={200} /></label>
          <label>Laboratory unit<input value={draft.unit} onChange={e => edit("unit", e.target.value)} required maxLength={400} /></label>
        </>}
        {draft.kind === "diagnosis" && <><p>Diagnosis code: type_2_diabetes</p><label>Diagnosis description (optional)<textarea value={draft.description ?? ""} onChange={e => edit("description", e.target.value)} maxLength={4000} /></label></>}
        {draft.kind === "medication" && <>
          <label>Recorded medication name<input value={draft.medication_name} onChange={e => edit("medication_name", e.target.value)} required maxLength={400} /></label>
          <label>Recorded dose value (optional)<input type="text" inputMode="decimal" value={draft.dose_value ?? ""} onChange={e => edit("dose_value", e.target.value)} maxLength={200} /></label>
          <label>Recorded dose unit (optional)<input value={draft.dose_unit ?? ""} onChange={e => edit("dose_unit", e.target.value)} maxLength={400} /></label>
          <label>Recorded frequency (optional)<input value={draft.frequency ?? ""} onChange={e => edit("frequency", e.target.value)} maxLength={400} /></label>
        </>}
        {draft.kind === "symptom" && <><label>Symptom code<select value={draft.code} onChange={e => edit("code", e.target.value)}>{["polyuria", "polydipsia", "fatigue", "other"].map(code => <option key={code} value={code}>{code}</option>)}</select></label><label>Symptom description<textarea value={draft.description} onChange={e => edit("description", e.target.value)} required maxLength={4000} /></label></>}
        {draft.kind === "history" && <><label>History category<select value={draft.category} onChange={e => edit("category", e.target.value)}>{["family_history", "allergy", "smoking", "prior_event", "other"].map(code => <option key={code} value={code}>{code}</option>)}</select></label><label>History description<textarea value={draft.description} onChange={e => edit("description", e.target.value)} required maxLength={4000} /></label></>}
        <label>Observed at (ISO timestamp with timezone)<input value={observed} onChange={e => setObserved(e.target.value)} required maxLength={100} /></label>
        <label>Provenance source<input value={source} onChange={e => setSource(e.target.value)} required maxLength={400} /></label>
        <label>Provenance reference (optional)<textarea value={reference} onChange={e => setReference(e.target.value)} maxLength={4000} /></label>
        <label>Data origin<select value={origin} onChange={e => setOrigin(e.target.value as typeof origin)}><option value="synthetic">synthetic</option><option value="de_identified">de_identified</option></select></label>
        <label>Correction reason<textarea aria-label="Correction reason" value={reason} onChange={e => setReason(e.target.value)} required maxLength={4000} /></label>
        <button>Review correction</button>
        <button type="button" className="secondary" onClick={onCancel}>Cancel correction</button>
      </fieldset>
    </form>
    {error && <p role="alert" aria-label="Correction validation error" className="error">{error}</p>}
    {review && <section aria-label="Review new version">
      <h4>Review new version</h4><FactDetails fact={review.fact} />
      <p>Observed: {review.observed_at}</p><p>Source: {review.provenance.source}</p><p>Reference: {review.provenance.reference ?? "None"}</p><p>Data origin: {review.provenance.data_origin}</p>
      <p className="break-all">Expected current version: {review.expected_version_id}</p><p>Reason: {review.correction_reason}</p>
      <p>Explicit submission accepts validated structured information. No LLM extraction or automatic treatment action occurs.</p>
      <button disabled={submitting} onClick={submit}>Submit new version</button>
    </section>}
  </section>;
}
