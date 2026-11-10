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
  author_id: string; author_role: Account["role"];
  attribution: Proposal["attribution"]; status: "CONFIRMED";
  fact_id: string; version: number; previous_version_id: string | null;
  entered_at: string; correction_reason: string | null;
  provenance: { source: string; reference: string | null; data_origin: "synthetic" | "de_identified" };
  entry_method: "STRUCTURED" | "LLM_EXTRACTED";
  confirmed_by_id: string | null; confirmed_at: string | null;
};
export type CorrectionRequest = {
  fact: Fact; observed_at: string; provenance: AcceptedFact["provenance"];
  expected_version_id: string; correction_reason: string;
};
export function attribution(value: Proposal["attribution"]) {
  return value === "patient_reported" ? "Patient-reported" : "Practitioner-managed";
}

const identifier = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function invalid(): never { throw new Error("Clinical record unavailable"); }
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}
function shape(row: Record<string, unknown>, required: string[], optional: string[] = []) {
  if (required.some(key => !(key in row)) || Object.keys(row).some(key => !required.includes(key) && !optional.includes(key))) invalid();
}
function text(value: unknown, maximum = 2000): value is string {
  return typeof value === "string" && value.trim().length > 0 && Array.from(value).length <= maximum;
}
function id(value: unknown): value is string { return typeof value === "string" && identifier.test(value); }
function timestamp(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match || !Number.isFinite(Date.parse(value))) return false;
  const [year, month, day, hour, minute, second] = match.slice(1).map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return year > 0 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1] && hour < 24 && minute < 60 && second < 60;
}

/** Lexical decimal validation only. Measurement values are never converted to JS numbers. */
function decimal(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 200) return false;
  const parts = /^[+-]?(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(value);
  if (!parts) return false;
  let digits = (parts[1] + (parts[2] ?? "")).replace(/^0+/, "");
  if (!digits) return true;
  if ((parts[3] ?? "").length > 5) return false;
  // Only the bounded exponent is integer metadata; the coefficient remains a string.
  let scale = (parts[2]?.length ?? 0) - Number(parts[3] ?? "0");
  while (digits.endsWith("0")) { digits = digits.slice(0, -1); scale--; }
  const fractional = Math.max(scale, 0), whole = Math.max(digits.length - scale, 0);
  return fractional <= 10 && whole <= 14 && whole + fractional <= 24;
}

function payload(value: unknown): Fact {
  const row = object(value);
  const nullableText = (v: unknown, max: number) => v == null || text(v, max);
  switch (row.kind) {
    case "laboratory": {
      shape(row, ["kind", "code", "value", "unit"]);
      const units: Record<string, string[]> = { hba1c: ["%", "mmol/mol"], glucose: ["mg/dL", "mmol/L"], creatinine: ["mg/dL", "umol/L"], egfr: ["mL/min/1.73m2"], urine_albumin_creatinine_ratio: ["mg/g", "mg/mmol"] };
      if (typeof row.code !== "string" || !Object.hasOwn(units, row.code) || typeof row.unit !== "string" || !units[row.code].includes(row.unit) || !decimal(row.value)) invalid();
      break;
    }
    case "diagnosis":
      shape(row, ["kind", "code"], ["description"]);
      if (row.code !== "type_2_diabetes" || !nullableText(row.description, 2000)) invalid();
      break;
    case "medication":
      shape(row, ["kind", "medication_name"], ["dose_value", "dose_unit", "frequency"]);
      if (!text(row.medication_name, 200) || !nullableText(row.frequency, 200) ||
          ((row.dose_value == null) !== (row.dose_unit == null)) ||
          (row.dose_value != null && (!decimal(row.dose_value) || !text(row.dose_unit, 200)))) invalid();
      break;
    case "symptom":
      shape(row, ["kind", "code", "description"]);
      if (typeof row.code !== "string" || !["polyuria", "polydipsia", "fatigue", "other"].includes(row.code) || !text(row.description)) invalid();
      break;
    case "history":
      shape(row, ["kind", "category", "description"]);
      if (typeof row.category !== "string" || !["family_history", "allergy", "smoking", "prior_event", "other"].includes(row.category) || !text(row.description)) invalid();
      break;
    default: invalid();
  }
  return row as Fact;
}

export function acceptedFact(value: unknown, patient: string): AcceptedFact {
  const row = object(value);
  shape(row, ["fact_id", "version_id", "version", "previous_version_id", "patient_id", "attribution", "author_id", "author_role", "fact", "observed_at", "entered_at", "provenance", "entry_method", "status", "correction_reason", "confirmed_by_id", "confirmed_at"]);
  if (!id(patient) || row.patient_id !== patient || !id(row.fact_id) || !id(row.version_id) || !id(row.author_id) || row.status !== "CONFIRMED" || !Number.isSafeInteger(row.version) || (row.version as number) < 1 || !timestamp(row.observed_at) || !timestamp(row.entered_at)) invalid();
  if (!((row.author_role === "patient" && row.attribution === "patient_reported") || (row.author_role === "practitioner" && row.attribution === "practitioner_managed"))) invalid();
  if (row.version === 1 ? (row.fact_id !== row.version_id || row.previous_version_id !== null || row.correction_reason !== null)
    : (!id(row.previous_version_id) || row.previous_version_id === row.version_id || !text(row.correction_reason))) invalid();
  const provenance = object(row.provenance);
  shape(provenance, ["source", "reference", "data_origin"]);
  if (!text(provenance.source, 200) || !(provenance.reference === null || text(provenance.reference)) || typeof provenance.data_origin !== "string" || !["synthetic", "de_identified"].includes(provenance.data_origin)) invalid();
  if (row.entry_method === "STRUCTURED") {
    if (row.confirmed_by_id !== null || row.confirmed_at !== null) invalid();
  } else if (row.entry_method === "LLM_EXTRACTED") {
    if (row.confirmed_by_id !== row.author_id || !timestamp(row.confirmed_at)) invalid();
  } else invalid();
  payload(row.fact);
  return row as AcceptedFact;
}

export function acceptedFacts(value: unknown, patient: string): AcceptedFact[] {
  if (!Array.isArray(value)) return invalid();
  const rows = value.map(row => acceptedFact(row, patient));
  if (new Set(rows.map(row => row.fact_id)).size !== rows.length || new Set(rows.map(row => row.version_id)).size !== rows.length) invalid();
  return rows;
}

export function acceptedHistory(value: unknown, patient: string, root: string): AcceptedFact[] {
  if (!Array.isArray(value) || value.length === 0) return invalid();
  const rows = value.map(row => acceptedFact(row, patient));
  if (rows[0].version !== 1 || rows.some((row, index) => row.fact_id !== root || row.attribution !== rows[0].attribution || (index > 0 && (row.version <= rows[index - 1].version || row.previous_version_id !== rows[index - 1].version_id))) || new Set(rows.map(row => row.version_id)).size !== rows.length) invalid();
  return rows; // Preserve server version order; observations may be retrospective.
}

function canonical(value: unknown): string {
  if (value !== null && typeof value === "object") {
    const row = value as Record<string, unknown>;
    return `{${Object.keys(row).sort().map(key => `${JSON.stringify(key)}:${canonical(row[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}
export function sameAcceptedVersion(a: AcceptedFact, b: AcceptedFact) { return canonical(a) === canonical(b); }

export function correctionRequest(value: unknown): CorrectionRequest {
  const row = object(value);
  shape(row, ["fact", "observed_at", "provenance", "expected_version_id", "correction_reason"]);
  payload(row.fact);
  if (!timestamp(row.observed_at) || !id(row.expected_version_id) || !text(row.correction_reason)) invalid();
  const provenance = object(row.provenance);
  shape(provenance, ["source", "reference", "data_origin"]);
  if (!text(provenance.source, 200) || !(provenance.reference === null || text(provenance.reference)) || (provenance.data_origin !== "synthetic" && provenance.data_origin !== "de_identified")) invalid();
  return row as CorrectionRequest;
}
