# Milestone 5E-B — Guarded live integration harness

Status: implementation and non-destructive validation only. Live execution requires
separate explicit approval and an explicitly configured dedicated test URL.

## Boundary and entry points

Six new test/documentation files only. No production endpoint, application model,
migration, authentication override, evidence rule or dataset change. The normal
mocked Playwright suite remains separate from `frontend/live-tests`.

From `backend`, after separate approval, the sole supported launcher is:

```powershell
.\.venv\Scripts\python.exe -B -m tests.live_support run --approve-live
```

Do not execute this command before approval. The flag acknowledges approval; it
is not a substitute for obtaining it. No bare Alembic or ordinary development
server command belongs in this workflow.

## Milestone 5E-D — Cleanup observability

The user reported a first successful browser run of the committed 5E-B harness.
That version printed browser success before its finalizer; the message alone did
not establish schema cleanup. The changes below apply to future runs. They do not
retroactively verify the earlier run, and no live run or database connection was
performed while implementing 5E-D.

Each guarded run prints `Live run: run_id=<32 hex> schema=cc_live_<32 hex>` before
setup. These are non-secret infrastructure identifiers, not patient identifiers.
Retain that line for an optional later exact-target inspection. Credentials,
ownership nonce, signing keys, passwords and clinical data are never included.
No receipt file or database audit table is added.

Successful completion now emits four distinct phases, in this order:

```text
Browser suite: PASS
Process shutdown: PASS
Schema cleanup: PASS (drop commit acknowledged)
Overall launcher: PASS [run_id=... schema=...]
```

Only the final line is overall success. It is reached after Playwright returns
zero, every registered root/job has confirmed termination, and the owned-schema
drop transaction has committed and its connection/engine cleanup has returned.
Capture the native exit status immediately afterward in the same PowerShell:

```powershell
$liveRunExitCode = $LASTEXITCODE
Write-Output $liveRunExitCode
```

Do not infer the original run's exit code after executing unrelated native
commands. The full run and its exit status are separate from browser assertions.

Failure semantics:

| Situation | Reporting and exit |
|---|---|
| Browser test fails; shutdown/cleanup succeed | Browser FAIL, cleanup phases PASS, overall FAIL `[BROWSER_FAILED]`; nonzero |
| Process termination or registration uncertain | Process FAIL, schema cleanup SKIPPED, overall FAIL `[PROCESS_SHUTDOWN_UNCERTAIN]`; nonzero, no schema deletion |
| Schema validation/drop/commit/disposal fails or times out | Schema FAIL, outcome unconfirmed, overall FAIL `[SCHEMA_CLEANUP_FAILED]`; nonzero |
| Other guard/startup/execution failure | Overall FAIL `[RUN_FAILED]`, or `[RUN_TIMEOUT]`; nonzero |
| Interrupted execution | No overall PASS; nonzero. Shutdown and cleanup guards still apply; fixed phase-specific categories take precedence if they fail |

Errors are reported using fixed categories, never raw driver or subprocess
exceptions. A failed/uncertain commit is not reported as successful cleanup or as
proof the schema still exists. Safe shutdown is always attempted first. No hidden
retry or deletion outside the exact owned schema is added. The private `observe`
command used by browser tests retains its JSON-only successful output.

### Exact-target read-only inspection

After an authorized run, use the identifier printed by that run and an explicitly
configured `TEST_DATABASE_URL` in the same PowerShell. This inspection command
does not require `--approve-live`: it starts no services, executes no live tests,
and performs no setup or cleanup. Running it still opens a database connection;
it was not executed during implementation.

From `backend`, choose **one** of these equivalent commands and enter the exact
non-secret identifier recorded by the launcher:

```powershell
$recordedRunId = Read-Host 'Recorded 32-hex run ID'
.\.venv\Scripts\python.exe -B -m tests.live_support inspect-schema --run-id $recordedRunId

# Alternative: use the recorded full schema name instead.
$recordedSchema = Read-Host 'Recorded cc_live_ schema name'
.\.venv\Scripts\python.exe -B -m tests.live_support inspect-schema --schema $recordedSchema
```

Missing, invalid, wildcard, public/development schema names and multiple targets
are rejected before connecting. There is no target fallback from environment
variables and no `DATABASE_URL` credential fallback. The existing strict test URL
driver/host/port/database validation applies. The connected server's database is
independently verified before the catalog lookup.

The inspector uses startup `default_transaction_read_only=on`, explicitly sets
its transaction read-only, restricts search path to `pg_catalog`, and executes a
parameterized exact-name `EXISTS` lookup. No schema lists, ownership comments,
clinical tables, prompts, transcripts or fact values are queried or returned.
The existing query/lock/operation deadlines also apply. Output contains only:

```text
{"schema": "cc_live_<recorded run ID>", "status": "ABSENT", "read_only": true}
```

`ABSENT` returns exit zero. `PRESENT` returns nonzero and grants no ownership,
authorization to delete, or proof that a process is still running. Invalid targets
and configuration return a sanitized `[INSPECTION_REJECTED]` failure; connection,
query and timeout failures return a sanitized inspection failure without printing
the supplied target, connection string or exception. Inspection never emits an
overall launcher PASS.

Absence is a point-in-time statement about the exact supplied name only. It cannot
prove that a schema was previously created, that it belonged to the claimed run,
that a browser received/read content, or that all child processes have stopped.
Use it together with the recorded run metadata and final launcher exit status;
never guess a target or enumerate other runs. A privileged concurrent database
owner can recreate objects or bypass protections; existing limitations remain.

Configure `TEST_DATABASE_URL` in the same PowerShell process without displaying it
or placing credentials in shell history:

```powershell
$liveTestSecret = Read-Host 'Dedicated test connection URL' -AsSecureString
$env:TEST_DATABASE_URL = [Net.NetworkCredential]::new('', $liveTestSecret).Password
Remove-Variable liveTestSecret
```

Expected URL shape (placeholders only):
`postgresql+asyncpg://<test-user>:<URL-encoded-password>@localhost:5432/clincoherence_db_test`.
Use an existing local test account with schema-creation/migration permissions.
Never enter credentials into chat, committed files or browser settings.
PostgreSQL must already be available. This launcher never starts Windows services,
installs dependencies or creates databases.

## Database isolation

`TEST_DATABASE_URL` is mandatory, with exact asyncpg driver, exact test database,
explicit port 5432, loopback host, credentials and no query/fragment overrides.
There is no dotenv or `DATABASE_URL` fallback. URL errors are redacted.

The launcher independently checks `current_database()` before DDL, creates a
random `cc_live_<32 hex>` schema without adopting existing schemas, and commits
a random ownership nonce as its PostgreSQL schema comment. Ownership checks also
require that the connected role owns the schema. Existing migrations 0001–0007
run through Alembic Operations on that schema-bound connection. No `public`
tables are reset, deleted or truncated. Schema creation is infrastructure only;
no additional application tables or migrations are introduced.
Cleanup checks the dependency graph and refuses a cascading drop if logical
objects in another schema depend on this run; internal PostgreSQL objects are
treated separately. Privileged concurrent DDL remains outside this guarantee.

The parent overrides its process URL before importing fixture models. Children
receive an explicit test `DATABASE_URL`, ephemeral signing key, HS256, mock LLM,
local invitation origin and isolated run metadata. The test-only ASGI factory
checks effective settings, engine URL and the actual request/release session
factory bindings. The production `get_db` and 5B dedicated release factory both
retain their existing behavior and use the same guarded engine.

Every new connection receives only the private schema as `search_path`. Every
checkout independently validates database, schema, exact search path, ownership
nonce and schema owner. Checkout verification rolls back its own probe before
the application transaction begins. Lifespan independently verifies the target
before serving. Missing/wrong configuration or isolation fails closed.

All harness and application connections have a five-second connection timeout,
15-second asyncpg command timeout, PostgreSQL `statement_timeout=15000` and
`lock_timeout=5000`. Setup, seed, observation and cleanup additionally have a
60-second cooperative asyncio operation deadline covering disposal. Cancellation
must unwind the operation before the launcher proceeds. These bounds cannot
guarantee a hard wall-clock limit for a stalled native operation or cancellation
cleanup that does not cooperate. A timed-out commit can have an uncertain outcome:
only independently verified owned-schema cleanup is attempted, never a broad
reset or retry against another database. A cleanup timeout reports failure; it
does not prove whether the owned-schema drop committed.

## Process ownership and shutdown

Fixed loopback ports: FastAPI 8010, Next.js 3010. Occupied ports are rejected,
including a second check immediately before startup. No server is reused.
The launcher starts Uvicorn without reload/access logs and Next.js through its
local Node entry point; child stdout/stderr are discarded, not saved.

This implementation targets Windows. Each child starts suspended, is assigned
to a new Windows Job Object, and resumes only after assignment. The job prevents
descendants escaping through early parent exit; kill-on-close protects against
launcher termination. Shutdown terminates the owned job and verifies zero active
members before schema cleanup. Unknown ownership or shutdown failure retains
the schema and reports failure instead of dropping it. It never kills an existing
user server by port or arbitrary process name.

Lifecycle correction: the parent registry reserves an ownership entry **before**
native creation, and the created handle is stored there before assignment or
resumption. Caller-side registration after `spawn` is no longer used. A sticky
registration-incomplete flag prevents interrupted reservations from looking like
an empty successful registry. Creation, assignment, resumption and teardown
account for `BaseException`, including KeyboardInterrupt and SystemExit.

Teardown separately confirms job descendants and the known root process (which
may still be suspended outside the job after failed assignment). Job shutdown is
idempotent; retained handles allow retry after partial failure. Registry shutdown
attempts the remaining owned entries even if one teardown is interrupted. Schema
cleanup is reached only after all entries are positively confirmed stopped and
registration is complete. If Popen is interrupted before its handle is captured,
the pending creation is treated as unresolved even if its job is empty. No PID is
guessed; schema cleanup is suppressed. The launcher reports the random schema
name, without ownership nonce, credentials or clinical data, for safe follow-up.

Readiness requires a run-specific header returned through a pure ASGI wrapper,
first directly and then through Next.js. The wrapper forwards `receive` and
awaits each original `send` without buffering clinical bodies, so it does not
replace the 4H protected response boundary. It adds no production route.

Next.js receives a minimal OS environment and backend origin, not DB credentials,
fixture passwords or JWT signing keys. Playwright and its private observer receive
run credentials only in process memory. No new `NEXT_PUBLIC_*` secrets exist.

## Synthetic fixtures and browser coverage

Test-only ORM setup creates two active, eligible practitioners with unique
`@synthetic.invalid` identities, hashed ephemeral passwords and no patient consent.
Patients are invited and activated through the actual UI/API, not seeded as
already-consented records. All subsequent authentication is ordinary login.

One serial browser journey uses separate, unsaved contexts for practitioner,
two patients and an unrelated practitioner. It covers:

- Practitioner and patient profile discovery with distinct profile IDs.
- Invitation link/code separation, actual inspection and explicit activation.
- No consent after activation; explicit consent grants and patient discovery.
- Real deterministic extraction, individual confirm/reject, pending/rejected
  exclusion, and no sharing of another speaker's confirmation cards.
- Actual verified-record release, exact received-byte digest comparison with
  committed audit/release-intent metadata, and duplicate-operation denial.
- Unauthorized/cross-patient denial, consent revocation and subsequent denial
  using the original authenticated session.
- Patient switching while an actual authorized facts response is delayed,
  stale-response suppression, sign-out clearing and no persistent browser state.

Post-revocation facts and verified-record denials both require HTTP 403, the exact
generic `{"detail":"Patient scope denied"}` shape, and absence of protected record
text, laboratory values, codes, units and observation date markers. Boolean
assertions avoid including a regressed response body in diagnostics.

The delay forwards the real upstream response without manufacturing data. It
tests frontend context isolation, not a new proof of transaction-race behavior.
Existing backend concurrency tests remain required for precise revocation,
correction, commit-before-send, disconnect and release timing guarantees.

The private `observe` subcommand opens a read-only transaction in the owned
schema and returns counts/booleans only. It checks the operation's committed PASS
evaluation, one intent and exact body digest. It never returns actor/patient IDs,
raw audit payloads, fact values, response prose or authentication material.
An intent is not proof of sending, delivery, reading or medical correctness.

## Browser privacy and execution policy

One worker, zero retries, no Playwright-managed server or existing-server reuse,
no saved storage state, screenshots, traces or video. DOM error snapshots are
disabled using the installed Playwright `PLAYWRIGHT_NO_COPY_PROMPT` capability;
the launcher checks for that capability before setup. Journey errors are redacted
before reporting, reporters cannot persist protected diagnostic text, and output
preservation is disabled. No prompts, tokens, invitation codes or response bodies
are printed or attached. Next telemetry is disabled. Any future Playwright upgrade
must recheck these safeguards.

No automatic retry of mutations or uncertain verified-record operations is
introduced. The only deliberate replay is a negative test using the original
operation ID to prove that protected content is withheld.

## Non-destructive validation

From `backend`:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/test_live_support.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check tests/live_support.py tests/live_app.py tests/test_live_support.py
.\.venv\Scripts\python.exe -m ruff format --check tests/live_support.py tests/live_app.py tests/test_live_support.py
.\.venv\Scripts\python.exe -m mypy --follow-imports=silent tests/live_support.py tests/live_app.py
```

From `frontend`:

```powershell
npm.cmd run lint
.\node_modules\.bin\tsc.cmd --noEmit --incremental false
```

Safety tests use mocks only: no sockets/services/database setup, migrations or
accounts are created. Windows asyncio itself requires an internal local socket
pair; a restrictive sandbox may block that despite all database operations being
mocked. Running these same safety tests outside that sandbox is not live execution.

Live status remains BLOCKED until explicit approval, a current-process test URL,
successful guard checks and independent live target/schema verification. No live
browser or PostgreSQL result is claimed by static/mocked validation.

Historical 5E-B implementation validation after lifecycle correction: 80 safety tests passed,
0 failed, 0 skipped. Added fault injection covers immediately-after-creation,
assignment, post-resumption and caller-return failures; incomplete registration;
uncaptured creation; job/root termination failures; successful confirmed retry;
remaining-entry teardown after interruption; and setup/cleanup timeout unwinding.
RuntimeError, KeyboardInterrupt and SystemExit are included. Ruff,
formatting, targeted mypy on the two harness modules, frontend lint and TypeScript
checks passed. No live suite, database connection, schema setup, migration,
fixture seeding or application-service startup was performed. The existing
Playwright suite was not rerun because it starts Next.js.

5E-D non-destructive validation: **121 passed, 0 failed, 0 skipped**. Ruff,
formatting and targeted mypy passed. It extends these tests with ordered final-success
reporting, browser/cleanup failures, uncertain shutdown, failed drop commits,
missing/invalid targets and test URLs, read-only exact-target queries, and server
database mismatch rejection. These are mocked tests; they do not establish a new
live integration result or independent cleanup of the earlier run.

## Milestone 5F-D — Clinical History and corrections journey extension

This extension changes only `frontend/live-tests/clinical-journey.spec.ts` and
this note. No launcher, application code, migrations, database isolation guards,
process ownership rules, release observer or overall launcher PASS semantics
change. The existing single serial journey and unsaved browser contexts remain.
The journey has a 300-second test deadline, below the unchanged 600-second launcher
browser deadline; worker count, retries and artifact restrictions are unchanged.

Added live assertions, **pending separately approved live execution**:

- Inspect a confirmed patient-reported laboratory fact in Clinical History,
  including provenance, original-speaker confirmation and metadata-only source
  references. Pending/rejected proposals remain excluded by the original journey.
- Correct that fact through explicit UI history loading, review and submission.
  Reload accepted state and linked history, compare the unchanged original
  version, and show it as Superseded. Check the new reason, predecessor,
  provenance, retrospective observation time and exact `7.2000000000` string.
- Extract and individually confirm a separate practitioner-managed laboratory
  fact in the practitioner's own conversation, then correct it through the UI.
  Check exact `6.9000000000`, linked versions, preserved source/author and the
  opposite category's absence of a Correct record control for each role.
- Obtain actual Verified Records responses before both corrections; ensure
  neither prior released display is restored after correction. Original received
  byte digest/audit-intent observation and deliberate operation replay denial
  remain in the journey; an intent still does not establish delivery or reading.
- After patient UI review, submit a competing authorized correction through the
  same-origin real HTTP API. The stale UI request must receive 409, reload the
  committed current history, close the editor and issue no automatic second POST.
- Forward one actual correction POST with `route.fetch({maxRetries: 0})`, check
  the upstream 201, then abort only its browser acknowledgement. This manufactures
  no API response or database result. Require the uncertain-acknowledgement notice,
  fresh accepted/history reads, exactly one new linked version and no write retry
  through the recovery/check interval. A deliberate fresh history read establishes
  the committed outcome; no second correction is automatically submitted.
- Deny cross-patient history reads/writes and outsider practitioner writes using
  real authentication. Deny a prepared practitioner correction after the patient
  revokes consent; clear the UI history, accepted records and draft. Also deny
  subsequent history/write requests with the original authenticated bearer.
  Require exact generic scope-denial bodies and absence of protected markers.
- Preserve the delayed-real-facts-response switching scenario. Additionally,
  switch patients with an open reviewed correction draft, then sign out with
  another draft open. Require editor/history clearing, no restoration of drafts,
  and unchanged practitioner history length after signing back in. Existing
  browser-storage/cookie checks remain.

All synthetic patients still originate from ordinary invitations and activation;
consent, extraction, individual decisions, writes, history and verified releases
use the real Next.js rewrite/FastAPI path when the guarded run is later approved.
Only transport delay/loss is injected; no fabricated JSON, authentication bypass,
new debug route, database fixture writer or additional harness is introduced.
The current fixture has one accepted root per attribution category; helper card
selection deliberately relies on that controlled journey shape.

Validation during 5F-D is non-live: the unchanged harness safety tests use mocks,
and the ordinary Playwright frontend regression suite uses its existing API mocks.
TypeScript includes the live test source, but static checking and mocked tests do
not establish that the extended journey passed against PostgreSQL. The prior
user-reported successful guarded run predates these assertions. No database
connection, schema creation/drop, migration, live service or guarded browser suite
is authorized by this implementation milestone.

5F-D non-live validation: **121 harness safety tests passed, 0 failed, 0 skipped**;
**140 mocked frontend Playwright tests passed, 0 failed, 0 skipped**. ESLint,
TypeScript (`--noEmit --incremental false`, including the live journey source),
production build and `git diff --check` passed. The ordinary mocked tests start
only their existing Next.js test server. No backend test files changed, so no
backend lint/type-check changes were required. No guarded live run or PostgreSQL
connection occurred; the added live assertions have not yet been executed.

Remaining coverage boundaries: the live loss scenario checks an actual committed
write with a deliberately lost acknowledgement, not every native timeout or
disconnect timing. The competing write is scheduled deterministically between
review and submission, not a database stress/race proof. Existing backend locking,
rollback, revocation, response-release concurrency tests and mocked frontend
timeout/duplicate-click tests remain necessary. No finite no-retry observation
proves absence of all future requests. This adds no evidence binding, clinical
interpretation, medical correctness claim, treatment rule or dataset integration.

## Milestone 6B — Live record-question coverage

Only the existing journey and this note change. Production code, launcher,
migrations, ownership/cleanup guards, zero retries, artifact suppression and the
existing 300-second journey/600-second browser deadlines are unchanged. The user
reported successful prior live history/corrections execution and 829 PostgreSQL
backend tests after 6A. Those results do not validate this new journey extension.
**The expanded 6B live journey must still be run separately with approval.**

Added scenarios use ordinary authenticated browser requests through the Next.js
rewrite, existing synthetic accounts and real application endpoints:

- Before accepted history exists, a record question returns the exact fixed
  NO_MATCHING_RECORDS protocol status. Treatment-decision wording returns the
  fixed refusal, and a combined unsupported laboratory question clarifies.
- Asking about a pending HbA1c or rejected glucose returns no accepted match.
  Subsequent HbA1c queries return the accepted original/corrected version only.
  A correction clears the earlier question response as well as Verified Records.
- Both patient and consented practitioner ask supported laboratory/record questions.
  The existing corrections give two distinct roots the same observation instant;
  a latest-value question must return LATEST_AMBIGUOUS instead of choosing a role
  or value. A full-record question returns both accepted versions.
- A normal authenticated structured-entry POST creates one synthetic medication
  fact for the second patient, after the original empty-patient switching assertions
  have completed. It is not an ORM seed, direct database write or bypass of
  extraction confirmation. No existing first-patient history assertions are
  weakened. Both the second patient and the authorized practitioner request its
  recorded medication information. The test checks the existing adherence caveat
  and that first-patient version references do not enter the second patient's answer.
- Successful responses must have exactly the unchanged released-record envelope
  and record fields. Each record must reference one of the expected accepted
  version IDs with the correct author/attribution; every expected version appears.
  The rendered UI paragraph must equal the corresponding server response text.
  Decimal strings in authoritative facts are unchanged; renderer text uses the
  existing server Decimal normalization (for example 7.2000000000 becomes 7.2),
  without JavaScript numeric conversion or clinical interpretation.
- Selected laboratory, full-record and medication answers are checked using the
  existing private read-only observer: one committed PASS audit/intent for the
  operation, matching the exact received-byte SHA-256 digest. No observer or
  public debug endpoint is added. Deliberately replaying a successful question's
  operation UUID must return generic 409 withholding, never records. Other
  explicit panel submissions must use distinct operation UUIDs and only submit
  the question and operation ID.
- Real already-authorized question responses are delayed while the existing
  correction-draft switching and sign-out actions occur. They are forwarded
  unchanged after the context changes and must never repopulate question records.
  The original delayed-facts scenario remains intact. Delays are in transport
  after upstream response completion, not while claiming to hold server DB locks.
- Cross-patient and outsider question requests fail with exact generic scope
  denials. Another unsaved browser context signs in as the same eligible
  practitioner, obtains a real answer, and then asks again after patient consent
  revocation. The real 403 must clear its prior answer and accepted records, with
  no draft/raw error echo. Existing correction-denial and original-JWT denial
  checks remain. All contexts retain the existing empty storage/cookie assertions.

Only real upstream response forwarding/delay is used; there are no mocked API
bodies, external LLM services, evidence changes, new roles, datasets or direct
database fixture operations. Scope-denial assertions check absence of protected
markers; failures remain sanitized by the journey's existing outer error boundary.
No screenshots, DOM snapshots, traces, auth state, response attachments or saved
clinical data are introduced. The observer still reports only metadata counts and
booleans. A committed intent is not proof of delivery, reading, medical correctness
or clinical safety.

The medication fixture is deliberately introduced only after the second patient's
empty-state checks. First-patient helpers still have one laboratory root per
attribution. This is a controlled serial smoke journey, not a general fixture
library, database stress test, proof of all concurrent revocation timings or
completeness/latest-at-handoff verification. 6A lookup-time selection limits,
fixed-status semantics, mock-provider scope and frozen evidence policy remain.

6B implementation validation: **121 mocked harness safety tests passed, 0 failed,
0 skipped**; **159 mocked frontend Playwright tests passed, 0 failed, 0 skipped**.
ESLint, TypeScript (including the live test source), production build and diff
checks passed. Only existing Next.js mock-test tooling was started. No backend
test source changed, so no backend lint/type-check rerun was necessary. There
were no PostgreSQL connections, schema operations or guarded live runs. Review
of the two-file diff found no blocking issue; the added live assertions await
the user's separate PowerShell run.

## Milestone 7B — Live synthetic evidence provenance validation

Scope: the existing journey, this note, the private read-only observer in
`backend/tests/live_support.py`, and its non-destructive safety tests. No production
release contract, feature, migration, catalogue, launcher lifecycle or cleanup
guard changes. The user confirmed 850 PostgreSQL backend and 172 frontend tests
after 7A; those results do not establish execution of these new live assertions.
**The 7B guarded journey remains pending the user's separate approved run.**

The journey now explicitly sets the synthetic-provenance checkbox on every normal
question submission, keeping all old requests in v1 by default. It verifies exact
two-field v1 or three-field opt-in request envelopes, fresh operation UUIDs,
strict v1/v2 response envelopes and exact server-text rendering.

V2 scenarios include:

- Patient-reported original and corrected HbA1c (%) and practitioner-managed HbA1c
  (%) while each is the unique latest measurement. Responses must reference the
  expected accepted version, author, attribution and claim; metadata must match
  the repository's unchanged approved synthetic source, unit, rule and manifest.
  The manifest hash is independently computed from canonical fixture JSON. Source
  IDs/versions/edition, unit/locator/hashes, rule and manifest hashes are checked in
  the actual UI, with the emphatic synthetic/not-guidance disclaimer and no links.
- Both independently assessed statuses must be RESOLVED and
  `clinical_support_asserted` must be false. Synthetic applicability is not
  clinical support, correctness or safety. No evidence content, real guideline,
  threshold, recommendation or private transcript is introduced.
- Existing audit/digest checks cover the **entire received v2 body**, including all
  provenance. Explicit v1 calls before and after v2 must keep the original
  representation and show no residual provenance. Evidence-mode ties clarify
  without any record/provenance; medication evidence requests clarify rather
  than silently falling back to plain record restatements.
- A correction removes earlier evidence and text; a fresh request identifies the
  new accepted version. The existing v1 confirmation, history/correction,
  replay, missing/unsupported question and denial scenarios remain intact.
- A second-patient synthetic HbA1c fact is created through the ordinary authorized
  structured-entry API, after its existing empty-state and medication checks.
  This preserves all first-patient correction/history assumptions. It provides a
  unique supported measurement for real v2 responses delayed while switching
  patients and signing out; neither text nor provenance may reappear afterward.
- After the original first-patient revocation assertions and discovery counts,
  the second patient independently revokes the practitioner's consent. The
  practitioner must lose already displayed v2 text/provenance on the next real
  evidence question, receive an exact generic 403, and display no accepted facts.
  No consent is automatically restored. Both patients still control their own
  relationships. All unsaved contexts retain existing storage/cookie checks.

### Migration 0008 and observer coverage

The harness's unchanged schema-bound bootstrap calls `tests.database.migrations()`;
the list includes `0008_evidence_release` after `0007_response_release_intent`.
A new offline safety assertion checks that relationship. The bootstrap invokes
the upgrades in the exact owned schema; it does not stamp an Alembic version table.

The existing private observer's exact-operation SELECT additionally reads the
stored intent representation. Its only extra output is the boolean
`evidence_representation`, true for exactly one v2 intent. It retains read-only
transactions, independent database/schema/ownership verification, bounded queries,
exact operation filtering and disposal. It returns no representation strings,
identifiers, source metadata, clinical values, credentials or deletion authority.
No startup, process ownership, migration execution or final PASS logic changes.

The v2 journey requires one committed intent, PASS audit, exact full-body digest
match and this stored-v2 boolean. The old 0007 CHECK constraint cannot accept a v2
intent, so successful guarded execution exercises the widened 0008 representation
constraint. V1 audit checks require the boolean to be false while still requiring
the committed intent/digest/PASS. This corroborates migration coverage in the
isolated schema; it is not proof of browser delivery/reading or clinical validity.
New mocked observer tests cover v1/v2, missing events and ownership failure before
event lookup. No database connection or schema operation is used in those tests.

Existing frozen-catalogue deployment limits, lookup-time latest selection,
transport-race versus database-race distinctions and artifact suppression remain.
Static checks and ordinary mocked browser tests do not establish a live v2 run.

7B implementation validation: **126 non-destructive harness tests passed, 0 failed,
0 skipped**; **56 focused mocked frontend tests passed, 0 failed, 0 skipped**
(record questions, synthetic provenance and clinical corrections). Ruff,
formatting, configured targeted mypy, ESLint, TypeScript, production build and
diff checks passed. No new full backend/frontend run was needed; the previously
confirmed full suites are separate from these focused results. The four-file
review found no blocking issue. No guarded launcher, PostgreSQL connection,
migration application, dataset or development/production database was used.

## Remaining limitations

- Live journey and Windows process supervision still require approved live validation.
- A privileged local database owner can alter schema comments or bypass triggers;
  this harness is an accidental-target guard, not a defense against that owner.
- A crashed launcher may leave a private schema; no automatic orphan scavenging or
  cleanup of schemas belonging to other runs is permitted.
- Interrupted creation before handle capture may leave an unresolved suspended
  process. The harness cannot safely infer its PID or claim termination; it retains
  the schema and requires separately approved operator inspection. It never treats
  an empty job as proof that this unknown root is gone.
- Global test-DB locks, other tools using the same DB, abrupt OS termination and
  concurrent deployments are outside this isolated local-run guarantee.
- Live tests use no evidence bindings or real medical evidence; pinned synthetic
  evidence policy and frozen-catalogue limitations remain unchanged.
- No durable transcripts, unrestricted AI, diagnosis, prescribing, clinical
  correctness claims or new clinical rules are introduced.
