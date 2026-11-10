# Milestone 7A — Versioned synthetic provenance release

## Scope and meaning

The existing record-question panel has an opt-in **Include synthetic demonstration
provenance** checkbox. The JSON request adds only the strict boolean
`include_synthetic_provenance: true`; omission/false keeps the existing behavior.
This is a request for additional checks, never a caller approval or PASS flag.
The sole supported evidence question intent is a unique latest recorded HbA1c in
`%`, for either fact attribution category. Existing exact question grammar,
observation-time selection, missing-context and tie handling remain. Unsupported
kinds, units or questions clarify rather than falling back to an unbound answer.

Only the unchanged code-pinned 4C synthetic source, unit, two attribution-specific
rules and approval manifest are used. Their intended use remains
`synthetic-context-testing`. No threshold, diagnosis, guideline recommendation,
interaction, contraindication, clinical evidence-support claim, real source,
external API or dataset is added. RESOLVED integrity and structured applicability
are distinct from factual grounding and never imply clinical correctness.

## Protocol compatibility

- `record-response-json-v1` remains the default and its exact JSON encoding is
  unchanged, including for existing 5B clients with evidence bindings.
- The new opt-in `record-evidence-json-v2` contains exactly
  `representation_version`, `records` and `provenance`. Each record retains the
  existing claim ID/server-rendered text shape. Provenance must cover those claim
  IDs once, in the same order.
- Each frozen, allowlisted provenance entry contains claim/accepted-version
  references; the synthetic source's ID, version, edition, title, publisher,
  fixture location, locator list and hash; exact evidence-unit reference/hash and
  locator; rule ID/version/hash; approval-manifest ID/version/hash; intended use;
  separate RESOLVED integrity/applicability statuses; a synthetic-only label;
  and `clinical_support_asserted: false`. No evidence content or private transcript
  is released as provenance.

The server selects the fixed fixture rule using the authoritative fact's
attribution, not the asking actor's role. 4D independently grounds the structured
claim, and 4C/4E independently resolve approval, exact references and applicability.
4F must grant technical PASS before any clinical response can be prepared.

## Protected byte binding

`AuditedRecordResponse` takes a trusted server-only `include_provenance` flag,
defaulting to false. No endpoint accepts a receipt, release-intent ID, verified
text or caller-provided gate result. The question endpoint sets the flag from the
validated opt-in after building the actual server evidence bindings.

During both T1 and T2, the new projection re-resolves the binding against the
pinned catalogue, checks it agrees with the independent gate's evidence results,
requires applicable HbA1c/% fixture rules and exact matched accepted-version
references, and sources display metadata from the trusted assets. Fingerprints
are checked before and after projection. Missing/unbound/withdrawn/mismatched
evidence or catalogue integrity failure withholds the whole response.

The existing canonical encoder covers **all record text and all provenance
metadata** in the one SHA-256 byte digest. T1 persists the existing metadata-only
gate audit and one-shot intent, including the chosen representation, and commits
before send. T2 independently revalidates current authorization/consent, gate,
accepted-state snapshot, audit identity, representation, catalogue and exact bytes.
The existing patient locks, connection checks, bounded handoff, cancellation,
disconnect handling and replay denial remain in force. No asynchronous database
work is added to synchronous verification consumers.

Audit schemas/fields are unchanged. Existing audit bindings retain source/unit
hashes, manifest identity/hash, rule and policy versions and catalogue fingerprint;
the release intent's digest additionally binds the full displayed metadata. Source
hashes/fingerprint cover the catalogue metadata rather than copying source titles
or other prose into audit rows. An intent proves neither delivery nor reading.

## Additive migration

File: `0008_evidence_release_representation.py`; revision ID
`0008_evidence_release` (within Alembic's default 32-character column); parent
`0007_response_release_intent`.

Upgrade replaces only `ck_release_representation` with an allowlist for v1 and v2.
No table/column, row update/deletion, trigger, FK, unique constraint or audit
permission changes. ORM metadata matches. Downgrade refuses while any v2 intent
exists, before narrowing the constraint; it never erases append-only history to
make downgrade possible. The offline schema recorder now models CHECK constraint
replacement and retains its full PostgreSQL DDL comparison. Shared test migration
registration adds 0008; the guarded launcher is unchanged and uses that same list.
This migration was not applied to any database during implementation.

## UI and privacy

The UI shows the emphatic synthetic/not-clinical-guidance label, both assessment
statuses and source/version/hash metadata only after a complete valid v2 response.
It validates exact shapes, synthetic identities, locator/reference consistency,
digest syntax and claim coverage; no partial record text is displayed if provenance
is malformed. The backend remains the authority for cryptographic trust and patient
authorization; frontend parsing is not an independent approval mechanism.

Source locations are plain metadata, not clickable/fetched links. React renders
titles as text, never HTML. Editing the question, changing the option, refreshing,
correcting, confirming/extracting, consent changes, switching patients or signing
out clear/unmount the prior answer and provenance. Late/aborted responses are
discarded; authorization denial clears protected context. There is no automatic
retry, browser persistence, analytics, log, URL or transcript storage.

## Remaining limits

Only synthetic structured-context matching is demonstrated; there is no medical
guidance. Lookup-time selection does not prove completeness/latestness after
additional roots arrive before T1. Existing frozen-per-deployment catalogue and
draining rules remain: no hot approval changes or atomic cross-deployment
withdrawal guarantee. Historical reproduction requires the pinned asset version.
The guarded live suite is not run in this milestone. PostgreSQL tests require an
explicit dedicated `TEST_DATABASE_URL` and must be reported separately from unit
and mocked browser validation. V2 safely withholds if migration 0008 is absent.

## Validation

- Full available backend suite: **626 passed, 0 failed, 224 skipped** (850
  collected). Every database-dependent test skipped because `TEST_DATABASE_URL`
  was absent. The new evidence-release module: **16 passed, 5 skipped**, including
  focused checks after the final exact source-version/hash lookup hardening.
- Focused question/provenance Playwright: **32 passed, 0 failed, 0 skipped**.
- Full mocked frontend rerun: **172 passed, 0 failed, 0 skipped**. An earlier run
  had one login-setup timeout before the evidence assertion; it passed unchanged
  in isolation. No assertion was removed, timeout increased or retry enabled.
- Ruff, formatting, configured targeted mypy, ESLint, TypeScript, production build
  and diff checks passed. The migration recorder failure found by full regression
  was fixed by modelling CHECK constraint replacement, preserving schema assertions.

No database connections, migration application, guarded live run, production or
development database operations, dataset integration or external provider calls
were performed. Dedicated PostgreSQL migration/constraint and end-to-end v2
validation remain required separately; unit/mocked results do not establish those.
