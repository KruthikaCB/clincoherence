# Milestone 4G verification audit trail and provenance foundation

This increment records internal gate evaluations only. It adds no endpoint, frontend, verification middleware activation, consumer callback, response-release orchestration, real LLM, medical rule, clinical threshold, recommendation or clinical correctness assertion. Approved requirements/architecture and 4A–4F services are unchanged. Clinical facts, confirmation history and evidence approvals are not modified.

## Contracts and stored content

`AuditMetadata` uses schema version `verification-audit-v1` and event kind `GATE_EVALUATED`. Audit schemas are frozen, strict Pydantic contracts with extra fields forbidden, tuple collections, bounded identifier/code formats and explicitly named metadata fields. No full gate object or original request is serialized into the audit table. The projector explicitly selects fields, including nested assessment fields, so future upstream content fields cannot silently expand retention.

Retained fields are patient and server-resolved actor identity/role; draft/claim identities; gate policy and decision/reasons; request/verification digests; accepted current/history snapshot references; 4A findings/ruleset; 4C/4D/4E and renderer policy versions; separate grounding/evidence integrity/applicability assessments; source/unit/edition/version/hash, rule/intended-use and manifest references; and checks performed/not applicable. Versions absent from an upstream failed verification are not fabricated. No drafting-provider identity is invented: these contracts do not supply one, and extraction-provider provenance is not drafting provenance.

Excluded content includes rendered record text, proposed fact values, descriptions, unverified draft prose, prompts, source quotes, conversation transcripts, tokens, passwords, credentials and raw exception details. Existing necessary clinical/extraction storage is unchanged. Identifier/hash metadata remains sensitive and access-controlled; hashing is not de-identification or cryptographic attestation.

An audit of PASS means only that 4F assessed internal record-restatement eligibility. It does not assert medical correctness, clinical support, treatment safety or HTTP release permission. REFUSE records technical verification refusal, not a clinical hazard finding. WARN remains reserved and is rejected by the audit decision type/database constraint. Every payload has `release_status=NOT_ASSERTED`, `http_release_authorized=false` and `unrestricted_text_verified=false`. No consumption/delivery success or failure is inferred.

`AuditEvent` is an immutable envelope containing event/operation IDs, payload hash, metadata and aware evaluation/recording timestamps. Its existence in a returned object does not prove transaction commit. `evaluated_at`, database `clock_timestamp()` recording time and application-generated UUID event identity are separate from the deterministic metadata hash. The hash includes patient/actor scope and all projected decision metadata, but excludes event/operation IDs and occurrence timestamps. Equal payloads can legitimately belong to separate evaluations.

## Database migration

`0006_verification_audit` depends on `0005_supervised_extraction`. Upgrade creates only `verification_audit_events`, its index and append-only function/triggers. It does not alter/backfill historical patient, consent, clinical or extraction rows. Prior transient verification runs are not retroactively represented as historical audit events.

The table has a UUID primary key; patient FK and composite actor-ID/role FK with deletion restricted; operation/event identity; schema and gate decision; metadata-only JSONB payload and SHA-256 digest; and separate evaluation/recording timestamps. Unique `(patient_id, actor_id, operation_id, event_kind)` enforces scoped operation idempotency. Checks restrict actor role, schema/event kind, allowed gate outcomes, digest syntax, JSON object type and agreement between envelope columns and payload scope/decision/release flags. An index on patient, actor, recording time and ID supports bounded deterministic ordering.

PostgreSQL row triggers reject UPDATE and DELETE, including no-op updates. A statement trigger rejects TRUNCATE, including an empty table. Explicit operator-controlled downgrade drops the new table/function; it is never invoked by application services. No migration was applied to development PostgreSQL in this environment.

Nested JSON fact/evidence references are validated by the strict projection and independently generated patient-specific gate result; they are not individual relational FKs. Database constraints enforce the event envelope's patient/actor identity, while service authorization is the access boundary. Privileged database owners can disable/drop protections or fabricate inserts; this is not a tamper-proof ledger or an authorization policy implemented entirely in SQL.

## Evaluation writer and transaction contract

`record_patient_gate_evaluation(db, account_id, patient_id, original_request, operation_id)` calls the existing `evaluate_patient_gate` itself. It never accepts a caller-supplied gate result, audit payload, actor-role flag, approval flag or precomputed decision. 4F freezes the complete original request, and 4E/4A independently load current accepted state, enforce current eligibility/consent and acquire existing patient locks. Pending/rejected proposals and private transcripts do not become accepted context through auditing.

The writer resolves the current account/role from the database, projects metadata, checks scoped operation reuse, rechecks patient authorization, inserts and explicitly flushes. It performs no commit, rollback or consumer invocation. Projection, insertion, flush or stored-history validation failure returns a sanitized failure and requires caller rollback; database flush errors leave the session failed until rollback. There is no fallback that returns an unaudited success. Existing inherited lock helpers retain their established timeout/error behavior.

Caller responsibilities:

1. Begin with a clean session, with no unrelated pending changes.
2. Await the writer and treat its receipt as transaction-local.
3. Explicitly commit to establish durability, or roll back on every failure.
4. Retain authorization locks through any authorized internal metadata use, or reacquire authorization with the audit reader after dropping the transaction.

Do not call the writer inside the synchronous 4E/4F consumption callback. Do not treat flush as durability, commit a failed operation, silently release clinical content, or use an old receipt/PASS as authorization. Existing guards have no hidden commit added. A full new gate evaluation must precede audit insertion: reusing 4A/4E's clean-session loader with unflushed audit rows is intentionally rejected.

## Retry and concurrency semantics

Operation IDs belong to trusted orchestration; they are not proof of identity or authorization. Every retry performs a fresh authorized gate evaluation. Identical projected metadata/hash for the same patient/actor/operation/kind returns the original event, preserving its original ID/timestamps without UPDATE. Conflicting content, changed accepted snapshot or changed decision under that operation ID returns a sanitized 409 and requires rollback. A genuinely new evaluation uses a new operation ID. Different actors/patients have separate namespaces.

The inherited patient lock serializes concurrent retries and consent/correction mutations before the unique-constraint check and insert. The unique constraint is a second database defense; an insertion race outside the approved locking path fails closed, rather than doing an append-only-breaking upsert. If revocation commits first, subsequent audit writes/reads are denied. If an audit operation holds the gate first, revocation/corrections wait until its transaction ends. Historical events remain unchanged after corrections or revocation.

No patient-linked row is inserted for unauthorized requests. Existing 4E/4F authentication, authorization, validation, context and catalogue errors propagate as sanitized boundary failures. Auditing unauthorized attempts would require a separately reviewed security-event design; no patient existence or supplied foreign identifiers are asserted as authorized audit provenance.

## Actor-only internal retrieval

`get_patient_audit_event` and `list_patient_audit_events` require a clean session, use the existing patient-lock/current-authorization helper, and filter by both authorized patient and current actor in SQL. Account role alone, historical grants, cached JWT claims and knowledge of an event ID confer no access. Patients see only their own evaluations; consented practitioners see only their own evaluations for that patient. Shared-record consent never grants another participant's draft/transcript metadata.

Stored payloads are revalidated as strict metadata, checked against envelope columns and rehashed before return. Corrupt or unknown content is withheld with a fixed error, not echoed in validation output. Missing/foreign event IDs within an authorized patient scope return the same 404. Cross-patient/ineligible/revoked access is denied before event lookup. Current authorization is checked again before return, and caller retains locks through consumption. The list returns up to 50 recent events by default, bounded to 1–100, ordered by recording time and UUID; no export or pagination feature is introduced.

Historical reads authorize current access but do not reinterpret past results using today's catalogue or current patient values. Revocation denies future practitioner reads without deleting history. These are internal services only; no HTTP release boundary is added.

## Release and reproducibility limits

This milestone does not retrofit the existing clinical response's commit-after-ASGI-send behavior, and does not establish a durable-before-release protocol. A future separately approved release increment must coordinate durable release intent, current authorization/snapshot/catalogue checks and protected handoff without claiming that an ASGI send proves human receipt. Existing 4E/4F guards remain unchanged and synchronous. No clinical content is returned by the audit writer/reader, and no clinical-output consumer is invoked here.

Metadata preserves deterministic decision identity, stated reasons and reference provenance. It cannot reconstruct/replay arbitrary rejected draft content or prompts from hashes alone. There is no raw draft retention, durable drafting-provider registry, cryptographic signature, security-attempt stream, shared audit-view policy or backfill of previously transient events. Complete-history loading, coarse locks and frozen-catalogue deployment/withdrawal limitations remain inherited research constraints.

## Validation

Using the existing backend virtual environment:

```text
.venv/Scripts/python.exe -m pytest tests/test_verification_audit.py tests/test_identity_models.py -q
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m mypy app/models/verification_audit.py app/schemas/verification_audit.py app/services/verification_audit.py --follow-imports=silent
```

Full suite: 551 collected; 377 passed, 0 failed, 174 skipped (7.95 seconds). New audit coverage: 42 passed, 0 failed, 20 PostgreSQL integration cases skipped. Offline migration/ORM metadata agreement passes. TEST_DATABASE_URL is absent, so no PostgreSQL integration or migration application was performed and no development URL was substituted.

The shared test fixture now requires exactly `postgresql+asyncpg` targeting `clincoherence_db_test` and verifies `current_database()` before creating its random isolated schema. The existing dedicated-schema cleanup behavior is retained. Tests cover projection minimization, strict/frozen metadata, digest identity, all gate outcomes, no release claims, authorization failures, retry/conflict behavior and flush-failure handling. Dedicated database cases cover durability/rollback, actor isolation/current consent, FK/check/unique constraints, UPDATE/DELETE/TRUNCATE protection, unchanged fact/confirmation history, concurrent retries/corrections and both write/read revocation race orders. These database cases still require validation in the user's configured PowerShell environment.

Ruff, formatting and targeted mypy passed for the affected Python files. No dependencies were installed. Only the six approved additions and the two approved registration/test-fixture files are changed; no staging, commit or push was performed.
