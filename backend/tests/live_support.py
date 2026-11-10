"""Opt-in local live harness. Importing this module never connects or starts processes."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"
DATABASE = "clincoherence_db_test"
PORTS = (8010, 3010)
QUERY_TIMEOUT = 15
OPERATION_TIMEOUT = 60
P = ParamSpec("P")
T = TypeVar("T")


class SafetyError(RuntimeError):
    """Only fixed, non-sensitive messages may leave the harness."""


class FailureCategory(StrEnum):
    PROCESS_SHUTDOWN_UNCERTAIN = "PROCESS_SHUTDOWN_UNCERTAIN"
    SCHEMA_CLEANUP_FAILED = "SCHEMA_CLEANUP_FAILED"


class LifecycleError(SafetyError):
    def __init__(self, category: FailureCategory) -> None:
        self.category = category
        message = (
            "Process shutdown uncertain; owned test schema retained"
            if category == FailureCategory.PROCESS_SHUTDOWN_UNCERTAIN
            else "Owned-schema cleanup failed; transaction outcome unconfirmed"
        )
        super().__init__(message)


def bounded_database_operation(
    function: Callable[P, Coroutine[Any, Any, T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    """Cooperative operation bound; DB-side timeouts also bound individual statements."""

    @wraps(function)
    async def bounded(*args: P.args, **kwargs: P.kwargs) -> T:
        async with asyncio.timeout(OPERATION_TIMEOUT):
            return await function(*args, **kwargs)

    return bounded


def connection_options(run: Run) -> dict[str, Any]:
    return {
        "timeout": 5,
        "command_timeout": QUERY_TIMEOUT,
        "server_settings": {
            "search_path": run.schema,
            "statement_timeout": str(QUERY_TIMEOUT * 1000),
            "lock_timeout": "5000",
        },
    }


def dedicated_url(value: str | None) -> URL:
    """No dotenv or DATABASE_URL fallback; reject alternate driver/host routing."""
    try:
        if not value or value != value.strip():
            raise ValueError
        url = make_url(value)
        if (
            url.drivername != "postgresql+asyncpg"
            or url.database != DATABASE
            or url.host not in {"localhost", "127.0.0.1", "::1"}
            or url.port != 5432
            or not url.username
            or not url.password
            or url.query
            or "#" in value
        ):
            raise ValueError
        return url
    except Exception:
        raise SafetyError("Explicit dedicated localhost test database URL required") from None


@dataclass(frozen=True)
class Run:
    url: URL = field(repr=False)
    run_id: str
    owner: str = field(repr=False)

    def __post_init__(self) -> None:
        dedicated_url(self.url.render_as_string(hide_password=False))
        if not re.fullmatch(r"[0-9a-f]{32}", self.run_id) or not re.fullmatch(
            r"[0-9a-f]{64}", self.owner
        ):
            raise SafetyError("Invalid run ownership")

    @property
    def schema(self) -> str:
        return "cc_live_" + self.run_id

    @property
    def marker(self) -> str:
        return "clincoherence-live:" + self.owner

    @classmethod
    def from_env(cls) -> Run:
        return cls(
            dedicated_url(os.environ.get("TEST_DATABASE_URL")),
            os.environ.get("CC_LIVE_RUN", ""),
            os.environ.get("CC_LIVE_OWNER", ""),
        )

    def environment(self) -> dict[str, str]:
        return {
            "TEST_DATABASE_URL": self.url.render_as_string(hide_password=False),
            "DATABASE_URL": self.url.render_as_string(hide_password=False),
            "CC_LIVE_RUN": self.run_id,
            "CC_LIVE_OWNER": self.owner,
        }


def isolated_engine(run: Run) -> AsyncEngine:
    return create_async_engine(
        run.url,
        hide_parameters=True,
        connect_args=connection_options(run),
    )


async def verify_database(connection: AsyncConnection) -> None:
    if await connection.scalar(text("SELECT current_database()")) != DATABASE:
        raise SafetyError("Server database verification failed")


async def verify_connection(connection: AsyncConnection, run: Run, *, owned: bool) -> None:
    await verify_database(connection)
    if owned:
        row = (
            await connection.execute(
                text(
                    "SELECT current_schema(), obj_description(oid, 'pg_namespace'), "
                    "nspowner = (SELECT oid FROM pg_roles WHERE rolname = current_user) "
                    "FROM pg_namespace WHERE nspname = :schema"
                ),
                {"schema": run.schema},
            )
        ).first()
        if row is None or tuple(row) != (run.schema, run.marker, True):
            raise SafetyError("Private schema ownership verification failed")


@bounded_database_operation
async def setup(run: Run) -> None:
    """Only called by the explicitly approved launcher; all DDL is run-local."""
    engine = isolated_engine(run)
    try:
        async with engine.connect() as connection:
            await verify_connection(connection, run, owned=False)
            # No IF NOT EXISTS: an existing schema can never be adopted.
            await connection.execute(text(f'CREATE SCHEMA "{run.schema}"'))
            await connection.execute(text(f"COMMENT ON SCHEMA \"{run.schema}\" IS '{run.marker}'"))
            await connection.commit()
            await verify_connection(connection, run, owned=True)
            from alembic.migration import MigrationContext
            from alembic.operations import Operations

            from tests.database import migrations

            def upgrade(sync_connection: Any) -> None:
                with Operations.context(MigrationContext.configure(sync_connection)):
                    for revision in migrations():  # type: ignore[no-untyped-call]
                        revision.upgrade()

            await connection.run_sync(upgrade)
            await connection.commit()
    finally:
        await engine.dispose()


def inspection_target(run_id: str | None, schema: str | None) -> str:
    """Exactly one explicit run-local identifier; no wildcard or environment target fallback."""
    if (run_id is None) == (schema is None):
        raise SafetyError("Exactly one inspection target is required")
    if run_id is not None:
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise SafetyError("Invalid inspection run identifier")
        return "cc_live_" + run_id
    if schema is None or not re.fullmatch(r"cc_live_[0-9a-f]{32}", schema):
        raise SafetyError("Invalid inspection schema")
    return schema


@bounded_database_operation
async def inspect_schema(run_id: str | None = None, schema: str | None = None) -> dict[str, object]:
    """Read one exact catalog target only. Presence grants no ownership or cleanup authority."""
    target = inspection_target(run_id, schema)
    url = dedicated_url(os.environ.get("TEST_DATABASE_URL"))
    engine = create_async_engine(
        url,
        hide_parameters=True,
        connect_args={
            "timeout": 5,
            "command_timeout": QUERY_TIMEOUT,
            "server_settings": {
                "search_path": "pg_catalog",
                "default_transaction_read_only": "on",
                "statement_timeout": str(QUERY_TIMEOUT * 1000),
                "lock_timeout": "5000",
            },
        },
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await verify_database(connection)
            present = await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = :schema)"
                ),
                {"schema": target},
            )
            if type(present) is not bool:
                raise SafetyError("Unverifiable schema inspection result")
            return {
                "schema": target,
                "status": "PRESENT" if present else "ABSENT",
                "read_only": True,
            }
    finally:
        await engine.dispose()


@bounded_database_operation
async def cleanup(run: Run) -> None:
    engine = isolated_engine(run)
    try:
        async with engine.connect() as connection:
            await verify_connection(connection, run, owned=True)
            # CASCADE must not remove logical objects in another schema through a foreign FK/view.
            external = await connection.scalar(
                text(
                    "WITH RECURSIVE owned(classid, objid, objsubid) AS ("
                    " SELECT 'pg_namespace'::regclass::oid, oid, 0 FROM pg_namespace "
                    " WHERE nspname = :schema UNION "
                    " SELECT d.classid, d.objid, d.objsubid FROM pg_depend d JOIN owned o "
                    " ON d.refclassid = o.classid AND d.refobjid = o.objid) "
                    "SELECT count(*) FROM owned o, LATERAL "
                    "pg_identify_object(o.classid, o.objid, o.objsubid) i "
                    "WHERE i.schema IS NOT NULL AND i.schema NOT IN "
                    "(:schema, 'pg_catalog', 'pg_toast')"
                ),
                {"schema": run.schema},
            )
            if external != 0:
                raise SafetyError("External schema dependency found; owned schema retained")
            await connection.execute(text(f'DROP SCHEMA "{run.schema}" CASCADE'))
            await connection.commit()
    finally:
        await engine.dispose()


@bounded_database_operation
async def seed(run: Run, password: str) -> tuple[str, str]:
    # App imports happen only after the parent has pinned DATABASE_URL.
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.security import get_password_hash
    from app.models import PractitionerProfile, User

    engine = isolated_engine(run)
    identities = tuple(f"doctor-{n}-{run.run_id}@synthetic.invalid" for n in (1, 2))
    try:
        async with engine.connect() as connection:
            await verify_connection(connection, run, owned=True)
            async with AsyncSession(connection, expire_on_commit=False) as session:
                for identity in identities:
                    account = User(
                        identity=identity,
                        hashed_password=get_password_hash(password),
                        role="practitioner",
                        is_active=True,
                    )
                    session.add(account)
                    await session.flush()
                    session.add(PractitionerProfile(account_id=account.id, is_authorized=True))
                await session.flush()
            await connection.commit()
    finally:
        await engine.dispose()
    return identities[0], identities[1]


@bounded_database_operation
async def observe(run: Run, operation: UUID, digest: str) -> dict[str, object]:
    """Private read-only metadata observer; never returns identities or clinical values."""
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SafetyError("Invalid observer digest")
    engine = isolated_engine(run)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await verify_connection(connection, run, owned=True)
            rows = (
                await connection.execute(
                    text(
                        "SELECT a.gate_decision, r.body_digest, r.representation_version "
                        "FROM verification_audit_events a "
                        "LEFT JOIN response_release_intents r ON r.evaluation_event_id = a.id "
                        "WHERE a.operation_id = :operation"
                    ),
                    {"operation": operation},
                )
            ).all()
            return {
                "events": len(rows),
                "intents": sum(row.body_digest is not None for row in rows),
                "pass": len(rows) == 1 and rows[0].gate_decision == "PASS",
                "digest_matches": len(rows) == 1 and rows[0].body_digest == digest,
                "evidence_representation": len(rows) == 1
                and rows[0].representation_version == "record-evidence-json-v2",
            }
    finally:
        await engine.dispose()


def require_ports_free() -> None:
    for port in PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise SafetyError(
                    "Dedicated live ports must be free; server reuse forbidden"
                ) from None


class WindowsJob:
    """Owned kernel job, including descendants even after the original process exits."""

    def __init__(self) -> None:
        from ctypes import wintypes

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]

        class Limits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("minimum", ctypes.c_size_t),
                ("maximum", ctypes.c_size_t),
                ("active", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Limits),
                ("io", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise SafetyError("Cannot establish process ownership")
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.kernel.CloseHandle(self.handle)
            raise SafetyError("Cannot establish process shutdown protection")

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        # Popen was CREATE_SUSPENDED: no descendants can escape before job assignment.
        handle = int(process._handle)  # type: ignore[attr-defined]
        if not self.kernel.AssignProcessToJobObject(self.handle, handle):
            raise SafetyError("Cannot assign owned process job")

    def resume(self, process: subprocess.Popen[bytes]) -> None:
        handle = int(process._handle)  # type: ignore[attr-defined]
        native = ctypes.WinDLL("ntdll")
        native.NtResumeProcess.argtypes = [ctypes.c_void_p]
        native.NtResumeProcess.restype = ctypes.c_long
        if native.NtResumeProcess(handle) != 0:
            raise SafetyError("Cannot resume owned process")

    def stop(self) -> None:
        if getattr(self, "closed", False):
            return

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("times", ctypes.c_int64 * 4),
                ("faults", ctypes.c_uint32),
                ("total", ctypes.c_uint32),
                ("active", ctypes.c_uint32),
                ("terminated", ctypes.c_uint32),
            ]

        if not self.kernel.TerminateJobObject(self.handle, 1):
            raise SafetyError("Owned process shutdown uncertain; schema retained")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            info = Accounting()
            if not self.kernel.QueryInformationJobObject(
                self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None
            ):
                raise SafetyError("Owned process shutdown verification failed")
            if info.active == 0:
                if not self.kernel.CloseHandle(self.handle):
                    raise SafetyError("Cannot close owned job handle")
                self.closed = True
                return
            time.sleep(0.05)
        raise SafetyError("Owned descendants still running; schema retained")


@dataclass
class OwnedProcess:
    process: subprocess.Popen[bytes] | None = None
    job: WindowsJob | None = None
    creation_started: bool = False
    shutdown_confirmed: bool = False

    def poll(self) -> int | None:
        if self.process is None:
            raise SafetyError("Owned process creation incomplete")
        return self.process.poll()

    def wait(self, timeout: int) -> int:
        if self.process is None:
            raise SafetyError("Owned process creation incomplete")
        return self.process.wait(timeout=timeout)


def stop_owned(process: OwnedProcess) -> None:
    if process.shutdown_confirmed:
        return
    failed = False
    if process.job is not None:
        try:
            process.job.stop()  # Even if the root exited, descendants must be stopped.
        except BaseException:
            failed = True
    if process.process is not None:
        try:
            # Assignment may have failed: the suspended root might not belong to the job.
            if process.process.poll() is None:
                process.process.kill()
            process.process.wait(timeout=20)
        except BaseException:
            failed = True
    elif process.creation_started:
        # Popen may have created a suspended root before raising/being interrupted.
        failed = True
    if failed:
        raise SafetyError("Owned process shutdown uncertain; schema retained")
    process.shutdown_confirmed = True


@dataclass
class ProcessRegistry:
    entries: list[OwnedProcess] = field(default_factory=list)
    registration_incomplete: bool = False

    def reserve(self) -> OwnedProcess:
        # A failed/interrupted registration can never look like an empty, safe registry.
        if self.registration_incomplete:
            raise SafetyError("Previous process registration incomplete")
        self.registration_incomplete = True
        entry = OwnedProcess()
        self.entries.append(entry)
        self.registration_incomplete = False
        return entry

    def shutdown(self) -> None:
        failed = self.registration_incomplete
        for entry in reversed(self.entries):
            try:
                stop_owned(entry)
            except BaseException:
                failed = True
        if failed or any(not entry.shutdown_confirmed for entry in self.entries):
            raise SafetyError("Process shutdown uncertain; owned test schema retained")


def spawn(
    command: list[str], cwd: Path, env: dict[str, str], registry: ProcessRegistry
) -> OwnedProcess:
    # No stdout/stderr artifacts containing credentials, page contents or request bodies.
    if os.name != "nt":
        raise SafetyError("This process ownership implementation requires Windows")
    entry = registry.reserve()  # Before ANY native process creation, not after spawn returns.
    try:
        entry.job = WindowsJob()
        entry.creation_started = True
        entry.process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW | 0x00000004,  # CREATE_SUSPENDED
        )
        entry.job.assign(entry.process)
        entry.job.resume(entry.process)
    except BaseException:
        # Ownership remains in the caller's registry even when local teardown is interrupted.
        with suppress(BaseException):
            stop_owned(entry)
        # Registry shutdown retries; it must confirm before any schema cleanup.
        raise
    return entry


def wait_ready(process: OwnedProcess, url: str, owner: str) -> None:
    import httpx

    deadline = time.monotonic() + 120
    with httpx.Client(trust_env=False, timeout=2) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise SafetyError("Owned server failed startup")
            try:
                response = client.get(url)
                if response.headers.get("x-clincoherence-live-run") == owner:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    raise SafetyError("Owned server readiness failed")


def run_live(*, approved: bool) -> int:
    if not approved:
        raise SafetyError("Live execution requires separate approval and --approve-live")
    if os.name != "nt":
        raise SafetyError("This process ownership implementation requires Windows")
    run = Run(
        dedicated_url(os.environ.get("TEST_DATABASE_URL")), uuid4().hex, secrets.token_hex(32)
    )
    require_ports_free()
    node = shutil.which("node")
    if node is None or not (FRONTEND / "node_modules/@playwright/test/cli.js").is_file():
        raise SafetyError("Local Node and installed Playwright are required")
    recorder = FRONTEND / "node_modules/playwright/lib/index.js"
    if not recorder.is_file() or "process.env.PLAYWRIGHT_NO_COPY_PROMPT" not in recorder.read_text(
        encoding="utf-8"
    ):
        raise SafetyError("Installed Playwright must support DOM snapshot suppression")
    password = secrets.token_urlsafe(32)
    child = {
        **os.environ,
        **run.environment(),
        "SECRET_KEY": secrets.token_urlsafe(48),
        "ALGORITHM": "HS256",
        "LLM_PROVIDER": "mock",
        "INVITATION_URL_ORIGIN": "http://127.0.0.1:3010",
        "BACKEND_CORS_ORIGINS": '["http://127.0.0.1:3010"]',
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    # Import-time Settings must not see the development URL during fixture seeding.
    os.environ.update(run.environment())
    registry = ProcessRegistry()
    setup_attempted = False
    result: int | None = None
    # Non-secret infrastructure identifiers only; retain these for later exact-target inspection.
    print(f"Live run: run_id={run.run_id} schema={run.schema}", flush=True)
    try:
        setup_attempted = True
        asyncio.run(setup(run))
        doctor, outsider = asyncio.run(seed(run, password))
        require_ports_free()
        backend = spawn(
            [
                sys.executable,
                "-B",
                "-m",
                "uvicorn",
                "tests.live_app:create_live_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8010",
                "--no-access-log",
                "--log-level",
                "critical",
            ],
            BACKEND,
            child,
            registry,
        )
        wait_ready(backend, "http://127.0.0.1:8010/health", run.run_id)
        # Next needs no database credentials, signing key or fixture passwords.
        next_env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "PATH",
                "PATHEXT",
                "SYSTEMROOT",
                "WINDIR",
                "COMSPEC",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
                "HOME",
            }
        }
        next_env["BACKEND_ORIGIN"] = "http://127.0.0.1:8010"
        next_env["NEXT_TELEMETRY_DISABLED"] = "1"
        frontend = spawn(
            [
                node,
                str(FRONTEND / "node_modules/next/dist/bin/next"),
                "dev",
                "--hostname",
                "127.0.0.1",
                "--port",
                "3010",
            ],
            FRONTEND,
            next_env,
            registry,
        )
        wait_ready(frontend, "http://127.0.0.1:3010/api/v1/auth/profile", run.run_id)
        playwright_env = {
            **child,
            "CC_LIVE_PASSWORD": password,
            "CC_LIVE_DOCTOR": doctor,
            "CC_LIVE_OUTSIDER": outsider,
            "CC_LIVE_PYTHON": sys.executable,
            "CC_LIVE_BACKEND": str(BACKEND),
            "PLAYWRIGHT_NO_COPY_PROMPT": "1",
        }
        browser = spawn(
            [
                node,
                str(FRONTEND / "node_modules/@playwright/test/cli.js"),
                "test",
                "--config=playwright.live.config.ts",
                "--project=live-chromium",
            ],
            FRONTEND,
            playwright_env,
            registry,
        )
        result = browser.wait(timeout=600)
        print("Browser suite: PASS" if result == 0 else "Browser suite: FAIL", flush=True)
        if backend.poll() is not None or frontend.poll() is not None:
            raise SafetyError("Owned service stopped during live tests")
    finally:
        # If any tree cannot be stopped, abort cleanup rather than dropping a live schema.
        try:
            registry.shutdown()
        except BaseException:
            # Schema name is random infrastructure metadata, not a credential or patient ID.
            print(
                f"Process shutdown: FAIL; schema cleanup: SKIPPED; retained schema={run.schema}",
                file=sys.stderr,
                flush=True,
            )
            raise LifecycleError(FailureCategory.PROCESS_SHUTDOWN_UNCERTAIN) from None
        print("Process shutdown: PASS", flush=True)
        if setup_attempted:
            try:
                asyncio.run(cleanup(run))
            except BaseException:
                print(
                    "Schema cleanup: FAIL; transaction outcome unconfirmed",
                    file=sys.stderr,
                    flush=True,
                )
                raise LifecycleError(FailureCategory.SCHEMA_CLEANUP_FAILED) from None
            print("Schema cleanup: PASS (drop commit acknowledged)", flush=True)
    # Unreachable on any execution, shutdown, cleanup, commit or disposal exception.
    if result != 0:
        print("Overall launcher: FAIL [BROWSER_FAILED]", file=sys.stderr, flush=True)
        return 1
    print(f"Overall launcher: PASS [run_id={run.run_id} schema={run.schema}]", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "observe", "inspect-schema"])
    parser.add_argument("--approve-live", action="store_true")
    parser.add_argument("--operation")
    parser.add_argument("--digest")
    parser.add_argument("--run-id")
    parser.add_argument("--schema")
    args = parser.parse_args()
    try:
        if args.command == "run":
            return run_live(approved=args.approve_live)
        if args.command == "inspect-schema":
            result = asyncio.run(inspect_schema(args.run_id, args.schema))
            print(json.dumps(result))
            return 0 if result["status"] == "ABSENT" else 1
        print(json.dumps(asyncio.run(observe(Run.from_env(), UUID(args.operation), args.digest))))
        return 0
    except BaseException as error:
        # Never echo SQLAlchemy/driver, command-line or browser exceptions.
        if args.command == "run":
            category = (
                error.category.value
                if isinstance(error, LifecycleError)
                else "INTERRUPTED"
                if isinstance(error, (KeyboardInterrupt, SystemExit))
                else "RUN_TIMEOUT"
                if isinstance(error, TimeoutError)
                else "RUN_FAILED"
            )
            print(f"Overall launcher: FAIL [{category}]", file=sys.stderr, flush=True)
        elif args.command == "inspect-schema":
            category = (
                "INSPECTION_REJECTED" if isinstance(error, SafetyError) else "INSPECTION_FAILED"
            )
            print(f"Schema inspection: FAIL [{category}]", file=sys.stderr, flush=True)
        else:
            print("Guarded observation: FAIL [OBSERVATION_FAILED]", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
