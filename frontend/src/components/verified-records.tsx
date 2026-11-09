"use client";

import { useEffect, useRef, useState } from "react";
import { AcceptedFact, attribution } from "./clinical-types";

type ReleasedRecord = { claim_id: string; text: string };
type VerifiedRequest = {
  operation_id: string;
  request: {
    draft: {
      draft_id: string; patient_id: string;
      claims: {
        claim_id: string; patient_id: string; claim_type: "FACT_RESTATEMENT";
        fact_version_ids: string[];
        source_attribution: { author_id: string; author_role: AcceptedFact["author_role"]; category: AcceptedFact["attribution"] };
        proposed_content: { fact: AcceptedFact["fact"]; observed_at: string };
      }[];
    };
    bindings: [];
  };
};

function released(value: unknown, claim: string): ReleasedRecord[] {
  if (!value || typeof value !== "object") throw new Error();
  const body = value as Record<string, unknown>;
  if (Object.keys(body).sort().join() !== "records,representation_version" ||
      body.representation_version !== "record-response-json-v1" ||
      !Array.isArray(body.records) || body.records.length !== 1) throw new Error();
  const record = body.records[0];
  if (!record || typeof record !== "object" || Object.keys(record).sort().join() !== "claim_id,text" ||
      record.claim_id !== claim || typeof record.text !== "string" || !record.text.trim()) throw new Error();
  return [{ claim_id: record.claim_id, text: record.text }];
}

export default function VerifiedRecords({ token, patient, facts, onDenied }: {
  token: string; patient: string; facts: AcceptedFact[]; onDenied: (status: number) => void;
}) {
  const [selected, setSelected] = useState(facts[0]?.version_id ?? "");
  const [records, setRecords] = useState<ReleasedRecord[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(true);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; pending.current?.abort(); };
  }, []);

  async function verify() {
    if (pending.current) return;
    const fact = facts.find(f => f.version_id === selected);
    if (!fact || fact.patient_id !== patient || fact.status !== "CONFIRMED" || !fact.author_id ||
        !["patient", "practitioner"].includes(fact.author_role)) {
      setRecords([]); setError("Refresh server state before selecting an accepted record."); return;
    }
    const controller = new AbortController();
    pending.current = controller; setBusy(true); setRecords([]); setError("");
    const claim = crypto.randomUUID();
    // Original assertions only: the server independently checks every field.
    const body: VerifiedRequest = {
      operation_id: crypto.randomUUID(),
      request: {
        draft: { draft_id: crypto.randomUUID(), patient_id: patient, claims: [{
          claim_id: claim, patient_id: patient, claim_type: "FACT_RESTATEMENT",
          fact_version_ids: [fact.version_id],
          source_attribution: { author_id: fact.author_id, author_role: fact.author_role, category: fact.attribution },
          proposed_content: { fact: fact.fact, observed_at: fact.observed_at },
        }] },
        bindings: [],
      },
    };
    const timer = setTimeout(() => controller.abort(), 20000);
    const current = () => active.current && pending.current === controller;
    try {
      const response = await fetch(`/api/v1/patients/${patient}/verified-records`, {
        method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(body), signal: controller.signal,
        cache: "no-store", credentials: "omit", redirect: "error", referrerPolicy: "no-referrer",
      });
      if (!current()) return;
      if (!response.ok) {
        if ([401, 403, 404].includes(response.status)) { onDenied(response.status); return; }
        setError(response.status === 409 ? "Records withheld or state changed. Refresh server state before a new explicit request."
          : response.status === 422 ? "Request rejected. Refresh server state before a new explicit request."
          : "Response unavailable; the outcome may be uncertain. No automatic retry. Make a new explicit request when ready.");
        return;
      }
      const result = released(await response.json(), claim);
      if (current() && !controller.signal.aborted) setRecords(result);
    } catch {
      if (current()) setError("No complete valid response received; the outcome may be uncertain. No automatic retry. Make a new explicit request when ready.");
    } finally {
      clearTimeout(timer);
      if (current()) { pending.current = null; setBusy(false); }
    }
  }

  return <section className="panel" aria-label="Verified Records">
    <h2>Verified Records</h2>
    <p>Request a technically verified, server-released restatement of an accepted record. Technical verification is not medical correctness, diagnosis or treatment advice. No clinical evidence support is asserted.</p>
    <label>Accepted record to restate<select className="w-full min-w-0 rounded border border-slate-500 bg-white p-2 text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600" value={selected} disabled={busy || !facts.length} onChange={e => { setSelected(e.target.value); setRecords([]); setError(""); }}>
      {!facts.length && <option value="">No accepted records available</option>}
      {facts.map(f => <option key={f.version_id} value={f.version_id}>{f.fact.kind} — {attribution(f.attribution)} — {f.observed_at}</option>)}
    </select></label>
    <button onClick={verify} disabled={busy || !facts.length} aria-busy={busy}>{busy ? "Requesting verified record..." : "Request verified record"}</button>
    {error && <p role="alert" aria-label="Verified record error" className="error">{error}</p>}
    <div aria-live="polite">
      {!records.length && <p>No server-released verified restatement displayed. Pending and rejected proposals are not eligible.</p>}
      {records.map(record => <article className="fact-card" aria-label="Server-released record" key={record.claim_id}>
        <strong>Technically verified record restatement</strong><p className="source-text">{record.text}</p>
      </article>)}
    </div>
  </section>;
}
