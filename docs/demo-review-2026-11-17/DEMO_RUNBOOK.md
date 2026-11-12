# ClinCoherence capstone — November 17, 2026

Status: **LIVE DESKTOP REHEARSAL PASSED — November 12, 2026.** See `LIVE_REHEARSAL_REPORT.md` for the actual results and prepared patient IDs. Use a browser wider than 680 pixels; narrow layouts hide patient navigation.

This demo uses existing schema through 0010 and a read-only derived synthetic medication assessment. Do not run migrations. Keep migration 0011 and clinician-review routes disabled. The initial implementation was offline; a separately approved rehearsal subsequently verified schema 0010 and populated only the two synthetic demo patients.

The controlled rehearsal services are running on frontend 3000 and backend **8001** because 8000 was occupied. For restarting this rehearsed setup, use `--port 8001` in the backend command below and `http://127.0.0.1:8001` for `BACKEND_ORIGIN`. Leave the existing port-8000 process untouched. Do not start duplicate servers on ports already in use.

## Prerequisites and approval boundary

- Existing backend virtual environment and frontend dependencies installed; PostgreSQL application's existing service available; schema 0010 verified by read-only queries before and after the approved November 12 rehearsal.
- Existing authorized practitioner account, with an identity ending `@synthetic.invalid` for synthetic demo ownership. Know its credentials privately; never project them.
- Existing correct database/signing configuration. These launch commands use that configuration; do not paste credentials or change `.env` files. The agent has not inspected it.
- The user explicitly approved and completed the bounded November 12 database-backed rehearsal for A and B. Preserve those records and that authorization scope; unrelated records, migrations and broader data changes remain prohibited. Existing features can persist clinical/audit records even where the new assessment itself does not.
- The existing synthetic-owner path requires development mode, its explicit enable flag, and the local application's `clincoherence_db` on loopback port 5432. It does not use the paused temporary cluster. Never operate that cluster or its PID file.
- Use mock extraction for a repeatable capstone. Ollama remains available in existing code but is not required for this route. Do not call mock extraction an actual LLM inference.

## Launch — after rehearsal database access is approved

Open a dedicated PowerShell terminal for the backend:

```powershell
Set-Location 'C:\Users\kavan\clincoherence\backend'
$env:APP_ENV = 'development'
$env:ENABLE_DEMO_PATIENTS = 'true'
$env:ENABLE_CLINICIAN_FACT_REVIEW = 'false'
$env:ENABLE_DEMO_REGISTRATION = 'false'
$env:LLM_PROVIDER = 'mock'
& '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open a second dedicated PowerShell terminal:

```powershell
Set-Location 'C:\Users\kavan\clincoherence\frontend'
$env:BACKEND_ORIGIN = 'http://127.0.0.1:8000'
$env:NEXT_TELEMETRY_DISABLED = '1'
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Open `http://localhost:3000` (or `http://127.0.0.1:3000`). Do not reuse the offline test build for a live demo: its backend rewrite intentionally targets port 1. If using production mode, rebuild normally with the approved backend origin before `npm run start`; the commands above avoid that dependency.

`http://127.0.0.1:8000/health` checks the API process only, not database readiness. Neither command performs a migration. Stop only these terminal applications using Ctrl+C; close the dedicated terminals to discard their process-scoped overrides. Do not stop PostgreSQL or Ollama services as part of this runbook.

## Synthetic setup and rehearsal

1. After approval, log in with the existing synthetic practitioner. Use the patient manager's **+ Add Synthetic Demo Patient** and **Create Patient** to create `capstone-20261117-a` and `capstone-20261117-b`, or select approved existing matching records. Never reuse real patient records.
2. For A, paste `PATIENT_A_NOTE.txt` exactly into **Clinical note** and submit once. Expect six proposals. Accept diagnosis, both HbA1c values, metformin and eGFR. Reject the deliberately incorrect fatigue proposal. Keep the space before `%`.
3. For B, paste `PATIENT_B_NOTE.txt`. Expect four proposals; accept all four. Medication use and other history remain UNKNOWN, not negative findings.
4. Prepopulate B before the live presentation. Rehearse A's confirmation flow once; use fresh approved synthetic identifiers for a later live extraction run to avoid duplicate observations. Do not reset or delete records.
5. Verify History, Medications, Summary and Verified Records. Request the new assessment and compare with `MEDICATION_RULES.md`. Switch patients while an assessment is pending and confirm that the old cards disappear.
6. Capture the synthetic-only screenshots listed in `DEMO_BACKUP_PLAN.md`. The November 12 live captures and results are now available in `live-rehearsal-2026-11-12/` and `LIVE_REHEARSAL_REPORT.md`; confirm presentation quality before projecting.

## Troubleshooting

- Login fails: check the existing account/configuration privately. Do not reset accounts or enable registration as an unreviewed workaround.
- No synthetic create button / 403: verify authorized demo owner, development mode and existing local database guard; never bypass authorization or consent.
- No proposals: use the exact supplied note; mock parser requires explicit labels, units and ISO timestamps.
- Assessment 409: ordinary non-demo records, malformed/mixed-origin history or inconsistent versions are deliberately refused. Select an approved owned synthetic record.
- Evidence unavailable: do not edit the hash to make a result appear. Explain that checks are withheld and review the catalog/source change separately.
- M8 routes return 404: expected. Do not enable them or apply 0011.
- Expired session: sign in again; token is memory-only. Do not refresh or sign out mid-demo unless demonstrating this behavior.

## Safe offline checks

From `backend`: `.\.venv\Scripts\python.exe -m tests.offline_runner tests/test_medication_suitability.py tests/test_clinical_fact_reviews.py -q -rs`.

From `frontend`: `node scripts/offline-check.cjs types`, then `node scripts/offline-check.cjs build`, then `node scripts/offline-check.cjs test`. These browser tests use already installed Google Chrome (no browser download). `node scripts/offline-check.cjs test-panels` selects only suitability and Verified Records tests. The launcher strips inherited application configuration, disables dotenv loading, pins the backend rewrite to loopback port 1 and runs mocked browser tests on 3107. The test server is not the live application backend. Do not run build and browser checks concurrently because both use `.next`.
