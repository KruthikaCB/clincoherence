# Milestone 5B — authenticated verified-record API bridge

## Scope and wire contract

Adds only `POST /api/v1/patients/{patient_id}/verified-records`. This is a narrow
accepted-record restatement bridge, not a general AI chat or medical advice API.
The body is an immutable, extra-forbidden `VerifiedRecordsRequest`:

```text
{
  "operation_id": "<client-generated UUID>",
  "request": <original EvidenceAwareDraftRequest>
}
```

The original request retains the existing `draft` (draft/patient/claim identities,
accepted fact-version references, structured proposed content and attribution)
and optional explicit synthetic evidence `bindings`. All existing nested validation
applies. Unknown envelope/nested fields are rejected, including client-supplied
PASS flags, verified text, digests, audit IDs and release-intent IDs. Original
claims and attribution are untrusted assertions, independently verified by 4D–4H;
they never establish actor identity or release authority. A valid operation UUID
is an idempotency reference, not authorization.

Success is the existing exact-byte `record-response-json-v1` JSON representation:
`representation_version` and `records`, each containing `claim_id` and server-rendered
`text`. The response has `Cache-Control: no-store`, `Referrer-Policy: no-referrer`
and the exact encoded content length. No gate/audit/intent object is returned as
a release permit. PASS means internal eligibility, not clinical correctness;
REFUSE is technical verification refusal, not detection of a medical hazard.
Synthetic evidence resolution does not establish medical support.

Authentication failures use the existing generic 401 boundary; unauthorized patient
scope uses generic 403. Path versus request patient mismatch returns generic 409.
Malformed/extra fields use the existing sanitized 422 validation route, without
echoing submitted clinical content. The 4H adapter retains its generic withheld/error
responses (including 409 for withheld evaluations/replayed operations and 503 for
infrastructure/integrity failures). None of these responses includes protected
records or raw exception details. After response start, failures may terminate an
incomplete response instead of producing a second error response.

## Authorization and release sequence

1. Existing JWT `CurrentAccount` resolves active eligible accounts from PostgreSQL.
   Existing `patient_scope` checks the patient identity and current consent. No new
   authorization implementation or consent-in-token mechanism is introduced.
2. The path patient ID must equal the original draft's patient ID. Actor identity
   comes exclusively from the authenticated account. Claim scope and references
   remain subject to independent accepted-state validation.
3. The endpoint returns `AuditedRecordResponse` directly, with server-resolved actor
   and patient IDs, the original request, operation UUID and trusted
   `async_session_factory`. This is not the authentication request's session.
   The factory dependency is server configuration/test injection only, never request data.
4. Unchanged 4H transaction 1 independently evaluates the gate, persists metadata-only
   audit and PASS-only intent, and explicitly commits before protected sending.
5. Unchanged 4H transaction 2 uses a distinct fresh session and independently checks
   authorization/consent, accepted versions/provenance, catalogue, committed metadata
   and exact body digest. Patient protections remain held through bounded ASGI handoff.
   Revocation/correction committed in the gap withholds the body.

The route neither serializes a normal JSONResponse nor buffers clinical bytes.
The authentication session performs only existing account/scope reads; its dependency
cleanup is not the release durability boundary. There are no added commits, synchronous
guard persistence calls, modified 4E/4F contracts, tables, migrations or frontend changes.
Existing source-speaker confirmation and pending/rejected exclusions remain inherited.

An existing committed intent is never resumed: the same operation cannot replay its
clinical body. After uncertainty, a new deliberate request needs a new operation UUID
and full evaluation. Intent durability proves preparation, not send, delivery or reading.

## Validation

Using the existing backend virtual environment:

- Bridge and 4H tests: **74 passed, 0 failed, 24 skipped**.
- Full backend suite: **451 passed, 0 failed, 198 skipped** (649 collected).
- New bridge cases: **30 passed, 8 PostgreSQL cases skipped**.
- Ruff check and formatting check: passed for all four changed Python files.
- Targeted mypy: passed for the new schemas/router and router registration.

`TEST_DATABASE_URL` is absent from this process. No PostgreSQL integration validation
or migration application was attempted; the development database was not substituted.
The shared opt-in fixture requires `postgresql+asyncpg`, the exact database name
`clincoherence_db_test`, and verifies the server database before creating an isolated
test schema. New HTTP integration tests inject a factory for that schema's test engine.
Run `python -m pytest -q` from the configured backend virtual environment with the
dedicated test URL available to complete database validation.

Offline tests exercise actual FastAPI dispatch and the real 4H adapter with mocked
persistence: exact bytes, distinct sessions, commit ordering, sanitized validation,
missing/malformed/expired JWTs, scope mismatch, catalogue/failure withholding and
disconnect cancellation. PostgreSQL cases cover patient/practitioner access, visible
durable audit/intent at HTTP start, body digest, operation replay, revoked/foreign scope,
unsupported prose, and revocation/correction/catalogue changes before revalidation.
Existing 4H regression tests retain the deeper connection, cancellation, send failure,
lock and timeout coverage. Mocked tests do not prove PostgreSQL race safety.

## Limitations retained

- Only synthetic/de-identified accepted records; no real LLM, thresholds, interpretation,
  diagnosis, prescribing, evidence rules or treatment advice is added.
- Existing 4H disclosure/connection-loss detection races remain: database locks and
  network delivery cannot be atomic, and already disclosed bytes cannot be recalled.
  ASGI/driver cancellation must cooperate; cleanup can extend the normal five-second
  handoff bound. Disconnects and uncertain sends do not trigger automatic replay.
- Catalogue assets remain frozen per deployment, without hot updates or atomic
  cross-worker withdrawal. No middleware may silently transform protected body bytes.
- Metadata-only audit cannot exactly replay arbitrary rejected drafts. No release-outcome
  or confirmed-delivery event is added. Other clinical endpoints are not retrofitted
  into audited release by this milestone.
- No frontend integration, public audit access or transcript sharing is introduced.
