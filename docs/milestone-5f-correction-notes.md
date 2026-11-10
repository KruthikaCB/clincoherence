# Milestone 5F-C — Structured Clinical Corrections UI

## Contract and workflow

The existing Clinical History panel offers **Correct record** for the current accepted version in the user's attribution category. It first fetches and validates fresh correction history. A newer current version causes a refresh rather than opening an editor against stale state.

The structured form supports all existing backend payloads: laboratory, diagnosis, medication, symptom and history. Cross-kind and cross-code replacements remain permitted by the existing backend contract. The form collects a replacement payload, timezone-aware `observed_at`, provenance (`source`, nullable `reference`, `data_origin`), the read-only `expected_version_id`, and a nonempty `correction_reason`. Laboratory values and recorded dose values remain strings, including trailing decimals; no measurement arithmetic or unit conversion is performed.

Review and submission are separate explicit actions. Editing fields invalidates the review. Submission posts only those five allowlisted fields to `/api/v1/patients/{patient_id}/facts/{fact_id}/corrections`. A synchronous one-shot guard prevents duplicate submissions. Structured entries use the existing authorized validation boundary; no LLM fact is automatically confirmed.

## Authorization and history

Patients can prepare corrections to patient-reported facts; practitioners can prepare corrections to practitioner-managed facts, including facts entered by another practitioner. There is no original-author-only check. These controls are presentation only: the existing backend independently enforces account eligibility, patient scope, current consent, version conflicts and correction locking.

The acknowledgement is validated for patient, root, predecessor, new version, author, attribution and structured entry method. It is never inserted optimistically into the accepted list. Current facts and confirmations are reloaded, followed by the relevant history. Prior versions retain their authors, provenance and superseded labels. Version numbers need not be consecutive and observation timestamps need not be monotonic.

Previously displayed Verified Records are unmounted as soon as correction processing starts and are not restored after it finishes. New restatements require a new explicit verified-record request.

## Failure and privacy behavior

- HTTP 409 reloads current state and history; another attempt requires reopening fresh history, review and explicit submission with the new expected version.
- Network failures, timeouts, malformed acknowledgements and other uncertain outcomes may follow a committed write. The UI states this explicitly and performs only recovery reads, never an automatic write retry. Opening another correction always requires a new history check.
- Failed recovery or authorization failures clear the protected patient context. Server error bodies are not displayed. A denied acknowledgement cannot prove that an earlier write did not commit.
- The workspace owns the mutation independently of the editor's lifetime. Patient switching, sign-out and authorization denial abort context requests and invalidate response epochs; late results cannot repopulate a different context. Editor/history reads also cancel on unmount.
- Tokens and clinical information remain in component memory. No new browser storage, cookies, URLs, logs, analytics, transcript retrieval or persistent drafts are introduced. Provenance references are displayed as metadata, not fetched as private transcripts.

## Validation and limitations

Focused mocked Playwright coverage includes both role categories, different practitioner authors, all replacement kinds, code changes, exact decimal strings, explicit review, validation, stale versions, uncertain acknowledgements, duplicate clicks, revocation, cross-patient responses, switching/sign-out, preserved history and Verified Records invalidation. Existing frontend regressions, ESLint, TypeScript and production build are run separately.

Validation: focused correction Playwright suite **24 passed, 0 failed, 0 skipped**; full mocked frontend suite **140 passed, 0 failed, 0 skipped**. ESLint, TypeScript checks and the production build passed.

This milestone does not run the guarded live browser-to-FastAPI/PostgreSQL suite. Mock tests do not prove database concurrency or authorization. Existing backend enforcement remains authoritative. Separate list/history reads are not an atomic snapshot; changes detected during history retrieval trigger refresh, and subsequent writes still require server-side expected-version validation. There is no push notification for revocation or concurrent correction: protected requests reauthorize, and already viewed information cannot be recalled. An uncertain write is not idempotently retried by the frontend.

No backend, migration, endpoint, clinical threshold, treatment recommendation, dataset integration, foundational requirements or architecture changes are included.
