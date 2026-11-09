# Milestone 5C — Verified Records frontend integration

## Scope

Adds a separate Verified Records panel to the existing authenticated workspace,
alongside the accepted longitudinal record and supervised extraction conversation.
Reuses existing panels, responsive layout, in-memory session, and `/api/v1` Next.js
rewrite. No authentication, backend, evidence, migration, dataset, foundational
requirements or architecture changes. Five files suffice; no extra styling or API
client framework is needed.

Files: `frontend/src/components/clinical-types.ts`,
`frontend/src/components/conversation.tsx`,
`frontend/src/components/verified-records.tsx`,
`frontend/tests/verified-records.spec.ts`, and these notes.

## Original request and released response

One explicit button action requests a restatement of one selected current accepted
fact. The accepted-fact TypeScript contract now includes the existing backend
`author_id` and `author_role` fields. These are assertions copied from the record,
never authorization. No proposals or conversation text enter this request.

The POST to `/api/v1/patients/{patient_id}/verified-records` sends exactly:

```text
operation_id: new UUID
request:
  draft:
    draft_id: new UUID
    patient_id: current patient profile UUID
    claims:
      - claim_id: new UUID
        patient_id: current patient profile UUID
        claim_type: FACT_RESTATEMENT
        fact_version_ids: [selected accepted version UUID]
        source_attribution:
          author_id: record author UUID
          author_role: record author role
          category: record attribution
        proposed_content:
          fact: unchanged structured accepted fact payload
          observed_at: unchanged observation timestamp
  bindings: []
```

Every explicit action gets a new operation, draft and claim UUID. Decimal strings
are preserved without JavaScript number conversion. This uses the existing no-binding,
4D-only behavior; it does not invent evidence selection. No client PASS, digest,
receipt, audit ID, intent ID or preverified text is supplied.

Only a successful, fully parsed server response with exact `record-response-json-v1`
shape and exactly the requested claim ID is displayed. Unknown fields, mismatched
claim IDs/representation versions, empty text and incomplete JSON withhold display.
Text is escaped React text, never HTML or Markdown execution. No restatement is
generated locally from accepted facts. Presentation checks are not verification:
the backend remains the sole authority for grounding, authorization and release.

The panel distinguishes server-released technical restatements from accepted facts
and PENDING/CONFIRMED/REJECTED extraction cards. It explicitly disclaims medical
correctness, diagnosis, treatment advice and clinical evidence support.

## Security and lifecycle

The existing JWT is passed only in the Authorization header. Fetch uses `no-store`,
`credentials: omit`, `redirect: error` and `no-referrer`. No cookies, browser storage,
URL parameters, analytics, transcript persistence or logging are added.

A synchronous controller ref prevents duplicate submissions. Every new request
clears the prior released content before sending. The button and selector are
disabled while pending, while sign-out and patient switching remain available.

The Verified Records component is unmounted on sign-out/context change and during
parent record operations (refresh, extraction, confirmation). Cleanup marks its work
inactive and aborts outstanding fetches; late results cannot restore content. A new
context gets a new component and fresh server-loaded accepted facts. Account/role
changes go through existing sign-out/sign-in state. No response is reused across
contexts, refreshes or sessions.

401 clears the authenticated session through the existing failure handler;
403/404 clear the patient context and all record/conversation displays. Generic
messages hide server error details. 409 withholds content and directs an explicit
server-state refresh; 422 rejects the request without displaying submitted data.
Other failures, incomplete responses and a 20-second client timeout are uncertain
outcomes. There is no automatic retry, replay, background polling or resumed intent.
A new explicit action always creates a new operation UUID. Client cancellation does
not prove rollback or non-delivery of an already prepared response.

Existing 4H/5B two-transaction audit/intent durability, current consent checks,
fresh snapshot/catalogue revalidation, exact-byte binding and one-shot ownership
remain unchanged. Frontend state never authorizes a release. Existing rendered data
cannot be remotely recalled on revocation; a subsequent denied request clears the
display. This milestone introduces no real-time revocation notification mechanism.

## Validation

- Frontend lint: passed, no warnings/errors.
- TypeScript `tsc --noEmit`: passed.
- Next.js production build: passed.
- Playwright Chromium: **33 passed, 0 failed, 0 skipped** (34.6 seconds): all 15
  existing smoke/confirmation tests plus 18 focused Verified Records cases.
- Focused coverage: patient/practitioner requests, exact request keys and attribution,
  decimal preservation, fresh UUIDs, server-only escaped rendering, withheld statuses,
  revocation-style 403 and session failures, uncertain network outcomes, no automatic
  retry, duplicate clicks, sign-out/patient switch during pending work, timeout,
  malformed responses, no accepted records, and refresh invalidation.

An initial test run failed due to ambiguous alert selectors matching Next.js's route
announcer; selectors were corrected before the successful full run.

Browser tests intercept API requests and do not contact FastAPI. Live browser-to-FastAPI
integration is not validated here: no dedicated live synthetic-account fixture and
running isolated backend were established for these browser tests. The separately
reported 649-test PostgreSQL backend baseline is not a substitute for that coverage.
Backend code/tests were not changed or rerun for this frontend-only increment.

## Remaining limits

Research T2DM prototype with synthetic/de-identified data only. No unrestricted AI
prose, medical interpretation, evidence rules, autonomous diagnosis or treatment.
The UI still uses the existing manual patient profile selection and does not add
onboarding/consent management screens. Request authorization is enforced by the
server, not by hiding or enabling controls. No new shared transcript access.

Transport success is not proof of patient receipt/reading or clinical correctness.
Inherited 4H connection-loss/disclosure races, cooperative cancellation cleanup,
metadata-only audit, frozen-catalogue deployment policy and uncertain-delivery
limitations remain. A frontend timeout cannot retract disclosed bytes. Future
body-transforming middleware or broader integration requires separate review.
