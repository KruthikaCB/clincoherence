export type Fact =
  | { kind: "laboratory"; code: string; value: string; unit: string }
  | { kind: "diagnosis"; code: string; description?: string | null }
  | { kind: "medication"; medication_name: string; dose_value?: string | null; dose_unit?: string | null; frequency?: string | null }
  | { kind: "symptom"; code: string; description: string }
  | { kind: "history"; category: string; description: string };
export type Account = { id: string; role: "patient" | "practitioner" };
export type Proposal = {
  confirmation_id: string; patient_id: string; source_message_id: string;
  speaker_id: string; speaker_role: Account["role"];
  attribution: "patient_reported" | "practitioner_managed";
  proposed_fact: Fact; observed_at: string; source_quote: string;
  status: "PENDING" | "CONFIRMED" | "REJECTED";
};
export type AcceptedFact = {
  version_id: string; patient_id: string; fact: Fact; observed_at: string;
  attribution: Proposal["attribution"]; status: "CONFIRMED";
};
export function attribution(value: Proposal["attribution"]) {
  return value === "patient_reported" ? "Patient-reported" : "Practitioner-managed";
}
