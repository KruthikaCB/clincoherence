# Milestone 4F deterministic verification decision gate foundation

Backend-only, internal, read-only technical eligibility for accepted-record restatements. This does not complete the clinical response gate or all 11 pipeline stages. No endpoint, frontend, middleware activation, migration, durable audit, external LLM, medical interpretation, threshold, treatment recommendation or LAB_DIFFERENCE support is added. Approved requirements/architecture and existing 4A–4E implementations remain unchanged.

## Approved semantics and contracts

Policy: `structured-record-gate-v1`.

- PASS means internal eligibility of the complete server-rendered accepted-record set. It is not clinical correctness, medical support, treatment safety or HTTP release permission.
- REFUSE means this draft failed technical verification and must be withheld. It does not assert a detected clinical hazard or irreconcilable clinical contradiction.
- CLARIFY means an explicitly whitelisted missing input can be supplied. It is not an inference that patient clinical information is missing.
- WARN is reserved. Neither the evaluator nor the output contracts permit its emission in this policy.

Frozen Pydantic contracts use tuples and existing frozen nested contracts. `GateInput` contains the original `EvidenceAwareDraftRequest` and a server-generated `EvidenceAwareRecord`; it is a trusted internal input, never a provider/public request. Pydantic validity alone does not establish that verification occurred. The patient adapter accepts only the original request, obtains its own fresh 4E record and never accepts caller-provided verification, policy decisions, recoverability flags or approvals.

`ClaimGateDecision` contains claim identity, decision/reasons, original grounding and evidence assessments without text, and gate checks. `GateDecisionRecord` includes draft/patient identity, request digest, verification reference, ordered claim decisions, batch decision/reasons, gate policy and checks, plus server-rendered text only for an entirely PASS batch. `unrestricted_text_verified` and `http_release_authorized` are always false. `GateRun.evaluated_at` is separate from deterministic content.

The verification reference preserves the existing current/history snapshot version IDs, 4A findings/ruleset, 4C/4D/4E policy versions, renderer version and catalogue fingerprint. Per-claim evidence assessments preserve rule, intended-use, source/unit/hash and manifest references. A canonical digest binds the complete 4E record, and a separate canonical request digest binds the full original envelope, including prose and claim coverage. These hashes are deterministic identity references, not signatures, authorization capabilities or persisted snapshot identifiers. Claim/binding order is canonicalized for the request digest. No source text is logged.

## Composition and decision rules

The pure `evaluate_gate` revalidates the original request and verification record, including constructed model copies. It does not load facts, resolve evidence, render new prose or duplicate 4A/4C/4D verification rules. It checks contract compatibility and maps their trusted results. Unknown status/reason combinations and unreviewed policy versions fail closed. Existing 4D reason literals are reused, and 4C status/reason combinations are explicitly allowlisted for this gate policy.

| Condition | Decision / fixed gate reason |
|---|---|
| Supported, grounded, verified renderer text, no binding | PASS / ACCEPTED_RECORD_RESTATEMENT |
| Supported, grounded, verified renderer text; requested evidence integrity/approval and applicability RESOLVED | PASS / SYNTHETIC_REFERENCE_CHECKS_COMPLETE |
| Known fact/content/value/unit/time/attribution mismatch | REFUSE / FACT_RESTATEMENT_MISMATCH |
| Unsupported claim type, clinical interpretation or LAB_DIFFERENCE | REFUSE / CLAIM_TYPE_UNSUPPORTED |
| Provider string as claim content, including renderer-identical wording | REFUSE / UNVERIFIED_PROSE |
| Any nonempty unrestricted envelope prose, including whitespace-only text | Batch REFUSE / UNRESTRICTED_PROSE_PRESENT |
| Unavailable accepted reference: missing, foreign, pending, rejected or superseded | REFUSE / ACCEPTED_REFERENCE_UNAVAILABLE |
| 4A accepted-state provenance/chain integrity error | REFUSE / ACCEPTED_STATE_INVALID |
| Absent, withdrawn, unapproved or unsupported-use evidence; unavailable rule/source/unit version | REFUSE / REQUIRED_EVIDENCE_UNAVAILABLE |
| Manifest/binding/edition/hash/locator mismatch or evidence integrity ERROR | REFUSE / EVIDENCE_REFERENCE_INVALID |
| Evidence applicability NOT_APPLICABLE or INSUFFICIENT_CONTEXT | REFUSE / EVIDENCE_CONTEXT_NOT_APPLICABLE |
| Unknown/inconsistent verification, malformed verified record, unreviewed policy, missing required applicability | REFUSE / VERIFICATION_CONTRACT_INVALID |
| Missing/extra/duplicate claim results, inconsistent bindings/references or invalid snapshot membership | REFUSE / CLAIM_COVERAGE_INVALID |
| Missing verified renderer text | REFUSE / RENDERER_TEXT_UNVERIFIED |
| Invalid catalogue fingerprint supplied to the pure internal evaluator | REFUSE / CATALOGUE_INTEGRITY_INVALID; production 4E service instead withholds at its boundary |
| INVALID/ONE_FACT_REFERENCE_REQUIRED and exactly zero selected references | CLARIFY / REQUIRED_INPUT_MISSING |
| INVALID/STRUCTURED_CONTENT_REQUIRED and content is actually None | CLARIFY / REQUIRED_INPUT_MISSING |
| INVALID/SOURCE_ATTRIBUTION_REQUIRED and attribution is actually None | CLARIFY / REQUIRED_INPUT_MISSING |
| Empty supported-claim batch, without a harder failure | CLARIFY / NO_SUPPORTED_CLAIMS_SELECTED |
| WARN | Never emitted; GATE-WARN-01 is skipped with WARNING_POLICY_NOT_CONFIGURED |

CLARIFY requires both the matching trusted 4D result and the actual omission in the frozen original request. Multiple references, arbitrary strings, malformed envelopes, missing/foreign/stale versions, evidence errors and integrity errors are not recoverable omissions. A known evidence failure overrides an otherwise recoverable missing field. Current 4E fixtures cannot produce recoverable insufficient evidence context for a grounded single-fact restatement; no new missing-context inference or clinical question generator is introduced.

Precedence: boundary failure withholds before consumption; otherwise any REFUSE overrides CLARIFY, which overrides PASS. PASS requires a nonempty fully passing batch with no unrestricted prose. Per-claim PASS can coexist with a failed batch, but **all batch text is withheld** unless every claim passes. Positive and negative per-claim reason codes remain in the deterministic aggregate reason set. No partial release or silent omission of failed claims is allowed.

Complete one-to-one coverage checks compare the original claim IDs with all 4E result IDs, including unsupported claims. Grounding references must match the original references (except 4D's deliberate unavailable-reference redaction), grounded versions must be current, snapshot IDs must be unique with current included in history, and bindings must match exactly. Scope mismatch aborts without a patient decision record. Invalid coverage/schema records do not echo their untrusted metadata; known authorized integrity failures retain their snapshot/findings reference.

4A POSSIBLE_DUPLICATE and NOT_COMPARABLE findings are preserved separately and do not create WARN or clinical contradictions. 4B remains separate and cannot make a LAB_DIFFERENCE draft claim eligible. Synthetic evidence RESOLVED establishes neither medical correctness nor clinical support. Unsupported descriptive prose is never promoted to verified text.

## Patient adapter and protected consumption

`evaluate_patient_gate` freezes and revalidates one complete original envelope before awaiting database work. It passes that immutable request to 4E and gates the returned fresh record. This prevents mutable dictionaries dropping prose/claims between verification and gating. Malformed requests still pass through 4E's authorization-first error boundary before any shape error is disclosed.

Current practitioner eligibility, consent, accepted-state loading, original-speaker confirmation provenance and patient locks remain 4E/4A responsibilities. No facts, proposals, decisions, consents or evidence assets are modified, flushed or committed by 4F. Caller owns transaction cleanup and must retain locks through consumption.

`consume_patient_gate` obtains a fresh 4E record, then calls the existing `consume_patient_evidence_aware_draft` guard. That guard reevaluates current authorization, snapshot and catalogue, compares its expected record, rechecks the catalogue immediately before its synchronous callback and retains the patient transaction locks. Inside that callback 4F recomputes the entire gate, compares it with the expected gate (including original-request and verification digests) and invokes the trusted consumer only for PASS. Changed context yields sanitized 409; a withheld gate yields sanitized 409 without invoking the consumer. Async consumers are rejected. Initial evaluation is not a transferable permission or a general response-release mechanism.

Existing 4E boundary failures remain sanitized service failures, not fabricated patient-specific gate decisions: 401/403 for authentication/eligibility/consent, 409 for scope/unavailable context or loader failure, 422 for malformed original envelopes, and 503 for unavailable/changed catalogue. In particular, schema-invalid stored provenance may fail at the inherited loader before a gate record exists; typed 4A integrity findings produce REFUSE. Errors disclose no hidden facts, foreign identifiers, patient existence or transcript content. Duplicate original request IDs are malformed-envelope errors; duplicate verification coverage is a gate REFUSE.

Revocation/corrections committed first invalidate waiting/new work. Active patient gates prevent revocation/corrections from committing until protected synchronous consumption finishes. Dropping locks requires fresh reevaluation before delayed use. Future HTTP integration must separately use the existing non-streaming protected release mechanism. Trusted consumers must remain synchronous and bounded, must not release locks, stream, defer disclosure or retain text for later disclosure. Delivered bytes cannot be retracted.

## Catalogue policy and limitations

Reuse 4E's deployment-frozen catalogue: no hot updates; reviewed version/pin changes require controlled deployment and draining active work; delayed consumers revalidate. No atomic withdrawal across workers, filesystem changes or deployments is claimed. A gate decision does not change that limitation.

Coverage is relative to the complete original envelope supplied by the trusted drafting orchestration. This foundation cannot detect facts/prose omitted before that envelope was constructed, and provides no durable cross-request draft-ID registry or cryptographic attestation. It does detect omitted claims between that original request and verification, and binds delayed consumption to both. No clinical evidence support, unrestricted semantic prose verification, warning policy, clinical hazard classification or durable gate audit is implemented. Existing research-scale history loading, repeated pure validation and coarse locks remain.

## Validation

Commands run from `backend` using its existing virtual environment:

```text
.venv/Scripts/python.exe -m pytest tests/test_verification_gate.py -q
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m mypy app/schemas/verification_gate.py app/services/gate_evaluator.py app/services/verification_gate.py --follow-imports=silent
```

Ruff check and format checks cover the three new source files and the new test file. No dependencies were installed.

Full backend suite: 489 collected; 335 passed, 0 failed, 154 skipped (4.72 seconds). New gate coverage: 66 passed, 0 failed, six PostgreSQL integration tests skipped. TEST_DATABASE_URL is absent in this process. No development database URL was used or substituted. The dedicated PostgreSQL tests for consent/isolation, confirmed versus pending/rejected state, read-only history preservation, corrections, both revocation race orders, protected consumption and catalogue changes remain unvalidated here. Earlier full PostgreSQL validation of 4E (417 passed) does not validate these new tests.

Final Ruff checks and formatting checks passed for all four new Python files. Targeted mypy passed for all three new source files. No frontend files changed and no frontend tests were run.
