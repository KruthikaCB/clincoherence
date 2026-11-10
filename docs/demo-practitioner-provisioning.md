# Local synthetic practitioner provisioning

This explicit operator command is for the local research development database only.
There is no registration endpoint or production credential verification. The fixed
login identity is `doctor-demo@synthetic.invalid`. The command creates an active
practitioner account and an authorized prototype practitioner profile using the
existing ORM and password hasher. It creates no patient, invitation or consent.

After applying the existing migrations to the development database, run from an
interactive PowerShell terminal:

```powershell
Set-Location C:\Users\91895\clincoherence\backend
.\.venv\Scripts\python.exe -B -m app.provision_demo
```

Enter and confirm a private password of 12–1024 characters at the hidden prompts.
Do not put it in command arguments, source files, chat or environment variables.
Then sign in at http://localhost:3000 using the fixed identity and your password.
Use the existing invitation/activation workflow to create a synthetic patient;
the patient must explicitly grant practitioner consent afterwards.

The effective application DATABASE_URL (including environment overrides) must use
`postgresql+asyncpg`, literal `localhost`, explicit port `5432`, database
`clincoherence_db`, credentials, and no URL query parameters. Loopback server
address, server port and current_database() are independently checked before any
insertion. Test databases, remote targets and alternate hosts are refused.

Account/profile insertion is one transaction. Existing accounts are never updated;
duplicates fail, including concurrent duplicates through the existing unique
constraint. No migration or schema initialization is performed. SQL parameter
logging is disabled; failures are sanitized. On uncertain connection/commit failure,
success is not asserted: an account may have committed. Do not reset or overwrite
an existing account; verify login/status locally before another attempt.

This is an explicit local bootstrap privilege, not practitioner credential assurance.
Database naming and loopback checks do not establish the provenance of a local
database: operators must keep this database synthetic and local. Offline tests
cover guards, password handling, hashing, duplicates and minimal ORM writes; actual
development provisioning is deliberately left to the operator.
