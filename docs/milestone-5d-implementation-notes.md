# Milestone 5D-C — Invitation, Consent and Patient Selection Dashboard

## Scope

Connects the existing authenticated Next.js workspace to committed discovery,
invitation and consent APIs. Management panels precede the accepted-record and
supervised AI workspace; this remains a doctor–patient research dashboard, not an
unrestricted medical chatbot. Reuses existing panels, responsive grids, JWT-in-memory
behavior and the `/api/v1` rewrite. No dependency, backend, migration, evidence,
dataset, activation-page or foundational-document changes.

Exactly eight files: new invitation/consent/patient-selector components, modified
`conversation.tsx`, new `onboarding-consent.spec.ts`, updated confirmation and
verified-record tests, and these notes. The new patient-selector module also holds
the small shared dashboard transport and cancellable-operation hook; no extra API
client file or framework is introduced.

## Discovery and context selection

Login continues to POST identity/password and GET `/auth/me`. It then GETs
`/auth/profile`, validates `{role, profile_id}` against the account role, and uses
that server-resolved profile identity. Account and profile UUIDs are never substituted.
The signed-in header is available while profile discovery is pending, so sign-out
can cancel and invalidate that work.

Patients automatically load their own patient profile; after a denied or failed
load, an explicit Open my record action can retry that same profile. There is no
arbitrary patient UUID entry. Practitioners GET `/patients`, accepting only the
minimal `[{patient_id}]` response and offering those identifiers in a selector.
Empty lists reveal no reason for absence. List refresh explicitly clears the selected
clinical context; selecting a patient independently loads authorized facts and the
current speaker's confirmations. Discovery is advisory, never an access permit.

The header shows the caller's own profile UUID, allowing prototype practitioners to
share their PROFILE UUID separately with a patient for an explicit consent decision.
No directory or human-readable identity lookup is invented.

## Practitioner invitations

- POST `/patient-invitations` with only `{intended_identity}`, normalized to the
  existing synthetic.invalid contract. No issuer ID or consent fields are supplied.
- Validate the existing created response: invitation/patient UUIDs, URL, separate
  prototype code, expiry and notice. URL validation permits the backend's HTTPS
  origin policy or local HTTP origins, requires `/activate#token=...`, and rejects
  query strings, credentials and malformed/duplicate token fragments.
- Link and separately revealed verification code use distinct, labeled, read-only
  copyable fields. No automatic clipboard write, navigation, inspection-token POST,
  message delivery or consent grant occurs. The operator must deliver them separately.
- GET `/patient-invitations/{known_id}` for issuer-owned status; POST its `/revoke`
  path after an explicit inline confirmation. Revoke has no required body; the UI
  sends empty JSON. Status responses are strictly validated and matched to the ID.
- `pending`, `consumed`, `revoked` are invitation states, not access decisions.
  Pending does not override expiry; consumed means redemption, not current consent.
  Revocation attempts and unavailable/non-pending states hide stored secrets.

Only the latest created invitation's secrets are kept in the issuer component's
memory. Known metadata IDs can be entered for later lookup, but no listing or secret
recovery endpoint exists. No automatic creation retry occurs after network failure
or timeout. Another explicit creation may create another pending invitation: the
existing backend has no creation operation-ID/idempotency contract. A lost successful
creation response cannot recover its raw link/code from stored hashes.

## Patient-controlled consent

GET `/patients/me/consents` loads only the authenticated patient's relationships.
POST the same path with exactly `{practitioner_id}` for explicit grant. The input is
clearly labeled as a practitioner PROFILE UUID, obtained through a trusted prototype
contact; eligibility is determined exclusively by the backend. No caller-selected
patient/actor ID or automatic grant is introduced.

Inline review identifies the selected practitioner profile before explicit POST
`/patients/me/consents/{practitioner_id}/revoke` (empty JSON, no required body).
All five response fields are checked: consent/practitioner IDs, status, grant timestamp
and nullable revocation timestamp. Completed mutations are followed by a fresh GET;
the UI does not optimistically mark access granted/revoked. It does not rewrite history.
Uncertain mutation or refresh outcomes disable further changes until the user explicitly
refreshes relationships. Automatic POST replay is never performed. Existing backend
semantics govern repeated grants, revocations and re-grants.

Consent mutation starts by invalidating/clearing current clinical work. Consent state
is reloaded, but clinical content needs a new explicit server-state refresh afterward.
Revocation blocks subsequent protected operations under backend enforcement; previously
viewed information cannot be recalled. No real-time cross-browser revocation broadcast,
production credential verification or shared private transcript access is claimed.

## Lifecycle, security and failures

The shared transport retains Authorization-header JWTs, no-store, credentials-omit,
redirect-error and no-referrer options. It never reads/echoes error bodies, persists
data, logs clinical content or automatically retries. Each request has a 20-second
client timeout and observes its owning AbortSignal.

Parent context invalidation increments an epoch and aborts the old request group
before scheduling replacement work. It clears proposals, accepted facts, source
turns, drafts, notices and patient selection as appropriate. Context keys unmount
Verified Records and issuer invitation components so old secret/response state is
not reused. Consent operations invalidate clinical work without unmounting their
own pending mutation. Sign-out clears session/profile data as well. No tokens or
protected content are put into storage, cookies, browser URLs or analytics.

Independent component operations use synchronous pending refs and generation checks
to prevent duplicate submission and stale updates after cleanup/Strict Mode replay.
Clinical requests keep their existing epoch/speaker/patient checks and now accept
abort signals. Prior-session discovery cannot overwrite a newly signed-in role.
401 ends the session; 403/404 and other clinical failures clear clinical content.
Old denial responses are subject to the same lifecycle guards. Nonclinical errors
are generic and disclose no server details or unrelated identities.

Existing individual confirmation and Verified Records semantics remain: only the
original speaker's proposals appear; accepted facts remain separate from proposals;
only 4H/5B server-released restatements are displayed as technically verified. No
client gate decisions, evidence rules, clinical correctness claims or medical advice
are added. Uncertain verification and confirmation outcomes retain their existing
explicit-action behavior; no automatic clinical mutation retries are introduced.

## Validation and limitations

- Focused dashboard Playwright: **19 passed, 0 failed, 0 skipped** (23.7 seconds).
- Full frontend Playwright: **76 passed, 0 failed, 0 skipped** (59.4 seconds), including
  all 57 baseline activation, smoke, confirmation and Verified Records cases.
- Frontend lint: passed with no warnings/errors.
- TypeScript `tsc --noEmit`: passed.
- Next.js production build: passed.

Focused browser coverage includes own-profile/account distinction, authorized and
empty lists, invitation creation/status/revocation, separate secrets and no recovery,
explicit consent/list refresh/revocation, uncertainty/timeout behavior, malformed
status withholding, role mismatch, stale discovery across sign-out/role changes,
invitation/clinical switching races and denied clinical response suppression.

Existing confirmation and Verified Records tests now mock real discovery contracts
instead of manual UUID navigation. Confirmation fixtures use UUID identifiers and
complete response metadata, source offsets/hashes and linked accepted-state fields;
accepted-record fixtures include the backend response fields and valid decimal strings.
Activation tests are unchanged and remain part of the full regression suite.

Playwright intercepts requests with synthetic fixtures. Live browser → Next.js rewrite
→ FastAPI → PostgreSQL integration remains unvalidated; passing browser mocks and the
separately validated 676-test backend baseline do not prove that combined deployment.
No backend tests, database operations or destructive test setup are run here.

Discovery can become stale after its authorization checks. Backend authorization,
consent locks, immutable history and 4H release remain the sole enforcement boundary.
Client cancellation cannot undo an already committed mutation or retract viewed/copied
information. Clipboard contents explicitly copied by the user are outside component
cleanup. No secure erasure of browser internals or transport-delivery guarantee is made.
Reload deliberately loses JWTs, secret links/codes and clinical view state. Production
identity assurance, patient-name directory, invitation listing/recovery/resend, real
LLM integration and treatment behavior are excluded.
