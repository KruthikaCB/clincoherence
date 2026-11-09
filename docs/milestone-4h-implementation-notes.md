# Milestone 4H secure audited response release foundation

Internal, non-streaming ASGI handoff of verified accepted-record restatements only. No production endpoint, frontend integration, middleware activation, real LLM, clinical threshold, diagnosis, prescribing, treatment advice or release-outcome persistence is added. Existing 4E/4F contracts, 4G audit schema, clinical-fact routes, approved requirements and architecture are unchanged. Synthetic/de-identified data restrictions and individual original-speaker confirmation remain inherited from the accepted-state verification pipeline.

## Intent and transient response contracts

Release policy is `audited-record-release-v1`; representation is `record-response-json-v1`. Frozen strict `ReleaseIntent` contains only intent/evaluation UUIDs, policy and representation versions, exact body SHA-256 digest and aware database recording time. It is lookup metadata, not a resumable release credential or delivery assertion. Event IDs and timestamps are separate from body identity. The parent 4G event retains its original metadata-only evaluation identity and `release_status=NOT_ASSERTED`.

`RecordResponse` is a transient frozen representation containing the representation version and the complete tuple of server-rendered claim-ID/text records. It is never stored. Encoding uses sorted JSON keys, compact separators, UTF-8, `ensure_ascii=False`, and disallows NaN. The digest is SHA-256 of those exact encoded bytes, not a hash of a separately reconstructed semantic object. The ASGI adapter sends exactly those bytes in one body message with `more_body=false`; content length is the encoded byte count.

No protected fields are taken from the base Response's body, headers, cookies, status, background task or FastAPI response-model encoding. The protected start message uses fixed status 200, JSON content type, no-store and no-referrer headers, plus the computed content length. All protected headers are delayed until pre-release commit and fresh verification succeed. Error responses contain a fixed generic message and no clinical records, draft text, patient/event identifiers or raw exception details.

The digest binds the adapter's ASGI response-body bytes. HTTP framing/TLS are transport operations, not additional verified content. The current application has no body-transforming middleware; future compression, transformation or body augmentation requires separate review and must not silently change this contract. A transport send does not prove client receipt, display or reading.

## Migration 0007

`0007_response_release_intent` depends on `0006_verification_audit`. It creates the one append-only `response_release_intents` table and its trigger functions. UUID identity, a unique evaluation-event FK with deletion restricted, constrained release/representation versions, constrained SHA-256 digest and database `clock_timestamp()` recording time are the only columns. The unique parent reference permits at most one intent per scoped 4G evaluation/operation. Patient and actor scope are inherited through that immutable parent, avoiding duplicate scope columns.

An INSERT trigger requires the parent gate decision to be PASS. Parent evaluation and intent are initially staged in the same transaction and become committed atomically. UPDATE/DELETE row triggers and a TRUNCATE statement trigger reject ordinary mutation, including no-op updates and empty-table truncation. Downgrade is explicit operator-controlled removal, never application behavior. No existing audit event or other historical row is rewritten/backfilled. Privileged database owners can still disable/drop protections; the ledger is not cryptographically tamper-proof.

No clinical response body, raw prose, prompts, transcripts, credentials or fact values are persisted in the new table. Existing audit digests and provenance remain sensitive metadata and are not de-identification guarantees.

## Internal entry point and transaction ownership

`AuditedRecordResponse(actor_id, patient_id, original_request, operation_id, session_factory, handoff_timeout=5.0)` is an internal Response subclass, not a registered route. Its identity/session factory arguments belong to trusted authenticated orchestration. Constructor validation freezes the complete original request before any asynchronous work; validation is not authentication. A future endpoint must authenticate and use the existing sanitized validation boundaries. Provider-supplied verification results, receipts, ownership flags, arbitrary body bytes and authorization flags are not accepted.

The factory must create dedicated sessions. The adapter rejects inactive/dirty sessions and pre-existing transactions, and rejects reuse of the first session object as the second phase. It never commits an unrelated request-session transaction or depends on get_db cleanup for durability. Existing guards receive no hidden commit. Helper functions and the private `_Prepared` value are internal orchestration components, not independent externally callable release capabilities.

### Transaction 1: durable preparation

1. Open a fresh dedicated session and independently evaluate the frozen request through 4F. Existing 4A/4E authorization, consent and accepted-state locks remain held.
2. Call 4G's independent evaluation writer, preserving its strict metadata-only projection. Compare its metadata with the first gate evaluation; evaluation clocks are deliberately excluded from that comparison.
3. For CLARIFY/REFUSE, stage only the evaluation. For PASS, check there is no prior intent for that evaluation, encode the exact representation, and stage a newly created intent.
4. Explicitly commit the evaluation and intent atomically before any protected start/body message. A known disconnect before commit rolls back preparation. Any insertion/flush/commit exception or cancellation prevents handoff and rolls back where possible.
5. Only this invocation's newly created intent and successful commit acknowledgement permit proceeding. A commit failure with an uncertain actual database outcome still prevents sending.

CLARIFY/REFUSE evaluations commit without an intent and produce only a generic withheld response. No accepted records or provider prose are exposed. Audit evaluation is not response authorization or a delivery event.

### Transaction 2: fresh validation and protected handoff

1. Open a distinct fresh dedicated session/transaction. Reevaluate the same frozen request using 4F and reacquire patient/account/practitioner protections in the existing patient-first order.
2. Look up the intent and parent evaluation under current patient authorization and actor-only filters. Require the stored intent to match the newly prepared receipt.
3. Require fresh PASS and exact equality of the fresh 4G metadata projection with the committed evaluation. This binds original request, draft/claims, accepted current/history versions, attribution/provenance checks, policies, evidence/manifest references and catalogue fingerprint. Corrections are never silently rebound.
4. Re-encode the body and compare its exact digest with the intent. Recheck authorization and trusted catalogue identity.
5. Establish the live PostgreSQL backend PID and transaction ID. Check the session/connection remains active, valid and in that same transaction before each protected message. Recheck the catalogue before each message.
6. During a pending send, a 25 ms monitor checks transaction identity and known disconnect/catalogue failure. A per-session asyncio lock serializes those database checks, avoiding concurrent AsyncSession operations.
7. Retain patient locks until owned sending/check tasks have finished or completed cancellation cleanup. Explicitly roll back the read-only transaction afterward. There are no post-send audit writes or commits.

No async persistence or awaited ASGI operation is placed inside the synchronous 4E/4F protected-consumption callback. The new adapter is an async release boundary reusing their evaluators and protections, not an expansion of the existing consumer contract.

## One-shot attempts, lookup and failures

The response instance marks itself used before its first await. Another invocation of that object fails. A separate instance with an existing intent also fails: there is no resume/replay branch, including after a crash, disconnect or uncertain commit/send. Conflicting operation-ID reuse fails through the existing 4G comparison. Concurrent duplicate operations are serialized by the patient gate and constrained by unique evaluation/intent references; only the fresh committed creator may proceed. Explicit retries after uncertainty need a new operation and fresh evaluation. No exactly-once delivery guarantee is made.

`get_release_intent` is an internal, actor-only metadata reader. It requires a clean session and current patient authorization, filters through the evaluation parent by both patient and actor, validates version/digest types, and rechecks authorization before return. Unknown/other-actor IDs use the same 404. Revocation denies future reads without deleting history. Lookup cannot authorize or resume handoff; caller must retain locks through authorized metadata use.

Pre-start failures produce only sanitized generic status handling. Once a protected response start may have escaped, send failure, timeout, catalogue change, connection failure or disconnect aborts remaining sends where possible; no second response or automatic retry is attempted. Partial disclosure cannot be recalled. Successful send return is not persisted or reported as confirmed delivery. Intent-only history means preparation occurred, not that any bytes were sent.

Protected-handoff timeout is trusted constructor configuration, defaults to five seconds and can be reduced to any finite positive value up to five seconds. It bounds the normal send attempt. Cleanup deliberately joins owned sending/monitor/receive tasks before releasing protections, using shielded joining to tolerate repeated cancellation. A noncooperative ASGI/driver cleanup can extend lock holding; detached sends are not an acceptable timeout fallback. Supported ASGI send/receive implementations must cooperate with cancellation.

## Security and remaining limitations

Revocation or correction committed in the preparation/handoff transaction gap causes fresh authorization or snapshot comparison to withhold the body. During an intact handoff transaction, cooperating revocation/correction services wait for the patient lock to be released. The adapter does not preserve old PASS as permission and leaves the existing gate's `http_release_authorized=false` unchanged.

Known session inactivity, closed/invalidated connections, transaction disappearance/replacement, query failure and disconnect are checked and abort remaining send work where possible. Sudden connection loss may release database locks before the application detects it; monitoring cannot make database state and network disclosure atomic. There remains a race between the last check, failure detection and transport progress. Already disclosed bytes cannot be retracted.

Keep the deployment-frozen catalogue policy: no hot approval updates; reviewed versions/pins require controlled deployment and draining active work. There is no atomic cross-worker/deployment withdrawal guarantee. Metadata-only audit cannot exactly replay arbitrary rejected drafts. No release-outcome event, confirmed delivery, durable draft text or shared transcript/audit access is implemented.

This increment does not retrofit existing clinical-fact endpoints or claim all application responses are audited. No frontend/prod endpoint is exposed. Future integration must use this boundary as designed, must not accept private prepared receipts or bypass it with cached PASS/body data, and must review any response-transforming middleware.

## Validation in this environment

Resumed the existing working tree at `e26a599748932db6c2ee945982aa7b186a8e7d16`; existing implementation was preserved. Missing work included these notes, cancellation-cleanup validation, additional failure/retry tests and dedicated-session checks. Only the nine approved files changed.

Using the existing backend virtual environment:

```text
.venv/Scripts/python.exe -m pytest tests/test_response_release.py tests/test_identity_models.py -q
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m mypy app/models/response_release.py app/schemas/response_release.py app/services/response_release.py app/api/audited_response.py --follow-imports=silent
```

Full suite: 611 collected; 421 passed, 0 failed, 190 skipped (13.48 seconds). New 4H coverage: 44 passed and 16 PostgreSQL cases skipped. Offline migration/ORM metadata agreement passes. TEST_DATABASE_URL is absent; PostgreSQL validation/migration application was not attempted and no development URL was substituted. Existing dedicated test guards require `clincoherence_db_test` and verify the server database before schema setup.

Unit/ASGI tests cover exact bytes/digests, protected commit ordering, commit/preparation/revalidation failures, no inherited Response-body/header bypass, withheld gates, mutable original requests, session freshness, repeated cancellation, timeout/disconnect/send failures, known connection failure/replacement, catalogue changes and one-shot instances. Dedicated PostgreSQL cases cover atomic preparation/rollback, PASS-only parents/uniqueness, append-only protections, actor lookup/revocation, independently observed durability before headers, transaction-gap changes, concurrent duplicate requests and revocation/correction blocking during handoff. Those PostgreSQL cases remain unvalidated here and require the user's configured test session.

Ruff, formatting checks and targeted mypy pass for affected files. No dependencies, services or system configuration were installed/changed. No staging, commit or push was performed.
