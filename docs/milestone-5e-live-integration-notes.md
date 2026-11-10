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
