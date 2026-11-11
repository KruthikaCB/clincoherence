"use client";

import { useState } from "react";
import { acceptedFact, AcceptedFact, attribution } from "./clinical-types";
import { ApiError, exact, request, useDashboardTask, uuid } from "./patient-selector";

type RecordSource = { source: AcceptedFact; text: string };
type Observation = { observed_at: string; status: "RECORDED" | "AMBIGUOUS"; value: string | null; unit: string | null; records: RecordSource[] };
type Summary = { points: Observation[]; latest: Observation | null; previous: Observation | null; egfr: Observation | null; medications: RecordSource[]; missing: string[]; direction: string; reason: string };
const missingLabels: Record<string, string> = { hba1c: "HbA1c", previous_hba1c: "Previous HbA1c", egfr: "eGFR", medications: "Recorded medications" };
const directions: Record<string, string> = { INCREASED: "Increased", DECREASED: "Decreased", UNCHANGED: "Unchanged", UNAVAILABLE: "Unavailable", NOT_COMPARABLE: "Not comparable" };
const date = (value: unknown): value is string => typeof value === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
function ids(value: unknown): string[] {
  if (!Array.isArray(value) || value.some(v => typeof v !== "string" || !uuid.test(v)) || new Set(value).size !== value.length) throw new Error();
  return value;
}
// Canonical decimal spelling for validation only. Never float-round measurements.
function decimal(value: unknown): string {
  if (typeof value !== "string" || value.length > 200) throw new Error();
  const p = /^([+-]?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d{1,3}))?$/.exec(value);
  if (!p) throw new Error();
  let digits = (p[2] + (p[3] ?? "")).replace(/^0+/, "");
  let scale = (p[3]?.length ?? 0) - Number(p[4] ?? 0);
  if (!digits) return "0";
  while (digits.endsWith("0")) { digits = digits.slice(0, -1); scale--; }
  return `${p[1] === "-" ? "-" : ""}${digits}e${-scale}`;
}

function decode(value: unknown, patient: string): Summary {
  const body = exact(value, "representation_version,summary,verification_scope");
  if (body.representation_version !== "clinical-summary-json-v1" || body.verification_scope !== "RECORDED_STATE_ONLY_NOT_CLINICAL_CORRECTNESS") throw new Error();
  const s = exact(body.summary, "hba1c_change,hba1c_observations,laboratory_policy_version,latest_egfr,latest_hba1c,medications,missing_information,patient_id,policy_version,previous_hba1c,renderer_version,snapshot,text,verification_policy_version");
  if (s.patient_id !== patient || s.policy_version !== "recorded-t2dm-summary-v1" || s.verification_policy_version !== "structured-fact-restatement-v1" || s.laboratory_policy_version !== "laboratory-explicit-pairs-v1" || s.renderer_version !== "record-restatement-text-v2" || typeof s.text !== "string") throw new Error();
  const snapshot = exact(s.snapshot, "current_version_ids,history_version_ids");
  const current = ids(snapshot.current_version_ids), history = ids(snapshot.history_version_ids);
  if (current.some(id => !history.includes(id))) throw new Error();
  function record(value: unknown): RecordSource {
    const r = exact(value, "source,verification");
    const raw = exact(r.source, "attribution,author_id,author_role,confirmed_at,confirmed_by_id,correction_reason,entered_at,entry_method,fact,fact_id,observed_at,patient_id,previous_version,previous_version_id,provenance,status,version,version_id");
    const { previous_version, ...fields } = raw;
    const source = acceptedFact(fields, patient);
    if (!current.includes(source.version_id) || (source.version === 1 ? previous_version !== null : !Number.isInteger(previous_version) || (previous_version as number) < 1 || (previous_version as number) >= source.version || !history.includes(source.previous_version_id!))) throw new Error();
    const v = exact(r.verification, "checks_not_applicable,checks_run,claim_id,fact_version_ids,outcome,reason_code,rendered_text,text_verified");
    if (typeof v.claim_id !== "string" || !uuid.test(v.claim_id) || v.outcome !== "GROUNDED" || v.reason_code !== "FACT_RESTATEMENT_MATCH" || v.text_verified !== true || typeof v.rendered_text !== "string" || !v.rendered_text.trim() || ids(v.fact_version_ids).join() !== source.version_id || !Array.isArray(v.checks_run) || !Array.isArray(v.checks_not_applicable)) throw new Error();
    return { source, text: v.rendered_text };
  }
  function observation(value: unknown, code: string): Observation | null {
    if (value === null) return null;
    const p = exact(value, "observed_at,records,status,unit,value");
    if (!date(p.observed_at) || !Array.isArray(p.records) || !p.records.length) throw new Error();
    const records = p.records.map(record);
    if (new Set(records.map(r => r.source.version_id)).size !== records.length) throw new Error();
    const values = new Set<string>();
    for (const { source } of records) {
      if (source.fact.kind !== "laboratory" || source.fact.code !== code || Date.parse(source.observed_at) !== Date.parse(p.observed_at)) throw new Error();
      values.add(`${decimal(source.fact.value)}:${source.fact.unit}`);
    }
    if (p.status === "RECORDED") {
      if (typeof p.unit !== "string" || values.size !== 1 || !values.has(`${decimal(p.value)}:${p.unit}`)) throw new Error();
    } else if (p.status !== "AMBIGUOUS" || p.value !== null || p.unit !== null || values.size < 2) throw new Error();
    return { observed_at: p.observed_at, status: p.status, value: p.value as string | null, unit: p.unit as string | null, records };
  }
  if (!Array.isArray(s.hba1c_observations) || !Array.isArray(s.medications) || !Array.isArray(s.missing_information)) throw new Error();
  const points = s.hba1c_observations.map(v => { const p = observation(v, "hba1c"); if (!p) throw new Error(); return p; });
  const latest = observation(s.latest_hba1c, "hba1c"), previous = observation(s.previous_hba1c, "hba1c"), egfr = observation(s.latest_egfr, "egfr");
  if (JSON.stringify(latest) !== JSON.stringify(points[0] ?? null) || JSON.stringify(previous) !== JSON.stringify(points[1] ?? null)) throw new Error();
  const medications = s.medications.map(record);
  if (medications.some(r => r.source.fact.kind !== "medication")) throw new Error();
  const missing = s.missing_information;
  if (missing.some(v => typeof v !== "string" || !Object.hasOwn(missingLabels, v)) || new Set(missing).size !== missing.length) throw new Error();
  const c = exact(s.hba1c_change, "comparison,direction,reason_code");
  if (typeof c.direction !== "string" || !Object.hasOwn(directions, c.direction) || typeof c.reason_code !== "string" || !/^[A-Z_]+$/.test(c.reason_code)) throw new Error();
  if (["INCREASED", "DECREASED", "UNCHANGED"].includes(c.direction)) {
    const comparison = exact(c.comparison, "earlier_version_id,later_version_id,numeric_difference,rule_id,unit");
    if (!previous || !latest || previous.status !== "RECORDED" || latest.status !== "RECORDED" || comparison.rule_id !== "LC-LAB-DIFFERENCE-01" || comparison.unit !== latest.unit || latest.unit !== previous.unit || !previous.records.some(r => r.source.version_id === comparison.earlier_version_id) || !latest.records.some(r => r.source.version_id === comparison.later_version_id)) throw new Error();
    decimal(comparison.numeric_difference);
  } else if (c.comparison !== null) throw new Error();
  return { points, latest, previous, egfr, medications, missing, direction: c.direction, reason: c.reason_code };
}

function Provenance({ records }: { records: RecordSource[] }) {
  return <details className="cc-advanced"><summary>Source provenance</summary>{records.map(({ source }) => <dl key={source.version_id}>
    <dt>Source</dt><dd>{source.provenance.source}</dd><dt>Attribution</dt><dd>{attribution(source.attribution)}</dd>
    <dt>Fact / version</dt><dd>{source.fact_id} / {source.version_id}</dd><dt>Author</dt><dd>{source.author_role} / {source.author_id}</dd>
    <dt>Entry method</dt><dd>{source.entry_method}</dd><dt>Reference (metadata only)</dt><dd>{source.provenance.reference ?? "None"}</dd>
  </dl>)}</details>;
}
function ObservationView({ title, point }: { title: string; point: Observation | null }) {
  return <section className="cc-card" style={{ padding: "0.75rem", marginTop: "0.75rem" }} aria-label={title}>
    <h3>{title}</h3>{!point ? <p>Not recorded.</p> : <>
      <p>{point.status === "AMBIGUOUS" ? "Conflicting values or incomparable units; no single value selected." : `${point.value} ${point.unit}`} — <time dateTime={point.observed_at}>{point.observed_at}</time></p>
      {point.status === "AMBIGUOUS" && point.records.map(r => <p key={r.source.version_id}>{r.text}</p>)}
      <Provenance records={point.records} />
    </>}
  </section>;
}

export default function ClinicalSummaryPanel({ token, patient, contextSignal, onDenied }: {
  token: string; patient: string; contextSignal: AbortSignal; onDenied: (status: number) => void;
}) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  const { begin, busy } = useDashboardTask();
  async function load() {
    const task = begin(); if (!task) return;
    const controller = new AbortController(), abort = () => controller.abort();
    task.signal.addEventListener("abort", abort); contextSignal.addEventListener("abort", abort);
    if (task.signal.aborted || contextSignal.aborted) abort();
    setSummary(null); setError("");
    try {
      const value = await request<unknown>(`/patients/${patient}/clinical-summary`, token, {
        operation_id: crypto.randomUUID(), request: { patient_id: patient, representation_version: "clinical-summary-json-v1" },
      }, controller.signal);
      const released = decode(value, patient);
      if (task.current() && !controller.signal.aborted) setSummary(released);
    } catch (cause) {
      if (!task.current() || controller.signal.aborted) return;
      if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) onDenied(cause.status);
      else setError("Summary withheld or unavailable. Empty records, changed state, or an uncertain response may require refresh. No automatic retry; request again explicitly.");
    } finally { task.signal.removeEventListener("abort", abort); contextSignal.removeEventListener("abort", abort); task.done(); }
  }
  return <section role="region" aria-label="Clinical Summary" style={{ padding: "1rem" }}>
    <h2>Clinical Summary</h2><p>Released recorded-state summary. Technical verification is not medical correctness or treatment advice.</p>
    <button className="cc-btn cc-btn-primary" disabled={busy} onClick={() => void load()}>{busy ? "Loading summary…" : "Request clinical summary"}</button>
    {error && <p role="alert" aria-label="Summary error">{error}</p>}
    {!summary && !busy && !error && <p>No summary displayed. Request a fresh authorized summary.</p>}
    {summary && <>
      <ObservationView title="Latest HbA1c" point={summary.latest} /><ObservationView title="Previous HbA1c" point={summary.previous} />
      <p>Recorded HbA1c direction: {directions[summary.direction]} ({summary.reason}). This is arithmetic only.</p>
      <details><summary>HbA1c history</summary>{summary.points.map(p => <ObservationView key={p.observed_at} title="Recorded HbA1c" point={p} />)}</details>
      <ObservationView title="Latest eGFR" point={summary.egfr} />
      <h3>Recorded medications</h3><p>These records do not confirm current use or adherence.</p>
      {!summary.medications.length && <p>No medication records in this released summary.</p>}
      {summary.medications.map(r => <article key={r.source.version_id}><p>{r.text}</p><Provenance records={[r]} /></article>)}
      <h3>Missing information</h3><p>{summary.missing.length ? summary.missing.map(key => missingLabels[key]).join(", ") : "None in the summarized categories."}</p>
    </>}
  </section>;
}
