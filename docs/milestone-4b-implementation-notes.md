# Milestone 4B explicit laboratory comparison policy

Approved boundary: internal, deterministic, read-only, laboratory-only comparisons of explicitly selected current accepted version pairs. No migration, public endpoint, frontend change, durable audit write, external provider, threshold, conversion, clinical interpretation, contradiction classification or clinical gate mapping. Approved requirements and architecture are unchanged.

## Internal interfaces

- `evaluate_laboratory_pairs(patient_id, current, history, pairs)` calls the existing 4A evaluator. It does not trust a supplied assertion that a snapshot is valid. Patient, accepted-membership, chain, category and current-selection validation remain 4A responsibilities.
- `compare_patient_laboratories(db, account_id, patient_id, pairs)` uses the shared `load_accepted_state` helper extracted from the 4A service. The helper retains the same clean-session, no-autoflush, current authorization and patient-lock behavior and reads complete CONFIRMED history/current versions. Both services recheck current authorization before returning.
- `VersionPair` contains `first_version_id` and `second_version_id`. No automatic pairs are generated. Pair endpoints are sorted by UUID text, reversed/repeated pairs are deduplicated, and pairs are sorted deterministically.

Every requested ID must resolve to a current CONFIRMED version of the authorized patient. Unknown, foreign, superseded, pending and rejected IDs use the same fixed unavailable error (service status 409) without echoing identifiers. An unavailable requested ID aborts the entire batch, never returning partial numeric comparisons. Superseded IDs are not silently replaced with successor IDs: the caller must explicitly select the latest accepted version. Mixed-patient input fails before a result is constructed.

## Versioned rules and semantics

Policy: `laboratory-explicit-pairs-v1`.

`LC-LAB-ELIGIBILITY-01` checks each explicit pair in fixed priority order:

1. Both payloads are laboratory observations.
2. Distinct fact roots (including rejection of self-pairs).
3. Exact laboratory code, then exact unit.
4. Same author, then same attribution category.
5. Distinct observation instants, compared as timezone-aware datetimes.

Ineligible pairs produce one NOT_COMPARABLE reason in a separate collection: LABORATORY_FACTS_REQUIRED, DISTINCT_FACT_ROOTS_REQUIRED, LABORATORY_CODE_MISMATCH, UNIT_MISMATCH_NO_CONVERSION, AUTHOR_MISMATCH, ATTRIBUTION_MISMATCH or DISTINCT_OBSERVATION_TIMES_REQUIRED. No finding is generated for an unrequested pair by 4B. The existing 4A snapshot evaluation remains separately available and retains its own unchanged rules.

`LC-LAB-DIFFERENCE-01` orders an eligible pair by observation instant and subtracts `later - earlier` as exact Decimal arithmetic. A fresh local decimal context derives sufficient precision from operand widths; caller precision, rounding and traps do not affect the arithmetic. Insignificant decimal zeros and negative zero are canonicalized without rounding. Result precision may exceed the input schema's digit limit, so the output does not reuse that narrower limit. The unit is copied unchanged. No clinical meaning, direction label, tolerance, rate, percentage change, treatment inference or event-identity claim is attached.

Any 4A INTEGRITY_ERROR blocks all arithmetic. Non-integrity 4A findings are preserved separately and do not turn a descriptive difference into a clinical conclusion. Empty requests produce no comparisons and explicit NO_PAIRS_REQUESTED skip reasons. When no requested pair is eligible, the difference rule has an explicit skip reason. Compatible and incompatible local pairs may coexist; neither overwrites the other's result.

## Result and security boundary

`LaboratoryEvaluation` contains policy_version, patient_id, the unchanged 4A consistency evaluation (including ruleset_version and current/history snapshot version IDs), canonical requested pairs, comparisons, not_comparable, checks_run and checks_not_applicable. Each numeric result identifies earlier/later version IDs, numeric_difference and unit. Decimal JSON values are strings. `evaluated_at` exists only in the separate `LaboratoryRun` envelope.

No accepted facts or confirmation decisions are written or altered. No proposals or transcripts are loaded. Original-speaker confirmation remains enforced by the existing workflow. Same-author comparison is deliberately conservative; it does not grant access to that author's private messages.

As in 4A, the caller owns transaction cleanup and must retain the patient lock through authorized consumption. A future API must use the existing protected response-release gate, or reacquire/revalidate authorization and snapshot after dropping the transaction. Results are not permission caches. Coarse patient serialization, complete-history retrieval and 4A pairwise comparisons remain research-scale limitations; no durable verification/audit record is added. Event identity, clinical meaning, broader terminology/units and medication policy remain excluded, not inferred.

## Validation in the implementation environment

- Full backend suite: 265 collected; 136 passed, 0 failed, 129 skipped (8.21 seconds). New laboratory coverage: 27 passed, six PostgreSQL tests skipped. TEST_DATABASE_URL was absent; no development database fallback was used. The new PostgreSQL read-only, isolation and revocation-race tests require separate validation.
- Existing frontend Chromium regression suite: 15 passed, 0 failed, 0 skipped (29.6 seconds). No frontend files changed.
- Ruff lint and formatting checks passed for five affected Python files. Targeted mypy passed for four affected source files. No dependencies were added.
