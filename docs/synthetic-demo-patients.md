# Creator-owned Synthetic Demo Patients

This opt-in local workflow creates an accountless synthetic record, not an activated
patient identity. No user, password, invitation, activation, verification timestamp,
consent or consent-history event is created. Ordinary patient onboarding, explicit
patient consent, and patient-only consent management are unchanged.

Migration `0009_demo_patients`, following `0008_evidence_release`, adds a persistent
is_demo marker, creator practitioner-profile FK, and synthetic identifier. Existing
profiles default to non-demo and retain their original activation constraints. Demo
profiles require no account or activation timestamps, but must be active, have a
creator, and name that same creator as initiator. A database UPDATE trigger prevents
changing the marker, creator or identifier, including converting ordinary profiles.
Identifiers are unique within each creator's scope. FK deletion is restricted.
Downgrade refuses if any demo profiles exist; it never deletes clinical history.

## Enable locally

Stop backend/frontend servers before applying migrations. Run yourself (implementation
does not migrate or provision the development database):

```powershell
Set-Location C:\Users\91895\clincoherence\backend
.\.venv\Scripts\python.exe -B -c 'from app.core.config import settings; from app.provision_demo import validate_target; validate_target(settings.DATABASE_URL); print("Development target verified")'
if ($LASTEXITCODE -ne 0) { throw 'Unexpected database target' }
.\.venv\Scripts\python.exe -B -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Migration failed; stop' }
.\.venv\Scripts\python.exe -B -m alembic current
$env:APP_ENV = 'development'
$env:ENABLE_DEMO_PATIENTS = 'true'
$env:LLM_PROVIDER = 'mock'
.\.venv\Scripts\python.exe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --no-access-log
```

Expected head: `0009_demo_patients`. Keep the existing development DATABASE_URL
pointing exactly to localhost:5432/clincoherence_db; no query overrides or aliases.
The connected database name, server loopback address and server port are independently
verified before creator access, including fresh verification/release checks.

In a separate terminal:

```powershell
Set-Location C:\Users\91895\clincoherence\frontend
$env:BACKEND_ORIGIN = 'http://127.0.0.1:8000'
npm.cmd run dev -- --hostname localhost --port 3000
```

Sign in at http://localhost:3000 with an eligible synthetic demo practitioner. Choose
Add Patient, enter an invented identifier such as demo-patient-1, then Create Patient.
The committed record is immediately selected and labeled Synthetic Demo Patient.
Use the existing individually confirmed synthetic extraction workflow, history,
corrections and verified record questions. The prototype still provides record
restatements only, never diagnosis or treatment advice.

## Boundaries and tests

GET/POST /api/v1/demo-patients require authentication plus explicit local mode, mock
provider, validated local development database, loopback client and local browser
origin. Creation accepts only a synthetic identifier; ownership is resolved from
the authenticated, currently eligible practitioner and locked through commit.
Registration has its own independent ENABLE_DEMO_REGISTRATION switch.

GET /patients remains advisory and minimal; it additionally discovers only the
creator's demo UUIDs while mode is enabled. Labels come from the creator-scoped demo
endpoint. Every clinical read, write, verification, audit and protected release still
calls the shared patient authorization service and retains patient-then-practitioner
locks. No other practitioner gains access, even through an unrelated consent row.
Disabling demo mode or creator eligibility denies future demo operations; it does not
delete history or recall already viewed information. Restart the backend without
ENABLE_DEMO_PATIENTS to disable. Do not hot-toggle environment settings during work.

The UI clears pending creation work on context reset/sign-out, prevents duplicate
submits and never automatically retries uncertain outcomes. Refresh discovery after
a lost acknowledgement; an identifier conflict never updates an existing record.
Browser state stays in memory. Synthetic identifiers are not patient names; no real
patient information is permitted. Database-owner privileges can defeat triggers;
this feature is not a production identity/consent mechanism.

Offline tests validate opt-in gates, input allowlists, generated migration SQL and
full migration/ORM schema parity. PostgreSQL ownership, immutability, clinical writes,
normal consent isolation and release revalidation tests require the explicitly
configured dedicated test database; they never fall back to development. No guarded
live suite is run as part of this change.
