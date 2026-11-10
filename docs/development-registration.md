# Explicit local synthetic registration

Registration is disabled by default. It requires all of:
`APP_ENV=development`, `ENABLE_DEMO_REGISTRATION=true`, `LLM_PROVIDER=mock`, and
the same strict localhost:5432/clincoherence_db URL guard as the CLI. Database
identity, loopback server address and port are checked again before insertion.
The API also rejects non-loopback clients and browser origins other than the local
port-3000 website. These checks supplement, rather than replace, the opt-in mode
and database restrictions. Keep both servers bound to loopback.
Do not deploy this opt-in bootstrap on any remotely exposed server, or enable it
against a database containing real patient information. These flags are an explicit
operator authorization for synthetic prototype eligibility, not credential verification.

In a new PowerShell window, stop the existing backend, then launch with:

```powershell
Set-Location C:\Users\91895\clincoherence\backend
$env:APP_ENV = 'development'
$env:ENABLE_DEMO_REGISTRATION = 'true'
$env:LLM_PROVIDER = 'mock'
.\.venv\Scripts\python.exe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --no-access-log
```

Launch Next.js bound to localhost in another terminal:

```powershell
Set-Location C:\Users\91895\clincoherence\frontend
$env:BACKEND_ORIGIN = 'http://127.0.0.1:8000'
npm.cmd run dev -- --hostname localhost --port 3000
```

Visit http://localhost:3000 and choose Create Account (or open /register).
Use `doctor-demo@synthetic.invalid` or another synthetic.invalid identity; enter
and confirm a private password, acknowledge the unverified-demo warning, and submit
once. Sign in ordinarily after success. No JWT is issued by registration.
Patient accounts remain invitation-only; explicit patient consent is still required.

The strict POST /api/v1/auth/demo-registration request accepts only identity,
password, password_confirmation and acknowledge_unverified_demo=true. Client role,
authorization and patient fields are rejected. GET reports availability only.
Duplicates return a generic conflict without updating the existing account. Existing
passwords are never reset. Account and profile creation share the existing transaction;
the function-scoped database dependency commits before a success response is sent.
Connection/commit failures return a generic unavailable result, never a confirmed success.
no patient, consent, migration or clinical data is created. The profile's is_authorized
flag denotes prototype eligibility only, as in the existing authentication contract.
Self-registered identities are unverified demos, not verified practitioners.

Password creation now accepts 8–1024 Unicode code points in activation, registration
and the CLI; hashing is unchanged. Login still verifies existing passwords normally.
The UI clears secrets on submit, uses memory only, cancels requests on unmount, and
never automatically retries an uncertain write. If the response is lost, first try
ordinary sign in; a successful commit may already have occurred.

Disable registration after use by stopping the backend, removing the two opt-in
environment variables from that terminal, and restarting:

```powershell
Remove-Item Env:ENABLE_DEMO_REGISTRATION -ErrorAction SilentlyContinue
Remove-Item Env:APP_ENV -ErrorAction SilentlyContinue
```

The provisioning failure was caused by comparing inet_server_addr()::text, which
returned ::1/128, to ::1. The corrected query uses host(inet_server_addr()) to remove
network notation without weakening address or database restrictions. Read-only
diagnosis found no doctor-demo account; provisioning was not rerun. No accounts were
modified by implementation/testing. Offline API/browser mocks are not live integration
validation; PostgreSQL tests remain opt-in against the dedicated test database only.
