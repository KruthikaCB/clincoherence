import { exact, uuid } from "./patient-selector";

export type SyntheticProvenance = {
  claim_id: string; fact_version_ids: string[]; label: "SYNTHETIC_FIXTURE_ONLY";
  source: { source_id: string; version: string; edition: string; label: string; title: string; publisher: string; canonical_location: string; locators: string[]; content_hash: string };
  evidence: { source_id: string; source_version: string; source_edition: string; source_content_hash: string; unit_id: string; unit_version: string; unit_content_hash: string };
  locator: string; rule_id: string; rule_version: string; rule_content_hash: string;
  approval_manifest_id: string; approval_manifest_version: string; approval_manifest_hash: string;
  intended_use: string; integrity_status: "RESOLVED"; applicability_status: "RESOLVED";
  clinical_support_asserted: false;
};

export function syntheticProvenance(value: unknown, claims: string[]): SyntheticProvenance[] {
  if (!Array.isArray(value) || value.length !== claims.length || !value.length) throw new Error();
  const digest = (v: unknown) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
  return value.map((item, index) => {
    const row = exact(item, "applicability_status,approval_manifest_hash,approval_manifest_id,approval_manifest_version,claim_id,clinical_support_asserted,evidence,fact_version_ids,integrity_status,intended_use,label,locator,rule_content_hash,rule_id,rule_version,source");
    const source = exact(row.source, "canonical_location,content_hash,edition,label,locators,publisher,source_id,title,version");
    const ref = exact(row.evidence, "source_content_hash,source_edition,source_id,source_version,unit_content_hash,unit_id,unit_version");
    if (row.claim_id !== claims[index] || row.label !== "SYNTHETIC_FIXTURE_ONLY" || row.clinical_support_asserted !== false ||
        row.integrity_status !== "RESOLVED" || row.applicability_status !== "RESOLVED" || row.intended_use !== "synthetic-context-testing" ||
        !Array.isArray(row.fact_version_ids) || row.fact_version_ids.length !== 1 || typeof row.fact_version_ids[0] !== "string" || !uuid.test(row.fact_version_ids[0]) ||
        !["synthetic-patient-lab-context", "synthetic-practitioner-lab-context"].includes(String(row.rule_id)) || row.rule_version !== "fixture-1" ||
        row.approval_manifest_id !== "synthetic-evidence-approvals" || row.approval_manifest_version !== "fixture-1" || !digest(row.approval_manifest_hash) || !digest(row.rule_content_hash) ||
        source.source_id !== "synthetic-t2dm-context" || source.version !== "fixture-1" || source.edition !== "synthetic-edition-1" || source.label !== "SYNTHETIC_FIXTURE_ONLY" ||
        source.publisher !== "ClinCoherence synthetic fixtures" || typeof source.title !== "string" || !source.title.trim() || source.title.length > 4000 ||
        source.canonical_location !== "fixture://clincoherence/synthetic-context" || !digest(source.content_hash) ||
        !Array.isArray(source.locators) || source.locators.length !== 1 || source.locators[0] !== "section:structured-context" || row.locator !== source.locators[0] ||
        ref.source_id !== source.source_id || ref.source_version !== source.version || ref.source_edition !== source.edition || ref.source_content_hash !== source.content_hash ||
        ref.unit_id !== "synthetic-context-unit" || ref.unit_version !== "fixture-1" || !digest(ref.unit_content_hash)) throw new Error();
    return item as SyntheticProvenance;
  });
}
