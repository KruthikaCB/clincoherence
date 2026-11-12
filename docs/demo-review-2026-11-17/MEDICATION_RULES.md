# Implemented medication checks

Catalog: `capstone-label-checks-2026-11-12-v1`, reviewed against primary DailyMed pages on November 12, 2026. Status: **SOURCE_VERIFIED_CLINICAL_REVIEW_PENDING**. This is source verification, not independent clinical approval. No ADA recommendation was encoded. Existing LOINC/RxNorm/DailyMed adapters are preserved; this engine reads a small pinned local catalog, not live adapter or guideline output.

| Rule | Input and deterministic result | Evidence |
|---|---|---|
| MET-RENAL-30-v1 | Recorded latest unambiguous eGFR below 30 → NOT SUITABLE UNDER DOCUMENTED RULE | Metformin SPL v4, sections 2.4, 4, 5.1 |
| MET-RENAL-REVIEW-v1 | eGFR 30 through 45 inclusive → CAUTION / REVIEW REQUIRED | Same label. The demo conservatively includes exactly 45 for review; no initiation/continuation determination is made. |
| EMPA-GLYCEMIC-30-v1 | eGFR below 30 → CAUTION / REVIEW REQUIRED for glycemic-control limitation | Jardiance SPL v31, sections 1, 2.1. This is not a general contraindication or HF/CKD treatment assessment. |
| SEMA-MTC-MEN2-v1 | Explicit personal/family MTC or MEN2 history → NOT SUITABLE UNDER DOCUMENTED RULE | Ozempic SPL v20, section 4. Applies to this product labeling, not an unrestricted class recommendation. |

All eGFR inputs use `mL/min/1.73m2`. Nonpositive values, unsupported units, absent observations and conflicting latest observations are UNKNOWN. No unit conversions or inference from creatinine are performed. A confirmed Type 2 Diabetes diagnosis is required before rules run. Future-dated current facts withhold all rule verdicts. Historical dates describe the fictional scenario; the engine does not establish current renal function.

Latest means greatest **observed_at**, not latest entry timestamp. Distinct current roots at that latest time with disagreeing values/units are ambiguous. Corrections use only the accepted latest version. HbA1c and recorded medications are context, not treatment-escalation or adherence rules. Every card includes the considered snapshot; each individual rule separately lists its bound version IDs.

Exact recognized history templates (case/whitespace normalized) are:

- `prior_event`: `personal history of medullary thyroid carcinoma` or `no personal history of medullary thyroid carcinoma`.
- `family_history`: `family history of medullary thyroid carcinoma` or `no family history of medullary thyroid carcinoma`.
- `other`: `multiple endocrine neoplasia type 2` or `no multiple endocrine neoplasia type 2`.

No general natural-language negation inference occurs. “Possible”, “denies” and unrecognized descriptions are UNKNOWN. Conflicting positive/negative statements withhold this rule. Explicit negatives for all three avoid triggering this one check but never establish overall suitability.

## Sources and version traceability

- [Metformin DailyMed label](https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=bfd6ec0f-8264-4877-aa6d-f347f53bc426), REMEDYREPACK INC., stable set/version `bfd6ec0f-8264-4877-aa6d-f347f53bc426:4`; archive publication October 26, 2026 (page update October 23). Describes renal limits and lactic-acidosis precautions.
- [Jardiance DailyMed label](https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=faf3dd6a-9cd0-39c2-0d2e-232cb3f67565), set/version `faf3dd6a-9cd0-39c2-0d2e-232cb3f67565:31`; archive publication February 2, 2026 (page update January 30). Also provides volume-depletion and ketoacidosis cautions.
- [Ozempic DailyMed label](https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=adec4fd2-6858-4c99-91d4-531f5f2a2d79), set/version `adec4fd2-6858-4c99-91d4-531f5f2a2d79:20`; archive publication June 10, 2026 (page update June 1). Also describes pancreatitis, retinopathy, volume-depletion kidney injury and severe gastroparesis precautions.

DailyMed set links can later show a newer label: use the stored SPL version and archive, not only the current web page. Local catalog bytes are pinned by SHA-256 in the service and returned with each response. Missing, altered or invalid evidence makes all cards INSUFFICIENT INFORMATION and removes rule verdicts. A future catalog update requires source re-verification, deliberate version/hash update, tests and clinical review.

## Expected fictional scenarios

| Scenario | Metformin | Empagliflozin | Semaglutide |
|---|---|---|---|
| A: eGFR 42; metformin recorded; MTC/MEN2 unknown | CAUTION / REVIEW REQUIRED | INSUFFICIENT INFORMATION | INSUFFICIENT INFORMATION |
| B: eGFR 25; explicit family MTC; medication use unknown | NOT SUITABLE UNDER DOCUMENTED RULE | CAUTION / REVIEW REQUIRED | NOT SUITABLE UNDER DOCUMENTED RULE |

There is deliberately **no positive suitability status**. Allergy status, current renal function, medication reconciliation, broader comorbidities, age/reproductive factors, interactions and indication remain incomplete; separate clinician approval is unavailable. No prescription, dose, initiation, discontinuation, treatment ranking or safety clearance is generated. All results require clinician review and remain synthetic educational assessments.
