"use client";

import { FormEvent, useState } from "react";
import { ApiError, exact, request, useDashboardTask, uuid } from "./patient-selector";

const messages: Record<string, string> = {
  QUESTION_UNSUPPORTED: "Question not supported or ambiguous. Use one of the exact examples below.",
  CLINICAL_DECISION_UNSUPPORTED: "Diagnosis, interpretation and treatment decisions are not supported. Ask only about recorded information.",
  NO_MATCHING_RECORDS: "No matching accepted record was selected at lookup. This does not establish absence of a condition or medication.",
  LATEST_AMBIGUOUS: "Multiple records share the latest observation time. Review Clinical History and clarify the record.",
  TOO_MANY_RECORDS: "Too many records for one verified response. Ask about a narrower category.",
};

function decode(value: unknown): { records: { claim_id: string; text: string }[]; notice: string } {
  if (!value || typeof value !== "object") throw new Error();
  if ((value as { representation_version?: unknown }).representation_version === "record-question-status-v1") {
    const body = exact(value, "reason_code,representation_version,status");
    if (typeof body.reason_code !== "string" || !Object.hasOwn(messages, body.reason_code) ||
        body.status !== (body.reason_code === "CLINICAL_DECISION_UNSUPPORTED" ? "REFUSE" : "CLARIFY")) throw new Error();
    return { records: [], notice: messages[body.reason_code] };
  }
  const body = exact(value, "records,representation_version");
  if (body.representation_version !== "record-response-json-v1" || !Array.isArray(body.records) ||
      body.records.length < 1 || body.records.length > 32) throw new Error();
  const records = body.records.map(item => {
    const row = exact(item, "claim_id,text");
    if (typeof row.claim_id !== "string" || !uuid.test(row.claim_id) || typeof row.text !== "string" || !row.text.trim()) throw new Error();
    return { claim_id: row.claim_id, text: row.text };
  });
  if (new Set(records.map(r => r.claim_id)).size !== records.length) throw new Error();
  return { records, notice: "" };
}

export default function RecordQuestions({ token, patient, onDenied }: {
  token: string; patient: string; onDenied: (status: number) => void;
}) {
  const [question, setQuestion] = useState("");
  const [records, setRecords] = useState<{ claim_id: string; text: string }[]>([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const { begin, busy } = useDashboardTask();
  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!question.trim()) return;
    const task = begin(); if (!task) return;
    setRecords([]); setNotice(""); setError("");
    try {
      const result = decode(await request<unknown>(`/patients/${patient}/record-questions`, token,
        { question: question.trim(), operation_id: crypto.randomUUID() }, task.signal));
      if (task.current() && !task.signal.aborted) { setRecords(result.records); setNotice(result.notice); }
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
      <label>Record question<input value={question} onChange={e => { setQuestion(e.target.value); setRecords([]); setNotice(""); setError(""); }} required maxLength={500} disabled={busy} /></label>
      <button disabled={busy || !question.trim()}>{busy ? "Checking recorded information..." : "Ask records"}</button>
    </form>
    {error && <p role="alert" aria-label="Record question error" className="error">{error}</p>}
    <div aria-live="polite">{notice && <p role="status">{notice}</p>}
      {records.map(record => <article className="fact-card" aria-label="Released question record" key={record.claim_id}>
        <strong>Technically verified record restatement</strong><p className="source-text">{record.text}</p>
      </article>)}
    </div>
  </section>;
}
