# Milestone 5F-B — Read-only patient-specific Clinical History

This increment extends the accepted-facts section in the existing authenticated
dashboard. It introduces no page, backend API, migration, correction write control,
medical interpretation or dataset integration. Approved requirements and
architecture remain unchanged. Synthetic/de-identified prototype data only.

## API and accepted-state boundary

The workspace still loads `GET /api/v1/patients/{patient_id}/facts` alongside the
speaker's existing confirmations. Complete accepted fact responses are validated
before either protected view is populated. Only `CONFIRMED` records are accepted;
pending/rejected extraction proposals remain separate in the conversation.

Clinical History supports the existing diagnosis, medication, laboratory, symptom
and history payloads. Validators check identifiers, patient scope, attribution,
version metadata, timezone-aware dates, provenance, entry method and confirmation
metadata, as well as supported payload fields/codes/units. Measurement strings
remain strings, including trailing zeroes and scientific notation. Decimal
coefficient/scale validation follows structural precision limits; no measurement
is parsed as a JavaScript floating-point value. There are no clinical thresholds,
unit conversions, correctness claims or treatment recommendations.
Text limits count Unicode code points, matching backend validation rather than
rejecting valid supplementary characters because of UTF-16 code-unit length.

`View details/history` explicitly requests
`GET /api/v1/patients/{patient_id}/facts/{fact_id}/history`, using the fact root,
not its version ID. History is read-only, authorized by the existing backend,
and checked for matching patient/root, accepted status, consistent attribution,
unique versions, ascending version order and linked predecessors. Version numbers
may skip; observation times may be retrospective. Neither is reordered or inferred.
Version-one root identity remains `fact_id == version_id`.

All returned versions are validated before any history detail renders. The
selected version must be present and structurally identical to the current
accepted snapshot. A newer linked history triggers one fresh workspace load,
withholding history and unmounting the previous Verified Records view. The user
must explicitly open history again afterward; there is no automatic history retry
loop. Separate requests do not constitute an atomic server snapshot. Further
concurrent changes can require another explicit refresh.

## Presentation

Current accepted cards remain distinct from speaker-specific confirmation cards
and technically verified server-released restatements. History displays every
accepted version in server order. Earlier versions are labelled `Superseded`,
not rejected or deleted. Details include patient/root/version/predecessor IDs,
observation and entry timestamps, author role/account identifier, source and
reference, data-origin declaration, entry method, correction reason, and original
confirmation identifiers/times where applicable. Structured entries are not
mislabelled as lacking acceptance merely because no extraction confirmation exists.

Provenance references are escaped plain text, never links or instructions to fetch
private messages. Shared-record consent does not grant another speaker's transcript
or confirmation access. No names, identities or emails are inferred from UUIDs.
Provenance declarations do not prove de-identification, approved evidence or
medical correctness. No correction or deletion action is offered.

## Authorization, cancellation and stale responses

The backend remains the sole access authority. Patients use their own discovered
profile; practitioners use server-discovered patient context with current consent.
The existing in-memory JWT and no-store/no-referrer/credentials-omit transport is
reused. No records, tokens or requests enter persistent browser storage, URLs,
analytics or logs.

Each history request has an owned operation guard and abort signal linked both
to component lifetime and the workspace's synchronously invalidated context
signal. Patient switching, sign-out, consent-related invalidation and authorization
failure clear/unmount protected history. Parent operations show a loading panel
instead of retaining an earlier detail snapshot. Late responses cannot restore a
cleared or different patient's content. Duplicate history clicks are disabled and
guarded synchronously. Responses are bound to the accepted array used to open them.

401/403/404 propagate through the existing workspace denial handler, clearing
accepted facts, proposals and verified content. Other invalid/error responses
withhold details and show a generic refresh instruction without echoing bodies.
No mutation retry or additional backend permission mechanism is introduced.
Remote revocation is enforced by subsequent server operations; it cannot recall
information already legitimately displayed. No push revocation channel is added.

## Tests and scope

The three existing confirmation/verified-record/onboarding test fixtures are
corrected so original root IDs equal version IDs. Backend constraints are unchanged.
New mocked browser tests cover all supported fact types, provenance and full
metadata, skipped version numbers, superseded history, retrospective observations,
exact decimal representations, malformed/cross-patient responses, authorization
errors, empty/loading states, stale-history refresh and switching/sign-out races.
Existing confirmation, onboarding and server-only Verified Records tests remain
regression coverage. No guarded live run or PostgreSQL operations belong to this
validation increment.

Validation: the initial focused run passed 39 tests; the final full mocked suite
passed **116 tests, 0 failed, 0 skipped**, including all **40 Clinical History**
cases and the existing 76 regression cases. The additional case verifies Unicode
text limits. ESLint, TypeScript (`--noEmit --incremental false`) and the production
build passed. No backend tests, PostgreSQL operations or guarded live suite ran.
Mocked browser results do not prove live database authorization, revocation
ordering or response-release behavior; genuine browser-to-FastAPI validation of
this UI remains for a separately approved integration increment.

## Remaining boundaries

- Lists/history remain unpaginated, matching the local research backend.
- Current and history reads are independently authorized; no cross-request atomic
  snapshot or automatic background synchronization is claimed.
- Clinical correctness, prescribing, medication changes, real LLMs and clinical
  evidence policy remain outside this increment.
- Structured correction controls and any change to same-category practitioner
  correction permissions require a separately reviewed increment.
