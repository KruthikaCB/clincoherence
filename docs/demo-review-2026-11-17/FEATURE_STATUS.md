# Feature status — November 12, 2026

**READY for the rehearsed desktop synthetic demonstration.** The separately approved live rehearsal passed on November 12; database identity and schema 0010 were confirmed before and after. See `LIVE_REHEARSAL_REPORT.md` for actual results, patient IDs and remaining presentation limitations. This does not establish clinical or production readiness.

| Feature | Status and limits |
|---|---|
| Authentication, patient selection, notes, Accept/Reject, history/corrections, medications | Existing functionality preserved; approved live login, synthetic creation, extraction, confirmation, history/provenance and medications passed. No live correction was made. |
| Summary and Verified Records | Live summaries, verified restatement and grounded questions passed. Record grounding is not clinical approval. |
| Synthetic medication assessment | New read-only endpoint and three cards, deterministic rules, exact patient/version provenance and missing-data handling. No migration. |
| Clinical evidence | Three source-verified DailyMed versions, pinned local catalog; independent clinical review pending. No ADA integration activated. |
| M8 clinician reviews | Default-off route gate; preserved code and unapplied 0011. PostgreSQL validation remains paused. |
| Real-patient medication recommendations | Disabled by design: endpoint refuses non-demo patients. |
| Live demo dataset and screenshots | Two approved synthetic patients populated through UI; actual captures and a live report saved. |

## Validation ledger

- Relevant backend regression run: **343 passed, 99 skipped, 0 failed**, one warning. Includes 33 initial new assessment/gate tests and existing fact, consent, extraction, consistency, summary, verification and M8 coverage.
- The 99 skips are explicit PostgreSQL integration tests, including all 17 M8 PostgreSQL tests. They do not count as passes. Automated DB lock/concurrency, trigger, migration and authorization/consent integration suites remain unexecuted. The later approved live rehearsal confirmed its bounded synthetic-owner workflow only.
- Warning: `asyncio_mode` is configured but pytest-asyncio is not installed; the offline runner executes coroutine tests using the installed AnyIO asyncio backend. No packages installed.
- Frontend TypeScript and optimized production build: passed (build includes lint/type validation). TypeScript was checked again after final test edits and passed.
- Final focused assessment suite: **35 passed, 0 skipped, 0 failed**, one warning of the same kind. Includes the two exact A/B note fixtures through mock parsing, grounding and deterministic assessment. These overlap the 33 assessment tests in the broader run; do not sum both run totals as unique tests.
- Initial browser attempts could not launch because Playwright's bundled Chromium was absent. A fail-fast diagnostic reported one infrastructure failure and 109 tests not run; an earlier attempt was interrupted after repeated identical startup failures. No application assertions executed in those attempts. The dedicated config now uses already installed Chrome; no download/install was performed.
- First Chrome regression run: **81 passed, 1 failed, 29 not run**. The failure was a new test's ambiguous alert selector matching both the application error and Next.js's hidden route announcer. The application correctly withheld the response. The selector was scoped to the panel.
- Final targeted Chrome run: **33 passed, 0 failed, 0 skipped**, covering all 15 new suitability tests and 18 existing Verified Records tests. Combined with the 78 existing correction/history/summary tests already passed, **111 distinct browser tests passed**. No unresolved test failure remains. There was no unnecessary rerun of the already-passing 78 tests.
- Mocked browser coverage includes wrong-patient facts/results, unsupported positive verdicts, missing evidence, authorization errors, A-to-B clearing/results, pending-response cancellation and patient-role exclusion. Test screenshots were visually inspected; they are synthetic mocked UI captures, not a live clinical rehearsal.

Earlier 1,029/256 full-suite numbers are historical, not a fresh claim. No unnecessary full-suite rerun was made. Offline tests block asyncpg and HTTP transports, disable dotenv loading and use synthetic objects. Browser tests mock the API and use an inert backend rewrite; they do not prove live database integration.

## Remaining priorities after live rehearsal

1. Use the two now-prepared synthetic patients, desktop-width navigation and the rehearsed port-8001 backend; avoid resubmitting the same notes or touching unrelated records.
2. Have a clinician/supervisor review the narrow rule scope and source catalog, and rehearse the nine-minute script before November 17.
3. Review the saved live screenshots and, if needed, recapture them at presentation quality. After capstone, separately resume M8 database validation and durable clinician-approved decision support; do not enable it for this demo.

## Change manifest for this capstone work

Paths are relative to `C:\Users\kavan\clincoherence`.

Modified existing files:

- `backend/app/core/config.py`: default-off M8 switch.
- `backend/app/main.py`: conditional M8 route registration.
- `backend/app/api/v1/router.py`: derived medication API registration; M8 registration moved to the gated app factory.
- `backend/pyproject.toml`: include the new catalog in packaged application data.
- `backend/tests/test_clinical_fact_reviews.py`: explicit test-only opt-in for existing M8 route tests (this file was already uncommitted before capstone work).
- `frontend/src/components/conversation.tsx`: practitioner panel/navigation, scoped context, corrected mock-note example spacing.

Created:

- `backend/app/api/v1/medication_suitability.py`
- `backend/app/schemas/medication_suitability.py`
- `backend/app/services/medication_suitability.py`
- `backend/app/fixtures/medication_suitability/catalog.v1.json`
- `backend/tests/test_medication_suitability.py`
- `frontend/src/components/medication-suitability.tsx`
- `frontend/src/components/medication-suitability.module.css`
- `frontend/tests/medication-suitability.spec.ts`
- `frontend/playwright.demo.config.ts`
- `frontend/scripts/offline-env.cjs`
- `frontend/scripts/offline-check.cjs`
- This directory's seven review Markdown documents and `PATIENT_A_NOTE.txt`, `PATIENT_B_NOTE.txt`.

Build/test tools also generate normal ignored `.next`, cache and test-result artifacts. Mocked-browser screenshots are software-test evidence only. The later approved live captures are clearly separated in `live-rehearsal-2026-11-12/`. Preexisting M8 model/migration/service/harness edits remain intact; no migration, Git commit/push/merge/reset, environment-file modification or temporary PostgreSQL recovery was performed. Approved live database operations are recorded separately in `LIVE_REHEARSAL_REPORT.md`.
