# Milestone 3C implementation notes

## Scope and setup

The existing Next.js/React/Tailwind frontend now provides login for existing activated accounts, a patient-scoped synthetic-text conversation, individual confirmation cards, and a separate accepted-fact view. The backend contracts, models, migrations, requirements and architecture are unchanged. No response generation, medical advice, evidence retrieval or runtime clinical verification is implemented.

Run the backend on port 8000 and `npm.cmd run dev` in `frontend`. Next.js forwards `/api/v1` to the server-only `BACKEND_ORIGIN` setting (default `http://127.0.0.1:8000`). Configure a trusted backend origin before starting/building Next.js; it must not contain credentials. This avoids cross-origin browser token handling and needs no CORS changes. Production transport, deployment hardening and identity assurance remain separate dependencies.

Login uses POST `/api/v1/auth/login` followed by GET `/api/v1/auth/me`. The role comes from the server, never a client mode selector. The existing account response does not expose a patient profile ID or patient directory: both modes explicitly enter a known synthetic patient profile UUID. This is not an authorization grant. The backend denies patients selecting another patient and practitioners without current authorization and consent.

## Cards and accepted state

POST `/patients/{id}/extractions` submits a stable, random source-message UUID and synthetic text. GET `/patients/{id}/confirmations` returns only the authenticated speaker's proposals. Each proposal renders one card with typed fields, units where present, observation timestamp, exact quote, attribution and lifecycle status. Cards are grouped by source-message UUID inside the conversation. Only individual confirm/reject endpoints are used.

A decision is never applied optimistically: after acknowledgement, both confirmations and GET `/patients/{id}/facts` reload. A 409 reloads both views instead of retrying the decision. Pending/rejected proposals are never mapped into accepted facts. If either read fails, no partial refreshed snapshot is displayed. Accepted facts are the authorized shared record; another author's transcripts/proposals are not shared. Current backend consent, original-speaker checks, atomic decisions, replay protection and release coordination remain authoritative.

In-memory synchronous request guards block duplicate submissions before disabled buttons render. Message retries reuse the same UUID for unchanged text after an uncertain extraction failure. No automatic mutation retries occur. Authorization errors use fixed client messages, never backend error bodies, and clear clinical content. Patient/speaker mismatches fail closed as defense in depth. Context switches and sign-out invalidate pending responses so late results cannot populate a different session/patient.

## Privacy and limitations

JWTs and source text are held in component memory, never localStorage/sessionStorage or URLs. Fetch uses no-store and omits cookies. No token/text/fact logging is added. Sign-out, context changes and reload clear local conversation text. Previous proposals can be reloaded only by their original speaker; only their exact supporting quotes are available because no full-transcript retrieval endpoint exists. Original full messages typed in this browser exist only for the active view. Browser-managed password autofill is independent of application storage.

This is a local synthetic-user research interface, not a production session-management design. There is no token refresh, patient directory, persisted frontend transcript, cross-participant transcript sharing, or bulk approval. The existing narrow timestamped mock grammar applies. No clinical truth or completeness claim is made.

Revocation is enforced on each backend operation and response release. Already delivered browser content cannot be retracted by a server revocation; this increment has no push notification channel. Refresh/action denial clears it. A committed decision may have its acknowledgement withheld after revocation; refresh or a replay conflict reconciles server state. The accepted record and proposal list are separate protected reads, not an atomic database snapshot. UI tests use synthetic mocked API responses; backend PostgreSQL tests remain necessary for database and concurrency guarantees. Playwright trace capture is disabled to avoid recording credentials or clinical text during interactive test runs.

## Validation

- Playwright Chromium: 15 passed, 0 failed, 0 skipped (33.5 seconds), including the original smoke test. API responses are mocked synthetic fixtures.
- Backend suite: 192 collected; 75 passed, 117 skipped, 0 failed (6.37 seconds). TEST_DATABASE_URL was unavailable in the agent process, so no PostgreSQL validation was repeated and the development database was not used. The preceding 3B PostgreSQL validation was user-supplied.
- Frontend lint, TypeScript validation and production build passed. No dependencies were added.
