# ClinCoherence v1.2 — change summary

Status: **Approved MVP requirements baseline — implementation subject to stated dependencies**

The requirements incorporate the final review clarifications. Architecture documentation is now aligned and an incremental implementation plan is prepared. No application code, database models, API contracts, or README were changed. Requirements approval does not authorize application changes in this documentation task.

## Comparison basis and limitation

Comparison uses the committed Phase 0 `README.md` and `docs/architecture.md`, plus the approved project decisions and final review clarifications. No earlier standalone approved requirements specification was available. The excluded local `lionic_terminology/ClinCoherence_Decisions_Summary.pdf` exists, but its text could not be extracted with available tools; its requirements and approval status are not inferred. This is not a complete redline against an unavailable earlier specification.

Supporting dataset notes reviewed: LOINC README, RxNorm handoff, DailyMed limitations/open questions, and MIMIC demo limitations. They inform provenance and uncertainty safeguards without promoting proposed evidence policies or clinical rules to approved status.

## Scope differences

| Area | Committed Phase 0 documentation | Approved v1.2 baseline |
|---|---|---|
| Modes | Unauthenticated Public and authenticated Practitioner | Two authenticated modes: Patient and Practitioner; Public clinical chat excluded |
| Patient identity | Patient login/portal deferred | Verification and activation separate from consent; controlled synthetic-user verification permitted for local research, labeled prototype-only and not production-grade |
| Onboarding | No invitation workflow specified | Practitioner-initiated copyable, expiring, single-use invitation |
| Patient access | Authorized practitioner access; no explicit patient-managed consent workflow | Individual practitioner accounts, authorization plus explicit patient consent, independent grants/revocations, multiple practitioners |
| Revocation | Not specified | Immediate denial, including existing sessions and pending data release/write operations |
| Fact acceptance | Fact validation/confirmation planned | Individual in-chat cards; only individually confirmed extracted facts enter accepted state. Pending/rejected proposals never enter accepted state; conversation can continue using previously accepted facts with all authorization/verification checks passing, without relying on unconfirmed proposals |
| Structured entry | Not specified | Authorized, validated structured entries do not require an LLM-extraction confirmation step |
| Information ownership | Practitioner-managed longitudinal state | Patients manage own patient-reported facts; authorized and consented practitioners manage practitioner-entered clinical facts; neither silently overwrites the other; corrections are versioned/auditable; shared-record access does not automatically grant cross-participant transcript access |
| Corrections/audits | Event/version history and response/audit persistence planned | Linked correction versions preserving originals and authorship; auditable grants, revocations, confirmations, corrections, and interactions |
| Development data | Synthetic only; real patient data out of scope | Synthetic or appropriately de-identified, subject to suitability/provenance review; identifiable real patient data excluded |
| Evidence | Clinical retrieval planned | Explicit source/version approval and traceable evidence; terminology and label limitations retained |

## Preserved requirements

- Unified, model-agnostic longitudinal clinical LLM verification system, initially T2DM and text-only.
- Mock LLM for initial reproducible development; local/cloud integration later.
- Planned 11-stage pipeline, preserving stage order and independent verification before release. Authentication/consent and clarification now apply to both authenticated modes.
- PASS, WARN, CLARIFY, and REFUSE semantics; PASS is not a guarantee of clinical correctness or safety.
- No autonomous diagnosis, prescribing, or medication changes; clinical authority remains with licensed clinicians.
- Longitudinal state, version history, evidence retrieval, and auditable persistence.
- Existing implementation stack and dataset exclusions are untouched; the baseline defines future behavior rather than claiming completed functionality.

## Review and implementation dependencies

The [approved specification](requirements-v1.2.md), renamed from `requirements-v1.2-draft.md`, supplies functional/non-functional requirement IDs, authorization rules, onboarding/consent and fact/correction workflows, isolation constraints, evidence provenance, acceptance criteria, and MVP exclusions. IDs FR-01–19, NFR-01–07, and AC-01–10 are retained; FR-05, FR-10, FR-12 and AC-02, AC-04, AC-05 incorporate the final clarifications alongside their workflows and pipeline text.

The approved initial permission matrix and controlled synthetic-user identity flow are sufficient for local prototype planning; production-grade identity assurance is not claimed. Production identity/credential assurance remains a dependency for production onboarding. Invitation lifetime/redemption lifecycle remains a configuration dependency. Detailed evidence/source/version review and clinical rule/outcome approval remain dependencies for evidence-backed verification. Cross-participant transcripts remain inaccessible through shared-record permission alone; any proposed access requires a separate explicit policy before implementation. Missing prior approved documents limit historical reconciliation and do not undo this baseline's approval.

Emergency consent override is explicitly excluded. No administrative override, additional product role, clinical threshold, regulatory certification, or numeric performance target has been invented. The [architecture](architecture.md) and [implementation plan](implementation-plan.md) now reflect this baseline. README retains the historical access description pending a separate alignment change; backend implementation remains future work.
