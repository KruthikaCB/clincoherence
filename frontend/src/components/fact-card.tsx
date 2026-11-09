import { attribution, Fact, Proposal } from "./clinical-types";

export function FactDetails({ fact }: { fact: Fact }) {
  return <dl className="fact-details">
    <div><dt>Type</dt><dd>{fact.kind}</dd></div>
    {"code" in fact && <div><dt>Fact</dt><dd>{fact.code}</dd></div>}
    {"category" in fact && <div><dt>Fact</dt><dd>{fact.category}</dd></div>}
    {fact.kind === "laboratory" && <><div><dt>Value</dt><dd>{fact.value}</dd></div><div><dt>Unit</dt><dd>{fact.unit}</dd></div></>}
    {fact.kind === "medication" && <><div><dt>Medication</dt><dd>{fact.medication_name}</dd></div>
      {fact.dose_value != null && <><div><dt>Value</dt><dd>{fact.dose_value}</dd></div><div><dt>Unit</dt><dd>{fact.dose_unit}</dd></div></>}
      {fact.frequency && <div><dt>Frequency</dt><dd>{fact.frequency}</dd></div>}</>}
    {"description" in fact && fact.description && <div><dt>Description</dt><dd>{fact.description}</dd></div>}
  </dl>;
}

export default function FactCard({ proposal, busy, onDecision }: {
  proposal: Proposal; busy: boolean; onDecision: (id: string, action: "confirm" | "reject") => void;
}) {
  return <article className="fact-card" aria-label="Fact confirmation" aria-busy={busy}>
    <div className="card-heading"><strong>{attribution(proposal.attribution)}</strong><span>{proposal.status}</span></div>
    <FactDetails fact={proposal.proposed_fact} />
    <p>Observed: <time dateTime={proposal.observed_at}>{proposal.observed_at}</time></p>
    <p>Exact source quote</p><blockquote>{proposal.source_quote}</blockquote>
    {proposal.status === "PENDING" && <div className="actions">
      <button disabled={busy} onClick={() => onDecision(proposal.confirmation_id, "confirm")}>Confirm</button>
      <button className="secondary" disabled={busy} onClick={() => onDecision(proposal.confirmation_id, "reject")}>Reject</button>
      {busy && <span role="status">Request in progress...</span>}
    </div>}
  </article>;
}
