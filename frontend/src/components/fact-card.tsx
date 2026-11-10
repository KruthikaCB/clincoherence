import { attribution, Fact, Proposal } from "./clinical-types";

/** Compact single-line summary used in record cards and sidebar items. */
export function FactSummary({ fact, compact = false }: { fact: Fact; compact?: boolean }) {
  switch (fact.kind) {
    case "laboratory":
      return compact
        ? <span>{fact.code} {fact.value} {fact.unit}</span>
        : <span>{fact.code}: <strong>{fact.value}</strong> {fact.unit}</span>;
    case "diagnosis":
      return <span>{fact.description || fact.code}</span>;
    case "medication":
      return compact
        ? <span>{fact.medication_name}{fact.dose_value ? ` ${fact.dose_value}${fact.dose_unit}` : ""}</span>
        : <span>{fact.medication_name}{fact.dose_value ? ` — ${fact.dose_value} ${fact.dose_unit}` : ""}{fact.frequency ? `, ${fact.frequency}` : ""}</span>;
    case "symptom":
      return <span>{fact.description}</span>;
    case "history":
      return <span>[{fact.category}] {fact.description}</span>;
    default:
      return <span>Unknown fact type</span>;
  }
}

/** Full detail list used in Clinical History and correction forms. */
export function FactDetails({ fact }: { fact: Fact }) {
  return (
    <dl className="fact-details" style={{ fontSize: "0.82rem" }}>
      <div className="cc-fact-row"><span className="cc-fact-label">Type</span><span className="cc-fact-val">{fact.kind}</span></div>
      {"code" in fact && <div className="cc-fact-row"><span className="cc-fact-label">Code</span><span className="cc-fact-val">{fact.code}</span></div>}
      {"category" in fact && <div className="cc-fact-row"><span className="cc-fact-label">Category</span><span className="cc-fact-val">{fact.category}</span></div>}
      {fact.kind === "laboratory" && (
        <>
          <div className="cc-fact-row"><span className="cc-fact-label">Value</span><span className="cc-fact-val"><strong>{fact.value}</strong></span></div>
          <div className="cc-fact-row"><span className="cc-fact-label">Unit</span><span className="cc-fact-val">{fact.unit}</span></div>
        </>
      )}
      {fact.kind === "medication" && (
        <>
          <div className="cc-fact-row"><span className="cc-fact-label">Name</span><span className="cc-fact-val">{fact.medication_name}</span></div>
          {fact.dose_value != null && (
            <>
              <div className="cc-fact-row"><span className="cc-fact-label">Dose</span><span className="cc-fact-val">{fact.dose_value} {fact.dose_unit}</span></div>
            </>
          )}
          {fact.frequency && <div className="cc-fact-row"><span className="cc-fact-label">Frequency</span><span className="cc-fact-val">{fact.frequency}</span></div>}
        </>
      )}
      {"description" in fact && fact.description && (
        <div className="cc-fact-row"><span className="cc-fact-label">Description</span><span className="cc-fact-val">{fact.description}</span></div>
      )}
    </dl>
  );
}

/** Legacy full card — kept for backward compatibility with clinical-history.tsx */
export default function FactCard({ proposal, busy, onDecision }: {
  proposal: Proposal; busy: boolean; onDecision: (id: string, action: "confirm" | "reject") => void;
}) {
  return (
    <article className="cc-proposal" aria-label="Fact confirmation" aria-busy={busy}>
      <div className="cc-proposal-header">
        <span className="cc-proposal-kind">{proposal.proposed_fact.kind}</span>
        <span className={`cc-proposal-status ${proposal.status}`}>{proposal.status}</span>
        <span style={{ fontSize: "0.72rem", color: "#6b7280", marginLeft: "auto" }}>{attribution(proposal.attribution)}</span>
      </div>
      <div className="cc-proposal-body">
        <FactDetails fact={proposal.proposed_fact} />
        <div className="cc-fact-row">
          <span className="cc-fact-label">Observed</span>
          <span className="cc-fact-val"><time dateTime={proposal.observed_at}>{proposal.observed_at}</time></span>
        </div>
        {proposal.source_quote && (
          <blockquote className="cc-fact-quote">&quot;{proposal.source_quote}&quot;</blockquote>
        )}
      </div>
      {proposal.status === "PENDING" && (
        <div className="cc-proposal-actions">
          <button className="cc-accept-btn" disabled={busy} onClick={() => onDecision(proposal.confirmation_id, "confirm")}>✓ Accept</button>
          <button className="cc-reject-btn" disabled={busy} onClick={() => onDecision(proposal.confirmation_id, "reject")}>✕ Reject</button>
          {busy && <span role="status" style={{ fontSize: "0.72rem", color: "#6b7280" }}>Working…</span>}
        </div>
      )}
    </article>
  );
}
