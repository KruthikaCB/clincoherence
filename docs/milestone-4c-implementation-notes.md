# Milestone 4C synthetic evidence approval and retrieval foundation

This increment is internal, backend-only and read-only. It adds no clinical rule, source authority, support/contradiction claim, threshold, treatment recommendation, unit conversion, LLM call, endpoint, frontend, migration or durable audit persistence. Requirements/architecture are unchanged. All fixture evidence is labeled SYNTHETIC_FIXTURE_ONLY and contains no clinical recommendations.

## Contracts and assets

Frozen Pydantic contracts use tuples and immutable scalar fields: EvidenceSource, EvidenceUnit, EvidenceApproval, RuleDefinition, VerificationRequest and EvidenceEvaluation, plus explicit reference/selection/qualifier/result contracts. Approval manifests and registry/bundle containers are also frozen. Prepared catalogue/resolution records are frozen dataclasses. The clinical FactVersion contracts are reused without mutation.

Three separate assets live under `backend/app/fixtures/evidence`:

- `bundle.json`: one synthetic source and one bounded synthetic content unit with source version/edition, unit version, locator, label and content hashes.
- `rules.json`: two fixture rules, `synthetic-patient-lab-context` and `synthetic-practitioner-lab-context`, both version fixture-1. They require an explicitly selected accepted laboratory/hba1c/% fact and the corresponding attribution category. This tests field matching, not a medical recommendation. The sole executable logic version is synthetic-structured-context-v1.
- `approvals.json`: trusted manifest synthetic-evidence-approvals/fixture-1. Each approval binds an exact source edition/version/hash, unit version/hash, rule version/hash and intended use synthetic-context-testing. The approval reference milestone-4c-synthetic-only records only authorization for synthetic infrastructure fixtures, never clinical source approval.

The backend pins the canonical SHA-256 digest of each whole asset in code. Entity content hashes cover their canonical JSON fields except the content_hash field itself; source metadata, unit source binding/locator/content and rule requirements are included. Canonical JSON sorts keys, uses compact separators and UTF-8; whitespace and platform line endings do not affect pins. Duplicate JSON object keys and duplicate source/unit/rule identities are rejected. Files are fixed-path local assets, bounded to 64 KB each; no caller path, live download, environment catalogue override, latest-version fallback or automatic evidence selection is supported. Package-data configuration includes the JSON files in backend distributions; no dependency was added.

## Trust and approval semantics

`prepare_evidence` always reads the code-pinned assets. Patient services accept only VerificationRequest, not supplied manifests, catalogue objects, approval booleans, evidence text, qualifiers or executable logic. The deterministic `resolve_catalog` helper accepts a trusted catalogue for core testing; it is not a production trust boundary or an API. Likewise, prepared evidence passed to the pure context evaluator is internal resolver output, never user material.

Exact manifest ID/version, requested rule version, intended use and complete evidence reference set must match. All required bindings must have exactly one approved manifest entry before unit resolution; missing, conflicting, withdrawn or mismatched entries fail closed. Edition, source/unit hash, unit identity, source binding and locator are checked. Failed resolution never exposes evidence content or matched patient IDs. Output includes resolved references only after all required units pass.

Withdrawal applies to the exact rule/unit/intended-use approval binding. Source-wide withdrawal requires withdrawing every applicable binding. Changes require a reviewed manifest/version and corresponding code trust-anchor deployment; arbitrary edits are rejected, not automatically trusted. The initial resolver has no approval expiry, automatic date policy, signed approval workflow or historical fallback. Source publication/effective-date semantics are not invented for these synthetic fixtures. These remain real-source governance prerequisites.

## Processing checks and statuses

- EV-SOURCE-APPROVAL-01: exact manifest/rule/intended-use/reference approval and source identity/hash/edition.
- EV-UNIT-RESOLUTION-01: exact unit identity/hash/source binding and locator.
- EV-CONTEXT-APPLICABILITY-01: only explicitly selected current accepted patient facts, exact kind/code/unit and supported structured qualifiers (attribution, author_role, entry_method). No numeric values are interpreted, no unit conversions occur, and descriptions/proposals/transcripts are never context sources.

RESOLVED means only synthetic evidence resolution plus required structured context matching. UNAVAILABLE means absent, withdrawn, mismatched or unapproved material/intended use. INSUFFICIENT_CONTEXT means no required context was explicitly supplied. NOT_APPLICABLE means selected structured context does not match fixture kind/code/unit/qualifiers; empty rule selection is an explicit no-op. ERROR means malformed/untrusted catalogue material or a 4A structural integrity failure preventing context evaluation. No status maps to a clinical gate or clinical correctness. Root status uses fixed conservative priority ERROR, UNAVAILABLE, INSUFFICIENT_CONTEXT, NOT_APPLICABLE, then RESOLVED; individual rule results remain separate.

Missing, foreign, superseded, PENDING or REJECTED selected version IDs raise the same fixed unavailable error (service 409). Mixed-patient input fails before a result is constructed. Complete accepted history/current selection and structural validation reuse 4A; its findings are preserved separately, never converted into clinical classifications. Unknown requested identifiers are not echoed. Invalid provenance/schema errors are sanitized by the shared loader.

Results identify policy synthetic-evidence-context-v1, patient snapshot version IDs, 4A ruleset version, selected rule versions, resolved source/unit edition/version/hash references and actual trusted manifest ID/version/hash. They contain fixed reason codes and per-rule checks run/not applicable. Inputs, rule results, matched version IDs and evidence references are deterministically ordered; duplicate selected fact IDs collapse. `evaluated_at` is only in a separate EvidenceRun envelope. There is no implicit evaluation date governing rule selection.

## Patient service and protected consumption

`evaluate_patient_evidence` prepares patient-independent local evidence before taking patient locks. It then calls 4A's load_accepted_state (clean session, disabled autoflush, current authentication/consent and shared patient lock), runs the pure evaluator and re-resolves the bounded local catalogue before returning. If approval/catalogue material changes during the operation, the result is withheld with fixed 503. Final current-consent authorization is rechecked. Unauthorized callers are denied even when evidence is unavailable.

No facts, evidence, confirmations or audit rows are written, flushed or committed. Original-speaker confirmation remains unchanged. The caller retains transaction/locks through authorized consumption and owns cleanup; a future endpoint must use the existing non-streaming release gate. Dropping locks and releasing cached results without reauthorization is unsafe. The final catalogue recheck is not a push withdrawal mechanism: future delayed consumption must also validate current trust material. Already delivered information cannot be retracted.

## Validation and remaining prerequisites

Full backend suite: 319 collected; 184 passed, 0 failed, 135 skipped (8.85 seconds). New evidence tests: 48 passed, six PostgreSQL integration tests skipped. TEST_DATABASE_URL was unavailable; no development database fallback was used. New database isolation, confirmation preservation and revocation-race coverage still requires dedicated PostgreSQL validation. Ruff/formatting passed for five new Python files; targeted mypy passed for four source files.

Real clinical verification still requires explicit source/version/location review, actual claim contracts, patient applicability qualifiers, terminology/product/route/event mappings where needed, reviewed clinical logic, missing-context policy, publication/effective-date/withdrawal governance and separately approved durable audit design. Fixture hashes prove reproducibility, not clinical truth or de-identification. The local static catalogue, complete-history loading and coarse locks remain research limitations.
