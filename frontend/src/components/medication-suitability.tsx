"use client";

import { useEffect, useState } from "react";
import { acceptedFact, AcceptedFact } from "./clinical-types";
import { FactSummary } from "./fact-card";
import { ApiError, request, useDashboardTask, uuid } from "./patient-selector";
import styles from "./medication-suitability.module.css";

const medications = ["metformin", "empagliflozin", "semaglutide"] as const;
type Medication = typeof medications[number];
const names: Record<Medication, string> = { metformin: "Metformin", empagliflozin: "Empagliflozin · SGLT2 inhibitor", semaglutide: "Semaglutide · Ozempic label" };
const statuses = {
  INSUFFICIENT_INFORMATION: "INSUFFICIENT INFORMATION",
  CAUTION_REVIEW_REQUIRED: "CAUTION / REVIEW REQUIRED",
  NOT_SUITABLE_UNDER_DOCUMENTED_RULE: "NOT SUITABLE UNDER DOCUMENTED RULE",
};
type Status = keyof typeof statuses;
type Source = { source_id: string; title: string; organization: string; document_version: string; published_on: string; url: string; stable_id: string; sections: string[] };
type Rule = { rule_id: string; medication: Medication; source_id: string; condition: string; description: string; scope: string };
type Check = { rule: Rule; outcome: "TRIGGERED" | "NOT_TRIGGERED" | "UNKNOWN"; fact_version_ids: string[] };
type Card = { medication: Medication; status: Status; reasoning: string[]; cautions: string[]; missing_information: string[]; fact_version_ids: string[]; rule_checks: Check[]; evidence: Source[]; clinician_review_required: true };
type Assessment = { patient_id: string; assessed_at: string; facts: AcceptedFact[]; assessments: Card[]; limitations: string[]; catalog_version: string | null; catalog_sha256: string | null; evidence_review_status: string };

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Unavailable");
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  if (typeof value !== "string" || !value.trim() || value.length > 3000) throw new Error("Unavailable");
  return value;
}
function strings(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > 128) throw new Error("Unavailable");
  return value.map(text);
}

/** Reject wrong-patient, malformed or positive-clearance responses before rendering. */
export function decodeSuitability(value: unknown, patient: string): Assessment {
  const row = object(value);
  if (row.patient_id !== patient || row.representation_version !== "synthetic-medication-assessment-v1" ||
    row.mode !== "SYNTHETIC_EDUCATIONAL_ONLY" || row.confirmation_scope !== "SPEAKER_CONFIRMED_NOT_CLINICIAN_APPROVED" ||
    !Array.isArray(row.facts) || row.facts.length > 128 || !Array.isArray(row.assessments) || row.assessments.length !== 3) throw new Error("Unavailable");
  const facts = row.facts.map(value => {
    const raw = { ...object(value) };
    if (!(raw.previous_version === null || Number.isInteger(raw.previous_version))) throw new Error("Unavailable");
    delete raw.previous_version;
    const fact = acceptedFact(raw, patient);
    if (fact.provenance.data_origin !== "synthetic") throw new Error("Unavailable");
    return fact;
  });
  const ids = new Set(facts.map(f => f.version_id));
  if (ids.size !== facts.length) throw new Error("Unavailable");
  const refs = (value: unknown) => strings(value).map(id => {
    if (!uuid.test(id) || !ids.has(id)) throw new Error("Unavailable");
    return id;
  });
  const assessments = row.assessments.map((value, index) => {
    const card = object(value);
    if (card.medication !== medications[index] || !Object.hasOwn(statuses, String(card.status)) ||
      card.clinician_review_required !== true || !Array.isArray(card.evidence) || !Array.isArray(card.rule_checks)) throw new Error("Unavailable");
    const evidence = card.evidence.map(value => {
      const source = object(value), url = new URL(text(source.url));
      if (url.protocol !== "https:" || url.hostname !== "dailymed.nlm.nih.gov" || url.username || url.password ||
        url.pathname !== "/dailymed/drugInfo.cfm" || !uuid.test(url.searchParams.get("setid") ?? "")) throw new Error("Unavailable");
      return { source_id: text(source.source_id), title: text(source.title), organization: text(source.organization),
        document_version: text(source.document_version), published_on: text(source.published_on), url: url.href,
        stable_id: text(source.stable_id), sections: strings(source.sections) };
    });
    const checks = card.rule_checks.map(value => {
      const check = object(value), rule = object(check.rule);
      if (!["TRIGGERED", "NOT_TRIGGERED", "UNKNOWN"].includes(String(check.outcome)) || rule.medication !== card.medication ||
        !evidence.some(source => source.source_id === rule.source_id)) throw new Error("Unavailable");
      return { outcome: check.outcome as Check["outcome"], fact_version_ids: refs(check.fact_version_ids), rule: {
        rule_id: text(rule.rule_id), medication: card.medication as Medication, source_id: text(rule.source_id),
        condition: text(rule.condition), description: text(rule.description), scope: text(rule.scope),
      } };
    });
    return { medication: card.medication as Medication, status: card.status as Status, clinician_review_required: true as const,
      reasoning: strings(card.reasoning), cautions: strings(card.cautions), missing_information: strings(card.missing_information),
      fact_version_ids: refs(card.fact_version_ids), evidence, rule_checks: checks };
  });
  if (!["SOURCE_VERIFIED_CLINICAL_REVIEW_PENDING", "UNAVAILABLE"].includes(String(row.evidence_review_status))) throw new Error("Unavailable");
  if (row.evidence_review_status === "UNAVAILABLE") {
    if (row.catalog_version !== null || row.catalog_sha256 !== null || assessments.some(c => c.status !== "INSUFFICIENT_INFORMATION" || c.evidence.length || c.rule_checks.length)) throw new Error("Unavailable");
  } else if (row.catalog_version !== "capstone-label-checks-2026-11-12-v1" || typeof row.catalog_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(row.catalog_sha256) || assessments.some(c => !c.evidence.length || !c.rule_checks.length)) throw new Error("Unavailable");
  const assessed_at = text(row.assessed_at);
  if (!Number.isFinite(Date.parse(assessed_at))) throw new Error("Unavailable");
  return { patient_id: patient, assessed_at, facts, assessments, limitations: strings(row.limitations),
    catalog_version: row.catalog_version as string | null, catalog_sha256: row.catalog_sha256 as string | null,
    evidence_review_status: String(row.evidence_review_status) };
}

export default function MedicationSuitabilityPanel({ token, patient, patientLabel, contextSignal, onDenied }: {
  token: string; patient: string; patientLabel: string; contextSignal: AbortSignal; onDenied: (status: number) => void;
}) {
  const [result, setResult] = useState<Assessment | null>(null);
  const [error, setError] = useState("");
  const { begin, busy } = useDashboardTask();
  useEffect(() => {
    setResult(null); setError("");
    const clear = () => { setResult(null); setError(""); };
    contextSignal.addEventListener("abort", clear);
    return () => contextSignal.removeEventListener("abort", clear);
  }, [patient, token, contextSignal]);
  async function load() {
    const task = begin(); if (!task) return;
    const controller = new AbortController(), abort = () => controller.abort();
    task.signal.addEventListener("abort", abort); contextSignal.addEventListener("abort", abort);
    if (task.signal.aborted || contextSignal.aborted) abort();
    setResult(null); setError("");
    try {
      const raw = await request<unknown>(`/patients/${patient}/medication-suitability`, token, undefined, controller.signal);
      const response = decodeSuitability(raw, patient);
      if (task.current() && !controller.signal.aborted) setResult(response);
    } catch (cause) {
      if (!task.current() || controller.signal.aborted) return;
      if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) onDenied(cause.status);
      else setError("Assessment unavailable. Select an authorized synthetic demo record and refresh its confirmed facts. No result was retained; request again explicitly.");
    } finally { task.signal.removeEventListener("abort", abort); contextSignal.removeEventListener("abort", abort); task.done(); }
  }
  return <section className={styles.panel} aria-label="Medication Suitability">
    <header className={styles.header}><div><p className={styles.eyebrow}>EDUCATIONAL LABEL CHECKS</p><h2>Medication Suitability</h2>
      <p><strong>{patientLabel}</strong><br /><span className={styles.identifier}>Patient {patient}</span></p></div>
      <button className="cc-btn cc-btn-primary" disabled={busy || contextSignal.aborted} onClick={() => void load()}>{busy ? "Assessing records…" : "Assess synthetic scenario"}</button>
    </header>
    <div className={styles.notice}><strong>Synthetic educational scenario — not a live clinical recommendation.</strong>
      <p>Confirmed records are not separately clinician-approved. These limited checks never prescribe, calculate doses or establish that a medication is safe. Clinician review is always required.</p></div>
    {error && <p className="cc-banner error" role="alert">{error}</p>}
    {!result && !error && !busy && <p className={styles.empty}>Request an assessment of this patient&apos;s confirmed synthetic records. Missing information remains UNKNOWN.</p>}
    {result && <>
      <p className={styles.meta}>Evidence: {result.evidence_review_status === "UNAVAILABLE" ? "Unavailable — all rules withheld" : "Source-verified; clinical review pending"} · Assessed {result.assessed_at}</p>
      <div className={styles.cards}>{result.assessments.map(card => <article key={card.medication} className={styles.card} aria-label={`${card.medication} assessment`}>
        <h3>{names[card.medication]}</h3>
        <p className={`${styles.badge} ${card.status === "NOT_SUITABLE_UNDER_DOCUMENTED_RULE" ? styles.negative : card.status === "CAUTION_REVIEW_REQUIRED" ? styles.caution : styles.unknown}`}>{statuses[card.status]}</p>
        <p className={styles.scope}>Recorded synthetic scenario only · clinician review required</p>
        {card.reasoning.map((reason, i) => <p key={i}>{reason}</p>)}
        <h4>Missing / not established</h4><ul>{card.missing_information.map((missing, i) => <li key={i}>{missing}</li>)}</ul>
        <h4>Safety cautions</h4><ul>{card.cautions.map((caution, i) => <li key={i}>{caution}</li>)}</ul>
        <details><summary>Patient facts and exact versions</summary>
          {!card.fact_version_ids.length && <p>No confirmed records available.</p>}
          {card.fact_version_ids.map(id => { const fact = result.facts.find(f => f.version_id === id)!; return <div className={styles.fact} key={id}>
            <FactSummary fact={fact.fact} /><p>Observed {fact.observed_at} · {fact.attribution}</p>
            <p>Source: {fact.provenance.source} · {fact.provenance.data_origin}</p>
            <p className={styles.identifier}>Fact {fact.fact_id}<br />Version {id} · author {fact.author_id}</p>
          </div>; })}
        </details>
        <details><summary>Rules and evidence references</summary>
          {!card.rule_checks.length && <p>No rule evaluated: evidence unavailable.</p>}
          {card.rule_checks.map(check => <div className={styles.fact} key={check.rule.rule_id}><strong>{check.rule.rule_id} · {check.outcome}</strong>
            <p>{check.rule.description}</p><p>{check.rule.scope}</p><p className={styles.identifier}>Bound fact versions: {check.fact_version_ids.join(", ") || "UNKNOWN"}</p></div>)}
          {card.evidence.map(source => <div className={styles.fact} key={source.source_id}>
            <a href={source.url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{source.title}</a>
            <p>{source.organization}<br />{source.document_version} · published {source.published_on}<br />Sections {source.sections.join(", ")}</p>
            <p className={styles.identifier}>{source.stable_id}</p><p>Source verified; clinical review pending.</p>
          </div>)}
        </details>
      </article>)}</div>
      <details className={styles.footer}><summary>Assessment boundaries and catalog provenance</summary>
        <ul>{result.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul>
        <p>{result.catalog_version ?? "Catalog unavailable"}</p><p className={styles.identifier}>SHA-256: {result.catalog_sha256 ?? "Unavailable"}</p>
      </details>
    </>}
  </section>;
}
