# Disposable PostgreSQL validation — recovery plan awaiting approval

The disposable cluster was initialized successfully during the approved first
attempt. PostgreSQL reached readiness, but the startup wrapper waited for its
process tree and never reached SQL identity verification or test-database creation.
The wrapper was interrupted. The temporary server is stopped; the directory is
retained. No migration or database test has run. This revised recovery/startup
procedure has NOT been executed and requires fresh approval.

Read-only recovery inspection confirmed: recorded PID `22536` is absent,
`pg_ctl status` returns 3 (no server running), and an exclusive TCP bind to
`127.0.0.1:55439` succeeds and is released. `postmaster.pid` still records `ready`
and the log has no clean-shutdown record. These files are preserved unchanged.
Repeat the checks before any later attempt; this observation is not a future
permission or liveness guarantee.

## Exact target

- PostgreSQL binaries: `C:\Program Files\PostgreSQL\18\bin` (18.6 installed).
- Already initialized cluster directory: `C:\Users\kavan\clincoherence\.m8-pg-disposable-20261112`.
- Cluster marker: `cc_m8_disposable_20261112`.
- Listen address/port: **127.0.0.1:55439**; no Windows service registration.
- Test database: **clincoherence_db_test** (the existing fixture requires this name).
- Test role: **cc_m8_test**, not a superuser and unable to create roles/databases.
- Bootstrap role: **cc_m8_bootstrap**, confined to this new cluster.
- Password-free synthetic-test URL:
  `postgresql+asyncpg://cc_m8_test@127.0.0.1:55439/clincoherence_db_test`.

No existing server is queried or configured. No existing credentials, dotenv,
password files, service files, application data, backups or database copies are used.

The previously approved initialization created empty default databases
(`template0`, `template1`, and `postgres`) inside this disposable cluster. Do not
run `initdb` again. The plan never connects to its `postgres` database; the access
rules reject it. No existing database named `postgres` is touched. Bootstrap SQL
connects only to this cluster's `template1` after startup gates pass.

## Existing PID-file gate

An existing `postmaster.pid` is inspected, never deleted, renamed, overwritten or
automatically dismissed. Validate its data directory and endpoint, and check the
recorded OS PID. A live or reused PID, unreadable file, access denial, unexpected
status or inconsistent fields stops the procedure. Only an explicit process-not-
found result counts as absence; a free port alone is insufficient.

**Current execution blocker:** even with the recorded PID absent, status 3 and a
free port, this plan refuses startup while the PID file exists. Separate approval
is needed to resolve/preserve that stale file or authorize PostgreSQL's own stale-
file handling. This plan does neither automatically and does not bypass this gate.
It includes no PID-file removal, rename, force-kill, reset or reinitialization command.

## Reuse preflight and bounded startup (PowerShell; not run)

Use only this already initialized directory, not another cluster or a fresh one.
Run sequentially and stop on any failure. The port probe releases its listener in
`finally` before returning. After the PID-file gate passes, repeat the stopped
status/PID-file checks and bind probe immediately before startup. Do not regard a
timeout as evidence that PostgreSQL stopped: inspect the exact temporary cluster
read-only and report uncertainty; do not retry, proceed to SQL, or kill processes.

```powershell
$ErrorActionPreference = 'Stop'
$pgBin = 'C:\Program Files\PostgreSQL\18\bin'
$repoRoot = 'C:\Users\kavan\clincoherence'
$pgData = [System.IO.Path]::GetFullPath((Join-Path $repoRoot '.m8-pg-disposable-20261112'))
if ($pgData -cne 'C:\Users\kavan\clincoherence\.m8-pg-disposable-20261112' -or
    [System.IO.Path]::GetDirectoryName($pgData) -cne $repoRoot) {
    throw 'Disposable directory mismatch'
}
if (-not (Test-Path -LiteralPath $pgData -PathType Container)) {
    throw 'Initialized cluster missing; do not initialize a replacement'
}
if ((Get-Content -LiteralPath "$pgData\PG_VERSION" -Raw).Trim() -ne '18') {
    throw 'Unexpected cluster version'
}
function Assert-DisposablePortAvailable {
    $portProbe = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Parse('127.0.0.1'), 55439
    )
    try {
        $portProbe.ExclusiveAddressUse = $true
        $portProbe.Start(1)
    }
    catch {
        throw "Cannot bind disposable endpoint 127.0.0.1:55439: $($_.Exception.GetBaseException().Message)"
    }
    finally {
        $portProbe.Stop()
    }
}
# Prevent inherited libpq settings or default password/service files being used.
foreach ($key in @('PGPASSWORD','PGSERVICE','PGSERVICEFILE','PGOPTIONS','PGHOSTADDR')) {
    [Environment]::SetEnvironmentVariable($key, $null, 'Process')
}
$env:PGPASSFILE = "$pgData\unused-no-password-file"
if (Test-Path -LiteralPath $env:PGPASSFILE) { throw 'Unexpected password file' }

# Verify the existing access rules without rewriting any cluster file.
$expectedHba = @'
host template1 cc_m8_bootstrap 127.0.0.1/32 trust
host clincoherence_db_test cc_m8_test 127.0.0.1/32 trust
host all all 0.0.0.0/0 reject
host all all ::/0 reject
'@
$actualHba = (Get-Content -LiteralPath "$pgData\pg_hba.conf" -Raw).Replace("`r`n", "`n").Trim()
if ($actualHba -cne $expectedHba.Replace("`r`n", "`n").Trim()) {
    throw 'Temporary cluster access rules changed; do not overwrite them'
}

$pidFile = Join-Path $pgData 'postmaster.pid'
if (Test-Path -LiteralPath $pidFile) {
    $pidLines = @(Get-Content -LiteralPath $pidFile)
    $recordedPid = 0
    if ($pidLines.Count -lt 6 -or
        -not [int]::TryParse($pidLines[0], [ref]$recordedPid) -or $recordedPid -le 0) {
        throw 'Invalid temporary PID file'
    }
    if ([System.IO.Path]::GetFullPath($pidLines[1]).TrimEnd('\') -ine $pgData -or
        $pidLines[3].Trim() -ne '55439' -or $pidLines[5].Trim() -ne '127.0.0.1') {
        throw 'PID-file cluster identity mismatch'
    }
    try {
        $recordedProcess = [System.Diagnostics.Process]::GetProcessById($recordedPid)
        $recordedProcess.Dispose()
        throw "Recorded PID $recordedPid is in use; do not act on that process"
    }
    catch [System.ArgumentException] {
        Write-Output "Recorded PID ${recordedPid}: absent"
    }
}

& "$pgBin\pg_ctl.exe" -D $pgData status
if ($LASTEXITCODE -ne 3) { throw 'Stopped status not established; do not proceed' }
Assert-DisposablePortAvailable
if (Test-Path -LiteralPath $pidFile) {
    throw 'Existing PID file preserved; separate stale-file recovery approval required'
}

# Final checks immediately before starting; never start through an existing PID file.
& "$pgBin\pg_ctl.exe" -D $pgData status
if ($LASTEXITCODE -ne 3) { throw 'Stopped status changed; do not proceed' }
if (Test-Path -LiteralPath $pidFile) { throw 'PID file appeared; do not start' }
Assert-DisposablePortAvailable
# Wait only for the pg_ctl process, never for its PostgreSQL descendants.
$pgStart = Start-Process -FilePath "$pgBin\pg_ctl.exe" -WindowStyle Hidden -PassThru -ArgumentList @(
    '-D', $pgData, '-l', "$pgData\server.log", '-w', '-t', '30', 'start',
    '-o', '"-h 127.0.0.1 -p 55439 -c cluster_name=cc_m8_disposable_20261112 -c lock_timeout=5s -c statement_timeout=60s"'
)
if (-not $pgStart.WaitForExit(45000)) {
    throw 'pg_ctl exceeded outer timeout; server state unknown; no SQL, retry or force-kill permitted'
}
$pgStart.Refresh()
if ($null -eq $pgStart.ExitCode -or $pgStart.ExitCode -ne 0) {
    throw 'Temporary cluster startup failed or timed out; no database commands permitted'
}

# ONE psql process/session: assert identity, then create the role and database.
# No reconnect, separate creation invocation, or transaction around CREATE DATABASE.
# Single-quoted PowerShell here-string preserves SQL dollar quoting literally.
@'
\set AUTOCOMMIT on
DO $identity$
BEGIN
    IF current_database() IS DISTINCT FROM 'template1'
       OR session_user IS DISTINCT FROM 'cc_m8_bootstrap'
       OR inet_server_addr() IS DISTINCT FROM '127.0.0.1'::inet
       OR inet_server_port() IS DISTINCT FROM 55439
       OR current_setting('cluster_name') IS DISTINCT FROM 'cc_m8_disposable_20261112'
       OR lower(rtrim(replace(current_setting('data_directory'), chr(92), '/'), '/'))
          IS DISTINCT FROM 'c:/users/kavan/clincoherence/.m8-pg-disposable-20261112'
    THEN
        RAISE EXCEPTION 'Refusing bootstrap: temporary cluster identity mismatch';
    END IF;
END;
$identity$;
CREATE ROLE cc_m8_test LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE clincoherence_db_test OWNER cc_m8_test TEMPLATE template0;
'@ | & "$pgBin\psql.exe" -X -w -h 127.0.0.1 -p 55439 -U cc_m8_bootstrap -d template1 -v ON_ERROR_STOP=1
if ($LASTEXITCODE -ne 0) { throw 'Verified bootstrap failed; no tests or automatic retry permitted' }
```

The identity block aborts on any mismatch (including NULL); the data directory is
compared after normalizing Windows path separators, case and a trailing slash.
`ON_ERROR_STOP=1` prevents either creation statement after a failed assertion.
All three statements use the same connection. A failed creation stops the sequence;
there is no automatic retry or cleanup of a partially completed bootstrap.

`Start-Process -Wait` waits for the process tree, explaining the prior wrapper
stall after PostgreSQL became ready. The replacement uses `pg_ctl -w -t 30` and
an outer `WaitForExit(45000)` on only the controller process. A PostgreSQL operation
can continue after a controller timeout, so neither timeout nor a free port is
treated as proof of shutdown. Sources: [PowerShell Start-Process](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/start-process?view=powershell-7.5),
[PostgreSQL pg_ctl](https://www.postgresql.org/docs/18/app-pg-ctl.html),
[Process.WaitForExit](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.process.waitforexit?view=net-9.0).

Loopback trust is limited to the named new roles/databases and used only for
synthetic tests. Other local processes could connect to this temporary test
server; it must never contain private data and will be stopped after testing.

## Guarded test command (not run)

The following inline launcher is intentionally separate from `offline_runner.main`:
the offline runner must continue to deny all database connections. This launcher
allows only the exact new test target, verifies cluster identity on every real
connection, suppresses dotenv/password files and blocks external HTTP transports.
No settings are written to `.env` or persistent environment variables.

The approved absolute interpreter runs in isolated mode (`-I`), ignoring inherited
Python environment settings and user-site packages. The launcher explicitly adds
only the approved backend checkout to the import path and pins its working
directory, pytest configuration, collection root and test path. The existing
`offline_runner` import can reuse the already installed system pytest as documented;
it does not call `offline_runner.main` or load application settings.

Before pytest is imported, inherited `PYTEST_ADDOPTS` and `PYTEST_PLUGINS` are
removed and `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` is set. Apart from pytest's built-in
plugins, the only explicitly loaded plugins are the reviewed `anyio.pytest_plugin`,
the repository's `tests.database`, and `OfflinePlugin` (which selects the asyncio
backend). The reviewed `tests/conftest.py` also names `tests.database`; pytest
reuses that registration. No pytest-asyncio, Hypothesis or other installed plugin
is automatically selected. AnyIO auto mode is explicit, not environment-dependent.

Run first with `test_path = backend / "tests/test_clinical_fact_reviews.py"` as
shown. Only after exit code zero, rerun the same launcher with the single change
`test_path = backend / "tests"` for the full backend regression suite. Both targets
remain absolute and within the approved checkout. Every nonzero Python exit code
throws immediately in the invoking PowerShell session; do not continue to the
regression run after a failure.

```powershell
$ErrorActionPreference = 'Stop'
@'
import os
import secrets
import sys
from pathlib import Path
from unittest.mock import patch

# Sanitize before importing pytest, plugins, or repository modules.
for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
    os.environ.pop(key, None)
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
backend = Path(r"C:\Users\kavan\clincoherence\backend")
approved_python = backend / ".venv/Scripts/python.exe"
if not sys.flags.isolated or Path(sys.executable).resolve() != approved_python.resolve():
    raise SystemExit("Refusing unapproved or non-isolated Python interpreter")
os.chdir(backend)
sys.path.insert(0, str(backend))

import asyncpg
import httpx
from pydantic_settings.sources import DotEnvSettingsSource
from tests.offline_runner import OfflinePlugin, deny_connection, deny_async_connection
import pytest

target = "postgresql+asyncpg://cc_m8_test@127.0.0.1:55439/clincoherence_db_test"
unused_passfile = r"C:\Users\kavan\clincoherence\.m8-pg-disposable-20261112\unused-no-password-file"
if Path(unused_passfile).exists():
    raise SystemExit("Refusing unexpected password file")
for key in ("PGPASSWORD", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGHOSTADDR"):
    os.environ.pop(key, None)
os.environ.update(
    TEST_DATABASE_URL=target,
    DATABASE_URL="postgresql+asyncpg://offline:offline@127.0.0.1:1/offline_unused",
    SECRET_KEY=secrets.token_urlsafe(48),
    APP_ENV="production", ENABLE_DEMO_REGISTRATION="false", ENABLE_DEMO_PATIENTS="false",
    LLM_PROVIDER="mock", LLM_BASE_URL="http://127.0.0.1:1", LLM_MODEL_NAME="qwen3:0.6b",
    PGPASSFILE=unused_passfile,
)
real_connect = asyncpg.connect

async def guarded_connect(*args, **kwargs):
    # SQLAlchemy passes explicit keyword connection parameters. Deny DSNs and
    # implicit/environment targets before opening a socket.
    if args or kwargs.get("dsn") is not None or (
        kwargs.get("host"), kwargs.get("port"), kwargs.get("database"), kwargs.get("user")
    ) != ("127.0.0.1", 55439, "clincoherence_db_test", "cc_m8_test"):
        raise RuntimeError("Refusing connection outside approved disposable target")
    if kwargs.get("password") is not None:
        raise RuntimeError("Disposable target must not use a password")
    kwargs["passfile"] = unused_passfile
    connection = await real_connect(**kwargs)
    try:
        identity = await connection.fetchrow(
            "SELECT current_database(), session_user, inet_server_addr()::text, "
            "inet_server_port(), current_setting('cluster_name')"
        )
        if tuple(identity) != (
            "clincoherence_db_test", "cc_m8_test", "127.0.0.1", 55439,
            "cc_m8_disposable_20261112",
        ):
            raise RuntimeError("Disposable cluster identity mismatch")
    except BaseException:
        await connection.close()
        raise
    return connection

test_path = backend / "tests/test_clinical_fact_reviews.py"
test_args = [
    "-c", str(backend / "pyproject.toml"),
    "--rootdir", str(backend), "--confcutdir", str(backend / "tests"),
    "-o", "addopts=", "--anyio-mode=auto",
    str(test_path), "-q", "-rs", "--tb=short",
]
with (
    patch.object(DotEnvSettingsSource, "_read_env_files", return_value={}),
    patch.object(asyncpg, "connect", guarded_connect),
    patch.object(httpx.HTTPTransport, "handle_request", deny_connection),
    patch.object(httpx.AsyncHTTPTransport, "handle_async_request", deny_async_connection),
):
    raise SystemExit(pytest.main(
        test_args, plugins=["anyio.pytest_plugin", "tests.database", OfflinePlugin()]
    ))
'@ | & 'C:\Users\kavan\clincoherence\backend\.venv\Scripts\python.exe' -I -
if ($LASTEXITCODE -ne 0) { throw 'Disposable validation failed; stop and report before continuing' }
```

## Database mutations requiring approval

The existing fixture creates a fresh `cc_test_<random UUID>` schema for each
database test, pins the search path to it, and invokes migration upgrades 0001
through 0011 using Alembic Operations. Tests populate only synthetic fixtures.
There is no `alembic upgrade` command using application settings and no migration
of the test database's public schema or any existing application database.

Approval must explicitly include these operations **inside this disposable target**:

- Creating the test role/database and per-test schemas, running migrations, and
  writing synthetic records.
- Attempting UPDATE, DELETE and TRUNCATE of synthetic review events to confirm
  that the append-only triggers reject them.
- Downgrading an empty review table, then upgrading to 0011 over synthetic existing
  facts; attempting and verifying refusal of a populated downgrade.
- Fixture teardown: `DROP SCHEMA "cc_test_<generated UUID>" CASCADE`, limited to the
  exact schema generated by that fixture in this new database.

Validation covers preservation, approval/version/clinician isolation, consent
revocation, concurrency, idempotent retries, forged constraints and append-only
enforcement. Report failures and skips separately; skipped cases are not passes.
The existing pytest 9.2/AnyIO fallback limitation remains; no packages are installed.

## Shutdown and retained artifacts

After tests close their sessions, stop only this new cluster:

```powershell
$ErrorActionPreference = 'Stop'
$pgStop = Start-Process -FilePath 'C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe' -WindowStyle Hidden -PassThru -ArgumentList @(
    '-D', 'C:\Users\kavan\clincoherence\.m8-pg-disposable-20261112', '-w', '-t', '30', 'stop', '-m', 'fast'
)
if (-not $pgStop.WaitForExit(45000)) { throw 'Shutdown controller timed out; stopped state unconfirmed' }
$pgStop.Refresh()
if ($null -eq $pgStop.ExitCode -or $pgStop.ExitCode -ne 0) {
    throw 'Temporary server shutdown requires read-only investigation; do not force-kill or delete files'
}
```

No `DROP DATABASE`, cluster-directory deletion, existing-service restart, merge or
push is planned. Retain the stopped cluster for review; removal requires separate
approval. No connection or command targets `clincoherence_db`, `collabsync`,
`collabsync_demo`, or any database named `postgres`.
