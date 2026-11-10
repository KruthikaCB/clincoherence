"use client";

import { useEffect, useRef, useState } from "react";
import { AcceptedFact, attribution } from "./clinical-types";
import { FactSummary } from "./fact-card";

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

function formatDate(iso: string): string {
  try { return new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }); }
  catch { return iso; }
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
        setError(response.status === 409
          ? "Records withheld or state changed. Refresh server state before retrying."
          : response.status === 422
          ? "Request rejected. Refresh server state before retrying."
          : "Response unavailable. No automatic retry — make a new explicit request when ready.");
        return;
      }
      const result = released(await response.json(), claim);
      if (current() && !controller.signal.aborted) setRecords(result);
    } catch {
      if (current()) setError("No complete valid response received. No automatic retry — make a new explicit request when ready.");
    } finally {
      clearTimeout(timer);
      if (current()) { pending.current = null; setBusy(false); }
    }
  }

  return (
    <div style={{ padding: "1rem" }} aria-label="Verified Records">
      <div style={{ marginBottom: "1rem" }}>
        <div style={{ fontSize: "0.75rem", color: "#6b7280", lineHeight: 1.5 }}>
          Request a technically verified, server-released restatement of an accepted record.
          Technical verification is not medical correctness, diagnosis or treatment advice.
        </div>
      </div>

      {!facts.length ? (
        <div style={{ padding: "2rem", textAlign: "center", color: "#9ca3af", fontSize: "0.82rem" }}>
          No accepted records available. Accept some extracted facts first.
        </div>
      ) : (
        <>
          <label className="cc-label" style={{ marginBottom: "0.75rem" }}>
            Select accepted record
            <select
              className="cc-select"
              value={selected}
              disabled={busy || !facts.length}
              onChange={e => { setSelected(e.target.value); setRecords([]); setError(""); }}
            >
              {facts.map(f => (
                <option key={f.version_id} value={f.version_id}>
                  {f.fact.kind} — {attribution(f.attribution)} — {formatDate(f.observed_at)}
                </option>
              ))}
            </select>
          </label>

          {/* Preview of selected fact */}
          {selected && (() => {
            const f = facts.find(x => x.version_id === selected);
            return f ? (
              <div style={{ padding: "0.6rem 0.75rem", background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: "var(--radius)", marginBottom: "0.75rem", fontSize: "0.82rem" }}>
                <span style={{ color: "#6b7280", fontSize: "0.72rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em" }}>Selected: </span>
                <FactSummary fact={f.fact} />
              </div>
            ) : null;
          })()}

          <button
            className="cc-btn cc-btn-primary"
            onClick={verify}
            disabled={busy || !facts.length}
            aria-busy={busy}
          >
            {busy ? <><span className="cc-spinner" /> Requesting…</> : "Request Verified Record"}
          </button>

          {error && <div className="cc-banner error" style={{ marginTop: "0.75rem" }} role="alert">{error}</div>}

          <div aria-live="polite" style={{ marginTop: "0.75rem" }}>
            {!records.length && !error && !busy && (
              <div style={{ fontSize: "0.78rem", color: "#9ca3af", padding: "1rem 0" }}>
                No server-released restatement displayed yet. Pending and rejected proposals are not eligible.
              </div>
            )}
            {records.map(record => (
              <div key={record.claim_id} className="cc-answer-card">
                <strong>Technically verified record restatement</strong>
                {record.text}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
