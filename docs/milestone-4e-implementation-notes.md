# Milestone 4E synthetic evidence-aware draft verification foundation

Internal, backend-only, read-only composition. This is not a general release-ready clinical response or a clinical correctness assessment. Approved requirements, architecture and existing 4A–4D contracts are unchanged. No endpoint, frontend, migration, durable audit, real LLM, laboratory-difference claim, clinical evidence-support assertion, threshold, interpretation, diagnosis, prescribing, treatment recommendation or clinical gate mapping is added.

## Contracts and policies

`EvidenceAwareDraftRequest` wraps the unchanged 4D `DraftVerificationRequest` and a tuple of explicit `ClaimEvidenceBinding` values. Each binding identifies one existing claim and exactly one 4C `RuleSelection`, requested approval-manifest identity/version and intended use. No binding means 4D-only behavior. Duplicate/unknown claim bindings and extra fields are rejected. A caller cannot supply patient context, approval flags, evidence contents, catalogue objects or precomputed verification outcomes. Constructed model instances are dumped and revalidated by the service.

The contracts are frozen, with tuple collections and the existing frozen nested contracts. Patient scope and selected fact-version context are derived exclusively from the bound claim and independently loaded patient snapshot. No transcripts, free text, pending proposals or rejected proposals supply evidence context.

`EvidenceAwareRecord` includes draft/claim identities, existing sorted current/history version references, 4A ruleset/findings, 4D grounding policy and renderer versions, 4C evidence policy, requested rule/manifest/intended-use bindings, actual manifest identity/version/hash, resolved source/unit edition/version/hashes, matched accepted fact versions and checks performed/not applicable. The catalogue fingerprint hashes the complete validated synthetic bundle, registry and manifest; it is not a patient snapshot ID. `evaluated_at` is only in `EvidenceAwareRun`. No aggregate snapshot identifier or persisted verification record is invented.

Composition policy: `synthetic-evidence-aware-restatement-v1`; grounding: `structured-fact-restatement-v1`; evidence: `synthetic-evidence-context-v1`; renderer: `record-restatement-text-v1`. The new processing check is `EA-COMPOSITION-01`.

## Composition and withholding

`prepare_batch` uses only 4C's code-pinned local loader and resolver. It fingerprints the complete catalogue before and after resolution, including when no evidence bindings were requested. Preparation is patient-independent and occurs before acquiring patient locks. Prepared batches are trusted internal values, never provider inputs.

`evaluate_evidence_aware_draft` reuses 4D's pure verifier, which independently checks 4A accepted-state integrity, correction chains, provenance, current-version membership, exact typed content/time and attribution. It then reuses 4C's context evaluator for grounded bound claims. Existing 4C rules/statuses/reason codes are not reimplemented or mapped to clinical outcomes.

Four dimensions remain distinct:

- Grounding metadata contains 4D outcome, fixed reason, accepted references and checks, **without** its text fields. Existing `ClaimResult` is unchanged.
- Evidence integrity/approval contains the original 4C resolution result. Missing, mismatched, unapproved, withdrawn or unsupported intended uses retain their 4C fixed reasons.
- Applicability contains the original 4C context result only when resolution succeeded and the claim is grounded. Otherwise it is absent with an explicit skipped check; absence never means applicability.
- `text_verified` applies only to the separately projected server-rendered record quotation. Bound claims require GROUNDED grounding, RESOLVED integrity and RESOLVED applicability. Arbitrary provider/envelope prose is never verified or echoed, even if its bytes match renderer output.

No-binding claims record `EVIDENCE_NOT_REQUESTED`; bound nongrounded claims record `CLAIM_NOT_GROUNDED`. Applicable synthetic matches record `SYNTHETIC_CONTEXT_MATCHED`; binding-specific evidence failures record `EVIDENCE_WITHHELD`, with the exact 4C status/reason retained in the evidence assessment. These codes describe processing only. Synthetic RESOLVED never means clinical support or medical correctness.

Per-binding unavailable/withdrawn/mismatched/not-applicable evidence withholds that claim's text without changing its grounding assessment. Accepted-state integrity failure or invalid trusted catalogue withholds all text. Missing/foreign/pending/rejected/superseded references retain 4D's batch-wide UNVERIFIABLE behavior and do not echo unavailable reference IDs. Authorized accepted historical IDs still appear in the reproducible snapshot. Mixed patient inputs abort without a record.

The current fixture rules require one hba1c/% observation with patient-reported or practitioner-managed attribution. Different kinds/codes/units/qualifiers return 4C NOT_APPLICABLE reasons; no unit conversion or numeric interpretation occurs. INSUFFICIENT_CONTEXT remains an unchanged 4C status but is not reachable for a grounded single-fact restatement with these fixtures: an absent fact reference fails 4D structure/reference checks first. No extra context is invented to manufacture that outcome. Multi-fact rules require a separately approved future contract.

## Patient service and protected internal consumption

`verify_patient_evidence_aware_draft` reuses 4A's clean-session/no-autoflush accepted-state loader and patient-level locks, current practitioner eligibility and explicit patient consent. Every claim uses the same authorized current/history snapshot. Malformed envelopes return a sanitized 422 only after the shared authorization/clean-session loader; scope/context errors return sanitized 409. Authorization is checked again and the entire trusted catalogue fingerprint is checked immediately before returning. Invalid or changed catalogue material returns 503 with no record. No clinical values, tokens, proposed text or validation details are logged.

The caller owns transaction cleanup and retains locks through internal use. `consume_patient_evidence_aware_draft` reevaluates the original request against current authorization, state and catalogue, compares the deterministic record with the prior expected record, then revalidates the catalogue immediately before invoking a trusted, synchronous, bounded consumer. No await occurs between that final check and invocation. Changed state/record requires reevaluation (409); changed/untrusted catalogue withholds consumption (503); revoked access denies consumption. Async consumers are rejected. The consumer must not defer disclosure, stream, release locks or retain text for later disclosure. Expected records are not authorization capabilities: fresh evaluation supplies the actual consumed record.

Dropping locks requires this reauthorization/reevaluation path before delayed use. An in-flight patient gate prevents consent revocation and patient corrections from committing until protected consumption completes; an already committed revocation denies subsequent work. No locks span external LLM calls because none exist. No facts, confirmation decisions, evidence assets or audit rows are inserted, updated, deleted, flushed or committed by this service. Original-speaker confirmation and immutable history remain existing responsibilities, independently validated before grounding.

This helper is **not** an HTTP release gate. A future endpoint must use the existing protected non-streaming response-release mechanism, and separately design any asynchronous consumer. Delivered information cannot be retracted.

## Frozen-catalogue deployment policy

Approved synthetic assets are immutable within a deployment. Hot approval updates are unsupported. New reviewed catalogue versions and trust pins require a controlled deployment that drains active evaluation/consumption work. Delayed consumers revalidate. Fingerprints/rechecks detect changes; they do not provide atomic withdrawal across workers, filesystem writers or deployments. No cross-worker/cross-deployment atomic catalogue withdrawal guarantee is claimed. Runtime tampering fails closed, not as an accepted hot update.

## Validation and limitations

Full backend suite: 417 collected; 269 passed, 0 failed, 148 skipped (4.93 seconds). New 4E coverage: 44 passed, 0 failed, six PostgreSQL tests skipped. `TEST_DATABASE_URL` is absent in this process, so PostgreSQL integration and concurrency coverage remains unvalidated here; no development database fallback was used. Existing tests remain preserved. Ruff, formatting and targeted mypy passed for the new Python files after final validation.

New coverage includes independent assessments and per-claim withholding, no-binding behavior, exact binding/manifest/intended-use failures, trusted withdrawn/missing approval fixtures, incompatible context, unavailable versions, isolation, arbitrary prose, forged flags/context, duplicate/unknown bindings, determinism/immutability, catalogue tampering, synchronous guarded consumption and sanitized model revalidation. Dedicated PostgreSQL tests cover patient authorization/consent, accepted versus pending/rejected versions, read-only fact/confirmation preservation, correction staleness, both revocation race orders, correction blocking and catalogue change before consumption.

Remaining limitations: synthetic-only evidence, research-scale complete-history loading/coarse patient locks/repeated pure validation, no independent semantic verification of prose, no evidence-support assertions, no durable verification history, no distributed withdrawal protocol and no general clinical response release. Real clinical sources/rules and their governance need separate approval. Existing defenses do not establish detection of privileged database edits made before authoritative snapshot loading.
