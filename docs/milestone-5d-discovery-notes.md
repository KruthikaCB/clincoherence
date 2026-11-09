# Milestone 5D-A — Secure Patient Discovery

## Scope and contracts

Two authenticated read-only routes, using the existing `/api/v1` prefix:

- `GET /auth/profile` returns `{ "role": "patient" | "practitioner", "profile_id": UUID }`.
  The profile ID is resolved from the authenticated account's role-specific profile,
  not copied from the account UUID. Only the caller's own profile is returned.
- `GET /patients` returns `[{ "patient_id": UUID }, ...]` for an eligible practitioner,
  ordered by patient profile UUID. An eligible practitioner with no accessible patients
  receives `[]`. An authenticated patient receives generic 403, not a patient list.

Both response schemas are frozen, extra-forbidden allowlists; successful responses set
`Cache-Control: no-store`. No caller-selected account, patient or practitioner query
parameter is used as authority. Unrecognized query parameters are ignored, as with
existing endpoints. There are no request bodies, directory searches, names, emails,
clinical facts, invitation secrets, consent histories or authorization flags in the
responses. A discovery result is not a grant or credential.

JWT authentication is the existing `CurrentAccount` dependency. Missing, invalid,
expired or forged credentials and inactive/missing/ineligible account profiles use
the existing generic 401 convention. Missing signing configuration remains 503.
The existing private validation route avoids echoing invalid submitted values.
No new authentication mechanism, role, registration or consent behavior is introduced.

## Authorization and query design

The service reuses `eligible_account` and `authorize_patient_scope`. Own-patient
profile resolution is checked with patient-scope authorization; practitioner profile
resolution requires its account mapping, practitioner role and explicit prototype
authorization. Eligibility is rechecked before returning the profile identity.

Practitioner discovery first verifies current eligibility and role. A SQL query
selects patient UUIDs only, joining the patient account, current pair consent,
practitioner profile and practitioner account. Predicates require:

- The authenticated account owns the authorized practitioner profile and remains active.
- The patient has an active patient account/profile and valid verified/activation times.
- Consent is specific to that pair, granted, active-patient-linked, not revoked, and
  has a non-future grant timestamp.

Each candidate is then checked through the existing authoritative patient-scope
service. A 403 candidate is omitted without explaining its absence; authentication,
database or other failures are not converted to a successful list. Final account
eligibility is checked again. Existing unique account/profile and patient/practitioner
pair constraints prevent duplicate relationships; ordering is deterministic.

Invitation issuance and activation are not queried as access conditions. No pending,
revoked or missing consent can create discoverability. Another practitioner's grant
cannot substitute for this practitioner's grant. Activation without an explicit grant
leaves the patient absent from the practitioner's discovery results.

## Advisory and transaction boundaries

The service performs SELECTs only and introduces no explicit commits, writes, locks
or historical events. Existing request-session commit/rollback ownership is unchanged.
There is no schema change or migration.

Discovery reflects authorization at its database checks. It is not an atomic promise
that a multi-patient list remains current through network delivery or future use.
Revocation committed before a subsequent discovery is reflected by its current checks;
revocation racing after the last check may make an already returned identifier stale.
Identifiers already disclosed cannot be recalled. This route does not hold multiple
patient locks or replace the existing consent-coordinated clinical release boundary.

Every subsequent scope, clinical, confirmation or verified-record request must
independently enforce current eligibility and consent. Existing patient locking,
correction coordination, speaker-specific confirmation, audit and 4H/5B release
semantics remain unchanged. Multiple independently consented practitioners remain
supported. No patient identity, clinical content or reason for absence is disclosed.

The SQL access predicates intentionally prefilter before the existing policy check;
future authorization policy changes must keep discovery filtering aligned. The extra
per-candidate checks are conservative but add queries; this prototype list has no
pagination or large-cohort performance claim.

## Tests and validation

Using the existing backend virtual environment:

```text
python -m pytest tests/test_patient_discovery.py -q
python -m pytest -q
python -m mypy app/schemas/patient_discovery.py app/services/patient_discovery.py app/api/v1/patient_discovery.py app/api/v1/router.py --follow-imports=silent
```

Focused discovery tests: **11 passed, 0 failed, 16 skipped**. Ruff, formatting and
targeted mypy pass for the five affected Python files (mypy covers the four source files).
Full available backend suite: **462 passed, 0 failed, 214 skipped** (676 collected,
9.13 seconds). All skips are opt-in PostgreSQL cases without the test configuration.

Offline tests cover authentication rejection, immutable minimal schemas, route coexistence
and delegation to existing authorization with conservative failure handling. PostgreSQL
tests cover both own-profile roles, account/profile ID separation, caller-ID substitution,
inactive/missing/pending/deauthorized profiles, future timestamps, role restriction,
empty lists, deterministic ordering, pair isolation, multiple practitioners, consent
revocation using an existing JWT, invitation/activation without consent, no-store/minimal
responses and unchanged relationship/history counts and history values.

`TEST_DATABASE_URL` is absent in this process. PostgreSQL tests were skipped; no database
integration validation or migration application was attempted, and the development
database was never substituted. The existing shared fixture requires an explicit
`postgresql+asyncpg` URL whose database is exactly `clincoherence_db_test`, verifies
`current_database()` before isolated test-schema setup, and never falls back to
`DATABASE_URL`. Full PostgreSQL validation remains required in the configured test session.

## Files and exclusions

Only the six approved files are changed: the new discovery schema, service, API,
tests and these notes, plus router registration. The exact `/patients` route has
no patient-ID path parameter and does not shadow `/patients/me/consents`,
`/patients/{patient_id}/scope`, facts, confirmations or verified-record endpoints.
Existing authentication response schemas are unchanged.

No frontend changes, migration, dataset access, clinical logic, evidence policy,
invitation/consent mutation, new product role, self-registration, automatic consent,
requirements/architecture changes or later milestone work is included. Profile UUIDs
remain sensitive metadata and must not be treated as de-identification or production
credential assurance. No stage, commit or push is part of this increment.
