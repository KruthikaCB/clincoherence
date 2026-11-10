# Milestone 6A — Patient-specific record questions

## Narrow supported behavior

`POST /api/v1/patients/{patient_id}/record-questions` accepts exactly `operation_id`
(UUID) and `question` (string, 1–500 characters). It never accepts a caller's
patient context, claims, PASS, rendered text, audit IDs, receipts or digests.

The deterministic parser recognizes the following full questions, ignoring case,
repeated whitespace and one terminal question mark:

- What confirmed clinical information is recorded?
- What medications are currently recorded? / What medications are recorded?
- What symptoms or relevant history have been recorded?
- What symptoms have been recorded?
- What relevant history has been recorded?
- What is my latest recorded value for hba1c? (also glucose, creatinine, egfr,
  urine albumin creatinine ratio). The grammar also accepts “the”, “this patient's”
  or “the patient's” instead of “my”, and permits omitting “value for”.

This is not general language understanding. Other wording or combined questions
requests clarification. Recognized clinical-decision wording returns a fixed
refusal; unrecognized advice requests still receive no clinical answer. There is
no external LLM, interpretation, diagnosis, prescribing or medication change.

Medication results mean current accepted versions of recorded medication facts,
not current adherence, active therapy or a verified medication reconciliation.
Laboratory selection uses the greatest timezone-aware observation timestamp,
not entry time. Any tie across roots/attributions/units requests clarification,
even if values match. No unit conversion, clinical thresholds or value arithmetic
is performed. Corrections use their current accepted version only; observations
may be retrospective. Empty matches request clarification without asserting that
a condition, medication or symptom is absent. More than 32 matches requires a
narrower question; nothing is silently truncated.

## Authorization, verification and release

Existing JWT/account eligibility and patient scope dependencies apply first.
Lookup uses the existing trusted dedicated release-session factory, 4A patient
locks, accepted-state loader and correction/provenance checks. Invalid, foreign,
pending or rejected state fails closed. No private transcripts or proposals are
used. The lookup session is explicitly rolled back and closed; it sends no
clinical body and makes no clinical or audit writes.

The server builds strict 4D FACT_RESTATEMENT claims referencing the original
accepted version, exact payload (including Decimal), observation time, author and
attribution. Claim and draft IDs derive from the operation UUID and version IDs.
No unverified prose or evidence bindings are added. Question text is not copied
into clinical state, audit metadata or release records.

The endpoint returns the existing `AuditedRecordResponse` itself. It does not
serialize or buffer clinical records outside that adapter. Its unchanged T1
independently verifies claims through 4A/4D/4E/4F, persists metadata-only gate and
PASS-only release intent, and commits before sending. Its distinct T2 revalidates
authorization, consent, accepted-state snapshot, catalogue and exact byte digest;
locks and connection checks remain in place through bounded ASGI handoff. An old
intent cannot be replayed. Gate PASS remains technical eligibility, never clinical
correctness, medical safety or delivery proof.

Selection is point-in-time at authorized lookup, not a new verified completeness
or latest-at-handoff assertion. A new independent root arriving between lookup
and T1 may not be selected. Selected versions must still independently ground at
T1; a correction that supersedes them withholds the response. Snapshot changes
between T1 and T2 remain subject to the existing strict release revalidation.
Stronger atomic query-selection assertions would require separate approval for
a release-contract extension; this milestone does not claim those semantics.

## Fixed statuses and UI

Nonclinical statuses use `record-question-status-v1`, CLARIFY/REFUSE and fixed
reason codes: QUESTION_UNSUPPORTED, CLINICAL_DECISION_UNSUPPORTED,
NO_MATCHING_RECORDS, LATEST_AMBIGUOUS, TOO_MANY_RECORDS. These are question protocol
statuses, not persisted 4F gate outcomes. Even selection-dependent status metadata
uses the existing `ClinicalResponse` authorization/lock-through-handoff guard and
no-store headers. It contains no fact values, IDs, question text or raw errors.
It is not a verified clinical response or a durable audited release event.

The separate dashboard panel sends only question/operation UUID through the
existing rewrite and in-memory JWT transport. Each explicit attempt gets a fresh
UUID. It accepts only the exact released-record representation or allowlisted
status shape, displays renderer-produced server text, and maps codes to static
messages. It never synthesizes an answer from accepted facts or confirmation
cards. Error bodies and malformed or forged representations are not displayed.

There are no automatic retries. Timeout, network failure or incomplete response
is uncertain and requires another explicit action. Component-owned cancellation
and generation guards discard late responses. A distinct account/patient/context
key prevents sibling key collisions. Parent refresh, extraction/confirmation,
correction, consent mutation, switching and sign-out unmount and clear the panel.
Already viewed information cannot be recalled; revocation is enforced on
protected requests, not through a new push notification. No browser storage,
cookies, URL content, analytics, logs or durable conversation transcript is added.

## Validation and limits

Tests cover the exact grammar, ambiguous/empty/oversized selection, observation
ordering, Decimal preservation, both attribution categories, immutable inputs,
foreign/pending/rejected state, forged fields, authentication, protected fixed
statuses and unchanged 4H commit-before-send orchestration. Dedicated-test-only
PostgreSQL tests cover both roles, replay, cross-patient denial and revocation,
correction or catalogue change after lookup. Frontend mocks cover real envelope
shapes, fixed statuses, denied/failed responses, malformed output, duplicate
clicks, uncertain acknowledgement, fresh UUIDs, context changes and state clearing.

No database migration, approved-document change, dataset access, general chatbot,
external provider, clinical evidence support claim or guarded live suite is
introduced. PostgreSQL and browser-to-FastAPI validation must be reported
separately from mocked/unit results. No audit fields were expanded: raw questions,
unsupported attempts and selection-policy identity are not durably recorded;
existing released claim/version provenance is retained by 4G/4H.

Validation in this environment: full backend suite **610 passed, 0 failed,
219 skipped** (829 collected); the new question module accounts for **27 passed
and 5 skipped**. `TEST_DATABASE_URL` was absent, so PostgreSQL tests skipped;
neither the development nor production database was used. Full mocked frontend
suite **159 passed, 0 failed, 0 skipped**. Ruff, formatting, targeted mypy,
ESLint, TypeScript and production build passed. A sibling React key collision
found by the correction regressions was fixed with a distinct question-panel
key; all existing assertions remain intact. No guarded live run occurred.
