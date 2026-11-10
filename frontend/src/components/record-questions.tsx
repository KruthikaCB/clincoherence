"use client";

import { FormEvent, useState } from "react";
import { ApiError, exact, request, useDashboardTask, uuid } from "./patient-selector";
import { SyntheticProvenance, syntheticProvenance } from "./synthetic-provenance";

const messages: Record<string, string> = {
  QUESTION_UNSUPPORTED: "Question not supported or ambiguous. Use one of the exact examples below.",
  CLINICAL_DECISION_UNSUPPORTED: "Diagnosis, interpretation and treatment decisions are not supported. Ask only about recorded information.",
  NO_MATCHING_RECORDS: "No matching accepted record was selected at lookup. This does not establish absence of a condition or medication.",
  LATEST_AMBIGUOUS: "Multiple records share the latest observation time. Review Clinical History and clarify the record.",
  TOO_MANY_RECORDS: "Too many records for one verified response. Ask about a narrower category.",
  SYNTHETIC_CONTEXT_UNSUPPORTED: "Synthetic provenance is supported only for a unique latest recorded HbA1c in %. No clinical guidance is available.",
};

function decode(value: unknown, evidence: boolean): { records: { claim_id: string; text: string }[]; notice: string; provenance: SyntheticProvenance[] } {
  if (!value || typeof value !== "object") throw new Error();
  if ((value as { representation_version?: unknown }).representation_version === "record-question-status-v1") {
    const body = exact(value, "reason_code,representation_version,status");
    if (typeof body.reason_code !== "string" || !Object.hasOwn(messages, body.reason_code) ||
        body.status !== (body.reason_code === "CLINICAL_DECISION_UNSUPPORTED" ? "REFUSE" : "CLARIFY")) throw new Error();
    return { records: [], notice: messages[body.reason_code], provenance: [] };
  }
  const body = exact(value, evidence ? "provenance,records,representation_version" : "records,representation_version");
  if (body.representation_version !== (evidence ? "record-evidence-json-v2" : "record-response-json-v1") || !Array.isArray(body.records) ||
      body.records.length < 1 || body.records.length > 32) throw new Error();
  const records = body.records.map(item => {
    const row = exact(item, "claim_id,text");
    if (typeof row.claim_id !== "string" || !uuid.test(row.claim_id) || typeof row.text !== "string" || !row.text.trim()) throw new Error();
    return { claim_id: row.claim_id, text: row.text };
  });
  if (new Set(records.map(r => r.claim_id)).size !== records.length) throw new Error();
  return { records, notice: "", provenance: evidence ? syntheticProvenance(body.provenance, records.map(r => r.claim_id)) : [] };
}

export default function RecordQuestions({ token, patient, onDenied }: {
  token: string; patient: string; onDenied: (status: number) => void;
}) {
  const [question, setQuestion] = useState("");
  const [records, setRecords] = useState<{ claim_id: string; text: string }[]>([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [evidence, setEvidence] = useState(false);
  const [provenance, setProvenance] = useState<SyntheticProvenance[]>([]);
  function clear() { setRecords([]); setProvenance([]); setNotice(""); setError(""); }
  const { begin, busy } = useDashboardTask();
  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!question.trim()) return;
    const task = begin(); if (!task) return;
    clear();
    try {
      const result = decode(await request<unknown>(`/patients/${patient}/record-questions`, token,
        { question: question.trim(), operation_id: crypto.randomUUID(), ...(evidence ? { include_synthetic_provenance: true } : {}) }, task.signal), evidence);
      if (task.current() && !task.signal.aborted) { setRecords(result.records); setNotice(result.notice); setProvenance(result.provenance); }
    } catch (cause) {
      if (!task.current()) return;
      if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) onDenied(cause.status);
      else setError("No complete verified response available; state may have changed or the outcome is uncertain. No automatic retry. Refresh state and make a new explicit request.");
    } finally { task.done(); }
  }
  return <section className="panel" aria-label="Record questions">
    <h2>Ask about accepted T2DM records</h2>
    <p>Deterministic supported questions only, not general medical understanding. Answers are server-released fact restatements, not medical correctness, diagnosis, clinical safety or treatment advice.</p>
    <p>Selection uses accepted versions at authorized lookup. “Latest” uses observation time; newer entries may arrive afterward. Recorded medications do not establish what is currently being taken. Pending/rejected proposals and private transcripts are never question context.</p>
    <details><summary>Supported question examples</summary><ul>
      <li>What confirmed clinical information is recorded?</li>
      <li>What is my latest recorded value for hba1c?</li>
      <li>What medications are currently recorded?</li>
      <li>What symptoms or relevant history have been recorded?</li>
    </ul><p>Supported laboratory names: hba1c, glucose, creatinine, egfr, urine albumin creatinine ratio.</p></details>
    <form onSubmit={ask} aria-busy={busy}>
      <label>Record question<input value={question} onChange={e => { setQuestion(e.target.value); clear(); }} required maxLength={500} disabled={busy} /></label>
      <label><input type="checkbox" checked={evidence} disabled={busy} onChange={e => { setEvidence(e.target.checked); clear(); }} />Include synthetic demonstration provenance</label>
      <p>Synthetic provenance is available only for latest HbA1c (%) questions. It tests reference integrity and structured applicability, not clinical support or real guideline evidence.</p>
      <button disabled={busy || !question.trim()}>{busy ? "Checking recorded information..." : "Ask records"}</button>
    </form>
    {error && <p role="alert" aria-label="Record question error" className="error">{error}</p>}
    <div aria-live="polite">{notice && <p role="status">{notice}</p>}
      {records.map(record => <article className="fact-card" aria-label="Released question record" key={record.claim_id}>
        <strong>Technically verified record restatement</strong><p className="source-text">{record.text}</p>
      </article>)}
      {provenance.map(p => <section className="fact-card break-all" aria-label="Released synthetic provenance" key={p.claim_id}>
        <strong>SYNTHETIC DEMONSTRATION DATA — NOT CLINICAL GUIDANCE</strong>
        <p>Reference integrity: {p.integrity_status}. Structured applicability: {p.applicability_status}. Neither establishes clinical correctness or clinical support.</p>
        <dl><dt>Claim</dt><dd>{p.claim_id}</dd><dt>Accepted version</dt><dd>{p.fact_version_ids[0]}</dd>
          <dt>Source</dt><dd>{p.source.title}</dd><dt>Publisher</dt><dd>{p.source.publisher}</dd>
          <dt>Source ID / version / edition</dt><dd>{p.evidence.source_id} / {p.evidence.source_version} / {p.evidence.source_edition}</dd>
          <dt>Source hash</dt><dd>{p.evidence.source_content_hash}</dd><dt>Location (metadata only)</dt><dd>{p.source.canonical_location} — {p.locator}</dd>
          <dt>Evidence unit / version / hash</dt><dd>{p.evidence.unit_id} / {p.evidence.unit_version} / {p.evidence.unit_content_hash}</dd>
          <dt>Rule / version / hash</dt><dd>{p.rule_id} / {p.rule_version} / {p.rule_content_hash}</dd>
          <dt>Approval manifest / version / hash</dt><dd>{p.approval_manifest_id} / {p.approval_manifest_version} / {p.approval_manifest_hash}</dd>
          <dt>Intended use</dt><dd>{p.intended_use}</dd></dl>
      </section>)}
    </div>
  </section>;
}
