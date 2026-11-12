# M8 Phase 1: clinician fact review

Backend only. Separate clinician approval history is implemented; recommendations,
evidence activation, structured clinical qualifiers, and frontend controls are not.
Patient confirmation, accepted-state selection, corrections, record questions,
summary and verified-record behavior retain their existing semantics.

## API contract

All routes are under `/api/v1`, require an eligible practitioner and current
patient authorization, and return `Cache-Control: no-store`. Normal patients
require pair-specific consent; the existing guarded, owner-only synthetic demo
policy is reused without expanding its permissions. Patients cannot use these
review APIs. Reviewer identity comes exclusively from the authenticated account.

- `GET /patients/{patient_id}/clinical-fact-reviews`: current accepted versions
  and the requesting clinician's review status. No review means `UNREVIEWED`.
- `GET /patients/{patient_id}/facts/{fact_id}/clinical-reviews`: that clinician's
  append-only review history across versions of the selected fact.
- `POST /patients/{patient_id}/facts/{fact_id}/clinical-reviews`: one decision.

GET requests accept `limit` (1–100, default 100) and `offset` (0–100000, default 0).
Current entries are ordered by fact UUID; history by recorded time and event UUID.
Each page is authorized separately; pagination is not a cross-request snapshot.

POST requires `operation_id`, `expected_version_id`, `expected_review_id` (explicit
null for the first review of this version), and `decision` (`APPROVE`, `REJECT`,
`WITHDRAW`). Reject and Withdraw require a nonblank reason of at most 2000
characters; approval may include a reason. Extra fields are rejected.

POST returns HTTP 200 with an immutable event receipt. It does not assert that
the event is still the current decision. GET current state to learn that. An exact
retry, after fresh authorization, returns the original receipt without insertion
or reactivation. Changed input with the same patient/clinician/operation ID fails
with 409. Foreign/missing accepted versions fail without exposing their contents.

## Decision lifecycle

- Approve and Reject can only target an exact current accepted version.
- A clinician may change their decision by appending another event with the
  previous event ID. Concurrent requests based on the same predecessor cannot
  both win. Distinct clinicians have independent sequences and operation IDs.
- Withdraw requires that clinician's latest decision for the exact version to be
  Approve. It is also permitted after a correction supersedes that version.
- A correction starts unreviewed for every clinician. Approvals never transfer
  between versions. Existing review events remain visible as historical receipts.
- Reject/Withdraw do not reject a patient confirmation, delete a fact, change its
  attribution, or erase another clinician's review. Clinical eligibility is not
  implemented in Phase 1: no existing consumer is changed to use these approvals.

## Persistence and concurrency

Migration `0011_clinical_fact_reviews` follows `0010_clinical_summary_release`.
It adds only a supporting unique constraint to `clinical_fact_versions` and a new
`clinical_fact_review_events` table. There is no data backfill or implicit approval.
Composite foreign keys bind reviews to the patient, fact root, accepted version,
and same-clinician predecessor. Sequence uniqueness prevents branching; a
practitioner-profile FK prevents patient accounts from being stored as reviewers.

Database triggers prohibit UPDATE, DELETE and TRUNCATE of review events. Downgrade
takes an exclusive table lock and refuses if any review history exists; it does
not delete history to make rollback possible. Application authorization is still
required: these constraints are not a replacement for consent checks.

The service uses the existing patient/account/practitioner lock ordering shared
with consent mutation and corrections. It rechecks authorization before inserting
and commits the event before acknowledging success. The existing ClinicalResponse
rechecks authorization and retains the patient gate through response handoff.
Revocation in the acknowledgement gap suppresses the body, not a committed event.
A historical receipt must never be used as a future authorization permit.

## Validation and operational boundary

`python -m tests.offline_runner` disables dotenv loading, removes TEST_DATABASE_URL,
uses an inert application database URL, and blocks asyncpg and httpx network
connections. It permits in-process ASGI transport and mocked provider tests. It
reuses an installed global pytest if absent from the runtime venv; when
pytest-asyncio is unavailable it uses the installed AnyIO asyncio backend. No
packages are installed. Normal configured pytest remains supported.

Offline coverage includes strict requests, role denial, exact version/clinician
query scope, transitions, stale decisions, retries, rollback, response handoff,
and ORM/migration schema agreement. Opt-in PostgreSQL tests cover lifecycle,
cross-patient access, append-only triggers, constraints, concurrent decisions and
consent revocation ordering. Those tests are skipped by the offline runner.

The migration has not been applied to the user's database. New review endpoints
require migration 0011 before use; old endpoints do not require the new table.
Dedicated disposable PostgreSQL validation, populated-database upgrade validation,
and permission to migrate an identified existing database remain separate steps.
Phase 2 and clinical evidence activation require separate approval.

### Recorded validation

Final guarded offline backend run: **1015 passed, 256 skipped, 0 failed** (1271
collected). Database-dependent tests were skipped, including the new PostgreSQL
race, trigger, constraint, upgrade-preservation and downgrade-refusal tests.
The offline ORM/migration DDL comparison passed. `git diff --check` passed.

The available environment has pytest 9.2.0 outside the application runtime venv
and AnyIO 4.16.0 inside it, but lacks pytest-asyncio, Ruff and mypy. The offline
runner reused installed packages and AnyIO's asyncio backend without installing
anything. Four warnings were reported: the unused pytest-asyncio configuration
key, two existing asyncio markers, and an existing Pydantic deprecation. This is
not validation of the repository's declared pytest <9 development environment;
Ruff/mypy and the standard pinned-development setup were not exercised.

Windows asyncio initialization hung inside the sandbox even for `asyncio.sleep(0)`;
the guarded test run was authorized outside it with dotenv loading and database/
HTTP connections still disabled. No existing database was connected to or migrated.
