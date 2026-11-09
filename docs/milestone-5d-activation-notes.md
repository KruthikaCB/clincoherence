# Milestone 5D-B — Patient Activation Page

## Scope and workflow

Adds `/activate` and shared TypeScript onboarding contracts, using the existing
Next.js styling and `/api/v1` rewrite. Only the four approved new files are included:
the page, `onboarding-types.ts`, `activation.spec.ts`, and these notes. No existing
frontend/backend files, authentication mechanisms, migrations, datasets, clinical
evidence rules, requirements or architecture documents are changed.

1. On the client, capture exactly one URL-safe 43-character `#token=...` value into
   a component ref. Query-string tokens and additional/duplicate fragment fields
   are not accepted. Promptly remove fragment/query data with `history.replaceState`,
   storing no token in replacement history state. No token is passed to server rendering.
2. POST `/api/v1/patient-invitations/inspect` with exactly `{token}`. This is the
   existing non-consuming inspection, not account activation or identity proof.
3. Require the existing response `{valid:true, expires_at, prototype_notice}`.
   Display the backend notice and aware expiry timestamp as escaped text. A static
   prototype-only warning is also visible independently of network success.
4. Collect the intended synthetic identity, separately delivered verification code
   and new password. Code/password fields are masked and use autocomplete-off.
5. Only an explicit form submission POSTs `/api/v1/patient-invitations/activate`
   with exactly `{token, identity, verification_code, password}`. No credentials,
   actor IDs, consent flags or approval claims are appended. The identity is normalized
   as the backend does; the separate secret code/password are not trimmed.
6. Require HTTP 201 with `{account_id, patient_id, prototype_notice}`. Clear token and
   expiry state, display success, and offer a link to the existing ordinary sign-in
   page. No automatic navigation, login, JWT creation or consent action occurs.

## Validation and failure behavior

Response parsers allowlist the actual backend response fields and check valid flags,
UUID shapes, aware parseable timestamps and bounded nonempty notices. Unknown/malformed
responses are not treated as success. Text uses ordinary escaped React rendering.

Local validation restricts identity to the existing `synthetic.invalid` pattern and
password length to 12–1024 Unicode code points, matching Python length semantics.
The password input permits up to 2048 UTF-16 code units to accommodate that upper
bound. The backend remains authoritative for invitation validity, intended identity,
code matching, expiration, account creation and atomic single-use consumption.

Invalid, expired, revoked, consumed and failed-verification invitations receive the
same generic error presentation; server error bodies and submitted secrets are never
echoed. Inspection failures do not enable the activation form or automatically retry.
Known activation 4xx rejection clears the submitted fields and allows another explicit
submission; it does not consume or automatically resubmit anything on the client.

Network failures, HTTP 5xx, unexpected success status, malformed success bodies and a
20-second activation timeout are uncertain outcomes. Stop the workflow, clear the token,
remove the form and advise ordinary sign-in or contacting the issuer before reopening
the original invitation. There is no automatic retry or resumable activation credential.
An abort or disconnect cannot prove that backend activation rolled back.

## Lifecycle and secret handling

The token is read inside an effect, never during server rendering or a GET/query
request. A capture-once ref survives development Strict Mode's repeated setup after
fragment removal. Inspection starts in a microtask guarded by the effect generation;
the discarded setup does not create a duplicate inspection. Cleanup aborts owned
requests, invalidates generations and clears the token after a real unmount while
allowing Strict Mode's immediate re-setup to retain its captured value.

Each activation sets a synchronous pending-controller guard before awaiting fetch.
Duplicate clicks cannot start a second request. Form fields are reset on submission;
secrets are held only transiently in form/request memory, never React-persisted storage.
Page hiding clears the workflow and fields, including back/forward-cache preservation.
A same-page fragment change promptly removes the replacement secret and invalidates
the old workflow instead of silently changing its patient invitation binding.

All fetches use no-store, credentials-omit, redirect-error and no-referrer options, and
send secrets only in JSON POST bodies. No Authorization header, cookies, localStorage,
sessionStorage, analytics, console logging or clinical content is introduced. Navigation
and unmount cancellation/generation checks suppress stale results. The page does not
display tokens, verification codes, passwords, account IDs or patient profile IDs.

The original invitation must be reopened after reload because the stripped token is
not recoverable from browser storage. App-level memory cleanup does not promise secure
erasure of browser internals or recall of already transmitted request bytes. The link
and code must still be delivered separately, and the existing flow is synthetic-user
research verification, not production identity assurance or practitioner credentialing.

## Validation coverage

- Focused activation Playwright: **24 passed, 0 failed, 0 skipped** (26.6 seconds).
- Full frontend Playwright: **57 passed, 0 failed, 0 skipped** (47.9 seconds), including
  all 33 existing smoke, confirmation and verified-record cases.
- Frontend lint: passed with no warnings/errors.
- TypeScript `tsc --noEmit`: passed.
- Next.js production build: passed; `/activate` renders without server-side token access.

Focused Playwright activation coverage includes valid inspection and exact activation
payloads, fragment/history removal, no secret GET/query requests, development Strict
Mode behavior, unavailable invitation states, wrong identity/code, password bounds,
synthetic-only identities, duplicate submissions, no optimistic success, uncertain
network/server/malformed/timeout outcomes, navigation while requests are pending,
same-page fragment replacement, no secret browser storage and no automatic login/consent.

Browser tests intercept API requests with synthetic fixtures matching the existing
backend schemas. They do not constitute live browser-to-FastAPI/PostgreSQL integration
validation. The separately validated 676-test backend baseline is unchanged; no backend
tests or database operations are run for this frontend-only increment.

## Remaining limitations

Production identity assurance, account recovery, invitation resending, onboarding
management UI and consent screens are outside this milestone. No clinical record,
conversation, diagnosis, prescribing or treatment behavior is added. Successful
activation must never be presented as explicit practitioner consent. Test-server
transport cancellation and committed-but-lost activation responses remain uncertain;
the user must not interpret a timeout as proof that the account was not activated.
