# Milestone 4D independent structured fact-claim verification

Backend-only, read-only FACT_RESTATEMENT checking against accepted patient records. No draft-generation/provider integration, free-prose verification, LAB_DIFFERENCE implementation, clinical interpretation, new diagnosis, prescribing, threshold, evidence-support assertion, gate mapping, endpoint, frontend, migration or durable audit persistence. Approved requirements/architecture and existing workflows are unchanged.

## Immutable draft and record contracts

`DraftVerificationRequest` contains draft_id, patient_id, a bounded tuple of claims and optional unverified_text. Each `DraftClaim` has claim_id, patient_id, claim_type, fact_version_ids, source_attribution and proposed_content. Supported content is a typed fact payload plus a timezone-aware observed_at. Attribution explicitly asserts author ID, author role and patient-reported/practitioner-managed category. No provider-supplied confirmed/correct/verified flags or extra fields are accepted.

Frozen subclasses reuse all five existing typed fact schemas (laboratory, diagnosis, medication, symptom, history) and validators without changing the accepted-state vocabulary. Nested draft content and attribution are frozen, not only the envelope. Strings can represent unsupported prose but are never interpreted as factual content. Claim IDs must be unique in a draft. Engineering bounds are 32 claims, eight references per claim, 4,000 characters for unsupported claim prose and 16,000 for envelope text; a supported restatement requires exactly one reference.

`ClaimResult` contains an approved internal outcome, fixed reason code, authorized version references, checks run/not applicable, optional rendered_text and text_verified. `DraftVerificationRecord` contains draft/patient IDs, policy structured-fact-restatement-v1, renderer record-restatement-text-v1, accepted current/history snapshot IDs, the 4A ruleset/findings and deterministically ordered claim results. `unrestricted_text_verified` is always false. `evaluated_at` appears only in a separate `DraftVerificationRun` envelope. No result is a signature, permission grant, persisted audit or clinical gate outcome. Verification retries are read-only; there is no durable cross-request claim-ID/replay registry.

## Independent verification and outcomes

The patient service loads current accepted versions and complete accepted correction history from the existing 4A loader, never from provider content. 4A membership, provenance, correction-chain, attribution and current-selection checks are reused unchanged. Source author/category must match the authoritative version. Typed proposed payloads must match every applicable field, including kind, code/category, laboratory value/unit, medication name/dose/unit/frequency, and descriptions. Observation instants must match. Numeric spelling and timezone offsets may differ while representing the same exact Decimal/instant; there are no tolerances, conversions, semantic mappings or clinical rules.

Checks: DV-STRUCTURE-01, DV-FACT-REFERENCE-01, DV-ATTRIBUTION-01, DV-FACT-CONTENT-01 and DV-RENDER-01.

- GROUNDED / FACT_RESTATEMENT_MATCH: typed content, observation and attribution exactly match a current accepted record. This means record correspondence only, never clinical correctness, medical support or permission to act.
- UNGROUNDED: a resolvable claim differs from accepted kind/code/value/unit/content, observation time or source attribution. Fixed mismatch reasons identify fields without echoing proposed values.
- UNSUPPORTED / CLAIM_TYPE_UNSUPPORTED: any type other than FACT_RESTATEMENT, including prose, LAB_DIFFERENCE and clinical interpretations. No such claim is executed or rendered.
- UNVERIFIABLE / REFERENCED_STATE_UNAVAILABLE: at least one requested version is missing, foreign, pending, rejected or superseded. All claims in that batch withhold text and redact reference IDs. The unavailable condition is deliberately indistinguishable; no partial grounded text escapes from another claim. Superseded references are not automatically rebound to successors.
- INVALID: a supported claim lacks exactly one reference, structured content or attribution; or 4A structural/provenance integrity blocks verification. All affected claims have no text. Duplicate claim IDs or malformed typed envelopes instead receive a fixed service 422 before a verification record is constructed.

Mixed request/claim/patient scope aborts with a fixed service 409 and no record. Mixed-patient accepted inputs also fail closed. Malformed accepted-state schema/provenance is sanitized by the shared loader (409); validly typed but inconsistent state yields INVALID/ACCEPTED_STATE_INVALID. All requested references are preflighted before any rendering. Unavailable-reference blocking has priority over per-claim evaluation. Otherwise 4A integrity failures block the batch before claim-specific checks.

## Renderer boundary

`render_restatement` emits a fixed plain-text record quotation identifying attribution, fact kind, author, version and UTC observation timestamp, followed by canonical JSON field data. It is generated from the matched server-loaded version, never supplied prose. Decimal strings are canonicalized without rounding or using caller precision. Free-text record fields are JSON-quoted data; control characters and HTML delimiters are escaped and round-trip to the original values. Consumers must still treat the output as plain text.

A recorded diagnosis or medication entry is quoted as an attributed record, never asserted as a new diagnosis or treatment instruction. Descriptions are matched/quoted literally; their meaning and clinical assertions are not verified. Only the generated rendered_text field of a GROUNDED result can have text_verified=true. Arbitrary envelope/provider prose remains unverified even if its bytes happen to equal renderer output. Standalone rendering grants no verification status. No whole answer, omitted claim or surrounding natural language is certified.

## Patient service and protected consumption

`verify_patient_draft(db, account_id, patient_id, draft)` accepts an explicitly scoped patient separate from untrusted draft data. It obtains 4A's clean-session/no-autoflush lock and current eligibility/consent authorization before parsing the draft. Typed request instances are dumped and revalidated with serializer warnings suppressed, so model-copy bypasses and malformed fields cannot become trusted assertions or leak in validation logs. No proposed content or unverified prose is logged or echoed in errors.

After deterministic evaluation, current authorization is checked again. No facts, proposals, confirmations or evidence records are inserted, updated, deleted, flushed or committed. Evidence assets/services are not consulted or changed: this milestone makes no evidence-related assertions. Original-speaker extraction confirmation remains required and unchanged.

The caller owns transaction cleanup and retains locks through authorized consumption. A committed revocation denies waiting/new operations; an existing verification gate prevents revocation/corrections committing before protected consumption finishes. Future APIs must use the existing non-streaming response-release gate. Releasing cached results after dropping locks without current authorization and snapshot validation is unsafe. Already delivered bytes cannot be retracted. Existing database triggers prevent ordinary history mutation; this foundation does not prove detection of privileged edits made before its authoritative read.

## Validation and limitations

Full backend suite: 367 collected; 225 passed, 0 failed, 142 skipped (3.81 seconds). New draft-verifier coverage: 41 passed, seven PostgreSQL tests skipped. TEST_DATABASE_URL was unavailable and no development database fallback was used. New database isolation, mutation/confirmation preservation, revocation and concurrent-correction tests require dedicated PostgreSQL validation. Ruff/formatting passed for all five new Python files; targeted mypy passed for four source files.

No clinical interpretation or unrestricted draft-text coverage is implemented. There is no evidence support, claim extraction, automatic correction, provider integration, response gate mapping or durable verification history. Complete-history retrieval, coarse patient locks and inherited 4A pairwise checks remain research-scale constraints.
