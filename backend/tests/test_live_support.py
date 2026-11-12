"""Non-destructive harness tests: no real connections, services, schemas or browsers."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url

from tests import live_app
from tests import live_support as live

SAFE = "postgresql+asyncpg://fixture:placeholder@127.0.0.1:5432/clincoherence_db_test"


@pytest.mark.parametrize("representation", ["record-response-json-v1", "record-evidence-json-v2"])
async def test_observer_reports_representation_boolean_only_after_owned_read(
    monkeypatch, run, representation
):
    operation, digest = uuid4(), "c" * 64
    connection, engine = connection_engine(live.DATABASE, (run.schema, run.marker, True))
    verified = AsyncMock()
    row = SimpleNamespace(
        gate_decision="PASS", body_digest=digest, representation_version=representation
    )
    result_rows = Mock()
    result_rows.all.return_value = [row]
    connection.execute.return_value = result_rows
    monkeypatch.setattr(live, "isolated_engine", Mock(return_value=engine))
    monkeypatch.setattr(live, "verify_connection", verified)
    result = await live.observe(run, operation, digest)
    assert result == {
        "events": 1,
        "intents": 1,
        "pass": True,
        "digest_matches": True,
        "evidence_representation": representation == "record-evidence-json-v2",
    }
    verified.assert_awaited_once_with(connection, run, owned=True)
    calls = connection.execute.call_args_list
    assert str(calls[0].args[0]) == "SET TRANSACTION READ ONLY"
    assert "r.representation_version" in str(calls[1].args[0])
    assert "WHERE a.operation_id = :operation" in str(calls[1].args[0])
    assert calls[1].args[1] == {"operation": operation}
    assert all(str(c.args[0]).startswith(("SET TRANSACTION READ ONLY", "SELECT")) for c in calls)
    assert set(result.values()) <= {True, False}
    connection.commit.assert_not_called()
    engine.dispose.assert_awaited_once()


async def test_observer_no_rows_cannot_claim_v2_or_digest(monkeypatch, run):
    connection, engine = connection_engine(live.DATABASE, (run.schema, run.marker, True))
    result_rows = Mock()
    result_rows.all.return_value = []
    connection.execute.return_value = result_rows
    monkeypatch.setattr(live, "isolated_engine", Mock(return_value=engine))
    monkeypatch.setattr(live, "verify_connection", AsyncMock())
    assert await live.observe(run, uuid4(), "c" * 64) == {
        "events": 0,
        "intents": 0,
        "pass": False,
        "digest_matches": False,
        "evidence_representation": False,
    }


def test_migration_0008_remains_in_shared_live_schema_upgrade_chain():
    from tests.database import migrations

    revisions = migrations()
    evidence = next(item for item in revisions if item.revision == "0008_evidence_release")
    assert evidence.down_revision == "0007_response_release_intent"
    demo = next(item for item in revisions if item.revision == "0009_demo_patients")
    assert demo.down_revision == evidence.revision
    summary = next(item for item in revisions if item.revision == "0010_clinical_summary_release")
    assert summary.down_revision == demo.revision
    assert revisions[-1].revision == "0011_clinical_fact_reviews"
    assert revisions[-1].down_revision == summary.revision


async def test_observer_rejects_ownership_failure_before_event_lookup(monkeypatch, run):
    connection, engine = connection_engine(live.DATABASE, None)
    monkeypatch.setattr(live, "isolated_engine", Mock(return_value=engine))
    monkeypatch.setattr(
        live,
        "verify_connection",
        AsyncMock(side_effect=live.SafetyError("Ownership verification failed")),
    )
    with pytest.raises(live.SafetyError):
        await live.observe(run, uuid4(), "c" * 64)
    assert connection.execute.await_count == 1
    assert str(connection.execute.await_args.args[0]) == "SET TRANSACTION READ ONLY"
    connection.commit.assert_not_called()
    engine.dispose.assert_awaited_once()


@pytest.fixture
def run():
    return live.Run(make_url(SAFE), "a" * 32, "b" * 64)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        " ",
        "not a URL",
        SAFE + " ",
        SAFE.replace("clincoherence_db_test", "clincoherence_db"),
        SAFE.replace("postgresql+asyncpg", "postgresql"),
        SAFE.replace("127.0.0.1", "remote.invalid"),
        SAFE.replace(":5432", ":5433"),
        SAFE.replace(":5432", ""),
        SAFE + "?host=remote.invalid",
        SAFE + "?options=unsafe",
        SAFE + "#fragment",
        SAFE.replace("fixture:placeholder@", ""),
        SAFE.replace(":placeholder", ""),
        SAFE.replace("5432", "invalid"),
    ],
)
def test_invalid_targets_never_create_engine(value, monkeypatch):
    engine = Mock(side_effect=AssertionError("Must not construct engine"))
    monkeypatch.setattr(live, "create_async_engine", engine)
    with pytest.raises(live.SafetyError):
        live.dedicated_url(value)
    engine.assert_not_called()


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_only_explicit_loopback_test_urls(host):
    assert live.dedicated_url(SAFE.replace("127.0.0.1", host)).database == live.DATABASE


def test_no_environment_fallback(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", SAFE)
    with pytest.raises(live.SafetyError):
        live.Run.from_env()


@pytest.mark.parametrize(
    "run_id,owner",
    [
        ("public", "b" * 64),
        ("a" * 31, "b" * 64),
        ("a" * 32, "wrong"),
        ('"; DROP SCHEMA public;--', "b" * 64),
    ],
)
def test_schema_names_cannot_be_supplied_arbitrarily(run_id, owner):
    with pytest.raises(live.SafetyError):
        live.Run(make_url(SAFE), run_id, owner)


def test_credentials_and_ownership_are_not_in_repr(run):
    assert "placeholder" not in repr(run) and run.owner not in repr(run)
    assert run.environment()["DATABASE_URL"] == SAFE


def test_engine_pins_every_connection_with_no_public_fallback(run, monkeypatch):
    construct = Mock()
    monkeypatch.setattr(live, "create_async_engine", construct)
    live.isolated_engine(run)
    assert construct.call_args.kwargs["connect_args"] == live.connection_options(run)
    assert live.connection_options(run)["command_timeout"] == 15
    assert live.connection_options(run)["server_settings"] == {
        "search_path": run.schema,
        "statement_timeout": "15000",
        "lock_timeout": "5000",
    }
    assert construct.call_args.kwargs["hide_parameters"] is True


def connection_engine(database, row):
    connection = AsyncMock()
    connection.scalar.return_value = database
    result = Mock()
    result.first.return_value = row
    connection.execute.return_value = result
    engine = Mock()
    engine.connect.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.connect.return_value.__aexit__ = AsyncMock(return_value=None)
    engine.dispose = AsyncMock()
    return connection, engine


@pytest.mark.parametrize(
    "database,row",
    [
        ("clincoherence_db", None),
        (live.DATABASE, None),
        (live.DATABASE, ("public", "unowned", True)),
        (live.DATABASE, ("cc_live_" + "a" * 32, "clincoherence-live:" + "b" * 64, False)),
    ],
)
async def test_cleanup_refuses_wrong_database_or_unowned_schema(run, monkeypatch, database, row):
    connection, engine = connection_engine(database, row)
    monkeypatch.setattr(live, "isolated_engine", Mock(return_value=engine))
    with pytest.raises(live.SafetyError):
        await live.cleanup(run)
    assert not any("DROP" in str(call.args[0]) for call in connection.execute.call_args_list)
    connection.commit.assert_not_called()
    engine.dispose.assert_awaited_once()


async def test_cleanup_drops_only_its_exact_owned_schema(run, monkeypatch):
    connection, engine = connection_engine(live.DATABASE, (run.schema, run.marker, True))
    connection.scalar.side_effect = [live.DATABASE, 0]
    monkeypatch.setattr(live, "isolated_engine", Mock(return_value=engine))
    await live.cleanup(run)
    assert str(connection.execute.call_args.args[0]) == f'DROP SCHEMA "{run.schema}" CASCADE'
    connection.commit.assert_awaited_once()


async def test_external_dependency_prevents_cascade_outside_owned_schema(run, monkeypatch):
    connection, engine = connection_engine(live.DATABASE, (run.schema, run.marker, True))
    connection.scalar.side_effect = [live.DATABASE, 1]
    monkeypatch.setattr(live, "isolated_engine", Mock(return_value=engine))
    with pytest.raises(live.SafetyError, match="External schema dependency"):
        await live.cleanup(run)
    assert not any("DROP" in str(call.args[0]) for call in connection.execute.call_args_list)


async def test_setup_wrong_database_prevents_all_ddl(run, monkeypatch):
    connection, engine = connection_engine("clincoherence_db", None)
    monkeypatch.setattr(live, "isolated_engine", Mock(return_value=engine))
    with pytest.raises(live.SafetyError):
        await live.setup(run)
    connection.execute.assert_not_called()


@pytest.mark.parametrize("approved", [False, True])
def test_failed_safety_check_prevents_startup(monkeypatch, approved):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    spawn = Mock()
    setup = AsyncMock()
    monkeypatch.setattr(live, "spawn", spawn)
    monkeypatch.setattr(live, "setup", setup)
    with pytest.raises(live.SafetyError):
        live.run_live(approved=approved)
    spawn.assert_not_called()
    setup.assert_not_called()


def test_occupied_port_cannot_reuse_server(monkeypatch):
    socket = MagicMock()
    socket.__enter__.return_value.bind.side_effect = OSError()
    monkeypatch.setattr(live.socket, "socket", Mock(return_value=socket))
    with pytest.raises(live.SafetyError, match="reuse forbidden"):
        live.require_ports_free()


def prepare_mock_launcher(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", SAFE)
    monkeypatch.setattr(live, "require_ports_free", Mock())
    monkeypatch.setattr(live.shutil, "which", Mock(return_value="node"))
    monkeypatch.setattr(live.Path, "is_file", Mock(return_value=True))
    monkeypatch.setattr(
        live.Path, "read_text", Mock(return_value="process.env.PLAYWRIGHT_NO_COPY_PROMPT")
    )
    monkeypatch.setattr(live, "setup", AsyncMock())
    monkeypatch.setattr(live, "seed", AsyncMock(return_value=("one", "two")))
    cleanup = AsyncMock()
    monkeypatch.setattr(live, "cleanup", cleanup)
    # Keep this test's dummy environment from escaping into other tests.
    monkeypatch.setenv("DATABASE_URL", "previous")
    monkeypatch.setenv("CC_LIVE_RUN", "previous")
    monkeypatch.setenv("CC_LIVE_OWNER", "previous")
    return cleanup


def test_failed_startup_cleans_owned_schema_after_processes_stop(monkeypatch):
    cleanup = prepare_mock_launcher(monkeypatch)
    registry = live.ProcessRegistry()
    monkeypatch.setattr(live, "ProcessRegistry", Mock(return_value=registry))
    process = live.OwnedProcess()
    registry.entries.append(process)
    monkeypatch.setattr(live, "spawn", Mock(return_value=process))
    monkeypatch.setattr(live, "wait_ready", Mock(side_effect=live.SafetyError("Startup failed")))
    stopped = Mock(side_effect=lambda entry: setattr(entry, "shutdown_confirmed", True))
    monkeypatch.setattr(live, "stop_owned", stopped)
    with pytest.raises(live.SafetyError):
        live.run_live(approved=True)
    stopped.assert_called_once_with(process)
    cleanup.assert_awaited_once()


def test_uncertain_process_shutdown_retains_schema(monkeypatch):
    cleanup = prepare_mock_launcher(monkeypatch)
    registry = live.ProcessRegistry(entries=[live.OwnedProcess()])
    monkeypatch.setattr(live, "ProcessRegistry", Mock(return_value=registry))
    monkeypatch.setattr(live, "spawn", Mock(return_value=Mock()))
    monkeypatch.setattr(live, "wait_ready", Mock(side_effect=live.SafetyError("Startup failed")))
    monkeypatch.setattr(live, "stop_owned", Mock(side_effect=live.SafetyError("Uncertain")))
    with pytest.raises(live.SafetyError, match="retained"):
        live.run_live(approved=True)
    cleanup.assert_not_called()


def test_descendants_stop_even_when_root_has_exited():
    process = live.OwnedProcess(process=Mock(), job=Mock(), creation_started=True)
    process.process.poll.return_value = 0
    live.stop_owned(process)
    process.job.stop.assert_called_once()
    process.process.wait.assert_called_once()


def test_windows_job_assigns_before_resume_and_verifies_descendant_shutdown(monkeypatch):
    kernel = Mock()
    kernel.CreateJobObjectW.return_value = 501
    kernel.SetInformationJobObject.return_value = 1
    kernel.AssignProcessToJobObject.return_value = 1
    kernel.NtResumeProcess.return_value = 0
    kernel.TerminateJobObject.return_value = 1

    def empty_job(handle, kind, pointer, size, returned):
        pointer._obj.active = 0
        return 1

    kernel.QueryInformationJobObject.side_effect = empty_job
    monkeypatch.setattr(live.ctypes, "WinDLL", Mock(return_value=kernel))
    job = live.WindowsJob()
    flags = kernel.SetInformationJobObject.call_args.args[2]._obj.basic.flags
    assert flags == 0x2000  # Kernel closes descendants even if the launcher dies.
    process = Mock(_handle=42)
    job.assign(process)
    job.resume(process)
    calls = [call[0] for call in kernel.mock_calls]
    assert calls.index("AssignProcessToJobObject") < calls.index("NtResumeProcess")
    job.stop()
    kernel.TerminateJobObject.assert_called_once_with(501, 1)
    kernel.QueryInformationJobObject.assert_called_once()
    kernel.CloseHandle.assert_called_once_with(501)


def test_spawn_cannot_run_child_before_job_assignment(monkeypatch):
    monkeypatch.setattr(live, "WindowsJob", Mock())
    popen = Mock()
    monkeypatch.setattr(live.subprocess, "Popen", popen)
    registry = live.ProcessRegistry()
    process = live.spawn(["not-executed"], live.BACKEND, {}, registry)
    assert popen.call_args.kwargs["creationflags"] & 0x4  # CREATE_SUSPENDED
    assert popen.call_args.kwargs["stdout"] == live.subprocess.DEVNULL
    process.job.assign.assert_called_once_with(process.process)
    process.job.resume.assert_called_once_with(process.process)
    assert registry.entries == [process]


def mock_native_startup(monkeypatch):
    """Real registry/spawn/teardown logic, but all DB and native operations are fake."""
    cleanup = prepare_mock_launcher(monkeypatch)
    registry = live.ProcessRegistry()
    monkeypatch.setattr(live, "ProcessRegistry", Mock(return_value=registry))
    job = Mock()
    native = Mock()
    native.poll.return_value = None
    native.wait.return_value = 0
    monkeypatch.setattr(live, "WindowsJob", Mock(return_value=job))

    def created(*args, **kwargs):
        assert len(registry.entries) == 1
        assert registry.entries[0].creation_started
        return native

    popen = Mock(side_effect=created)
    monkeypatch.setattr(live.subprocess, "Popen", popen)

    async def safe_cleanup(run):
        assert registry.entries and all(entry.shutdown_confirmed for entry in registry.entries)

    cleanup.side_effect = safe_cleanup
    return cleanup, registry, job, native, popen


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("phase", ["created", "assigning", "resumed", "returned"])
def test_partial_startup_is_registered_and_cleanup_requires_confirmed_stop(
    monkeypatch, failure, phase
):
    cleanup, registry, job, native, popen = mock_native_startup(monkeypatch)

    def interrupted(*args):
        entry = registry.entries[0]
        assert entry.process is native and entry.job is job
        raise failure()

    if phase in {"created", "assigning"}:
        job.assign.side_effect = interrupted
    elif phase == "resumed":
        job.resume.side_effect = interrupted
    else:
        # Covers the old spawn-return / parent-registration window.
        monkeypatch.setattr(live, "wait_ready", interrupted)
    with pytest.raises(failure):
        live.run_live(approved=True)
    assert registry.entries[0].shutdown_confirmed
    job.stop.assert_called_once()
    native.kill.assert_called_once()
    native.wait.assert_called_once()
    cleanup.assert_awaited_once()
    if phase in {"created", "assigning"}:
        job.resume.assert_not_called()


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_interruption_before_registration_never_looks_like_empty_success(monkeypatch, failure):
    cleanup = prepare_mock_launcher(monkeypatch)

    class InterruptedRegistration(list):
        def append(self, entry):
            raise failure()

    registry = live.ProcessRegistry(entries=InterruptedRegistration())
    monkeypatch.setattr(live, "ProcessRegistry", Mock(return_value=registry))
    popen = Mock()
    monkeypatch.setattr(live.subprocess, "Popen", popen)
    with pytest.raises(live.SafetyError, match="retained"):
        live.run_live(approved=True)
    assert registry.registration_incomplete
    popen.assert_not_called()
    cleanup.assert_not_called()
    with pytest.raises(live.SafetyError, match="Previous"):
        registry.reserve()  # A second reservation must not erase the uncertainty latch.


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_creation_without_captured_handle_suppresses_cleanup(monkeypatch, failure):
    cleanup, registry, job, native, popen = mock_native_startup(monkeypatch)
    popen.side_effect = failure()
    with pytest.raises(live.SafetyError, match="retained"):
        live.run_live(approved=True)
    assert registry.entries[0].creation_started
    assert registry.entries[0].process is None
    assert not registry.entries[0].shutdown_confirmed
    native.kill.assert_not_called()  # No guessed PID or unrelated process may be killed.
    cleanup.assert_not_called()


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("transition", ["job_stop", "root_kill", "root_wait"])
def test_uncertain_partial_termination_prevents_schema_cleanup(monkeypatch, failure, transition):
    cleanup, registry, job, native, popen = mock_native_startup(monkeypatch)
    job.assign.side_effect = RuntimeError("Injected startup fault")
    target = {"job_stop": job.stop, "root_kill": native.kill, "root_wait": native.wait}[transition]
    target.side_effect = failure()
    with pytest.raises(live.SafetyError, match="retained"):
        live.run_live(approved=True)
    assert not registry.entries[0].shutdown_confirmed
    assert registry.entries[0].process is native and registry.entries[0].job is job
    cleanup.assert_not_called()


def test_partial_teardown_can_retry_only_using_preserved_owned_handles(monkeypatch):
    cleanup, registry, job, native, popen = mock_native_startup(monkeypatch)
    job.assign.side_effect = RuntimeError("Injected startup fault")
    job.stop.side_effect = [KeyboardInterrupt(), None]
    with pytest.raises(RuntimeError):
        live.run_live(approved=True)
    assert job.stop.call_count == 2
    assert registry.entries[0].shutdown_confirmed
    cleanup.assert_awaited_once()


def test_registry_continues_stopping_other_owned_entries_after_interruption(monkeypatch):
    first, second = live.OwnedProcess(), live.OwnedProcess()
    registry = live.ProcessRegistry(entries=[first, second])

    def stop(entry):
        if entry is second:
            raise KeyboardInterrupt()
        entry.shutdown_confirmed = True

    stopping = Mock(side_effect=stop)
    monkeypatch.setattr(live, "stop_owned", stopping)
    with pytest.raises(live.SafetyError):
        registry.shutdown()
    assert stopping.call_count == 2 and first.shutdown_confirmed
    assert not second.shutdown_confirmed


@pytest.mark.parametrize("operation", ["setup", "cleanup"])
async def test_database_operation_timeout_unwinds_before_any_ddl(run, monkeypatch, operation):
    connection, engine = connection_engine(live.DATABASE, (run.schema, run.marker, True))
    cancelled = False

    async def blocked(*args):
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    connection.scalar.side_effect = blocked
    monkeypatch.setattr(live, "isolated_engine", Mock(return_value=engine))
    monkeypatch.setattr(live, "OPERATION_TIMEOUT", 0.01)
    with pytest.raises(TimeoutError):
        await getattr(live, operation)(run)
    assert cancelled
    connection.execute.assert_not_called()
    engine.dispose.assert_awaited_once()


def test_setup_timeout_never_starts_children_and_uses_only_guarded_cleanup(monkeypatch):
    cleanup = prepare_mock_launcher(monkeypatch)
    monkeypatch.setattr(live, "setup", AsyncMock(side_effect=TimeoutError()))
    spawn = Mock()
    monkeypatch.setattr(live, "spawn", spawn)
    with pytest.raises(TimeoutError):
        live.run_live(approved=True)
    spawn.assert_not_called()
    cleanup.assert_awaited_once()


@pytest.mark.parametrize("changed", ["engine", "factory", "release"])
def test_release_and_request_session_factories_must_be_identical(run, changed):
    engine = SimpleNamespace(url=run.url)
    factory = SimpleNamespace(kw={"bind": engine})
    release = Mock(return_value=factory)
    live_app.verify_factories(engine, factory, release, run)
    if changed == "engine":
        engine.url = make_url(SAFE.replace("clincoherence_db_test", "clincoherence_db"))
    elif changed == "factory":
        factory.kw["bind"] = object()
    else:
        release = Mock(return_value=object())
    with pytest.raises(live.SafetyError):
        live_app.verify_factories(engine, factory, release, run)


@pytest.mark.parametrize("bad", [False, True])
def test_application_checkout_guards_all_pooled_connections(run, monkeypatch, bad):
    listeners = {}

    def listens_for(engine, name):
        def register(callback):
            listeners[name] = callback
            return callback

        return register

    monkeypatch.setattr(live_app.event, "listens_for", listens_for)
    live_app.install_guards(SimpleNamespace(sync_engine=object()), run)
    params = {}
    listeners["do_connect"](None, None, None, params)
    assert params == live.connection_options(run)
    connection = Mock()
    connection.cursor.return_value.fetchone.return_value = (
        "clincoherence_db" if bad else live.DATABASE,
        run.schema,
        run.schema,
        run.marker,
        True,
    )
    if bad:
        with pytest.raises(live.SafetyError):
            listeners["checkout"](connection, None, None)
    else:
        listeners["checkout"](connection, None, None)
    connection.rollback.assert_called_once()


def test_app_factory_rejects_mismatched_environment_before_app_start(run, monkeypatch):
    for name, value in run.environment().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATABASE_URL", SAFE.replace("clincoherence_db_test", "clincoherence_db"))
    with pytest.raises(live.SafetyError):
        live_app.create_live_app()


async def test_asgi_identity_wrapper_preserves_send_order_and_exact_body():
    sent = []
    receive = AsyncMock()
    payload = b'{"synthetic":"fixture"}'

    async def app(scope, source, send):
        assert source is receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        assert len(sent) == 1  # Send is awaited directly, not buffered behind middleware.
        await send({"type": "http.response.body", "body": payload})

    async def send(message):
        sent.append(message)

    await live_app.IdentityWrapper(app, "a" * 32)({"type": "http"}, receive, send)
    assert sent[1]["body"] is payload
    assert sent[0]["headers"] == [(b"x-clincoherence-live-run", b"a" * 32)]


def completed_browser_world(monkeypatch, exit_code=0):
    """Full launcher control flow; database, subprocesses and readiness remain mocked."""
    cleanup = prepare_mock_launcher(monkeypatch)
    registry = live.ProcessRegistry()
    monkeypatch.setattr(live, "ProcessRegistry", Mock(return_value=registry))
    jobs = [Mock() for _ in range(3)]
    roots = [Mock() for _ in range(3)]
    for root in roots:
        root.poll.return_value = None
        root.wait.return_value = 0
    roots[2].wait.return_value = exit_code
    monkeypatch.setattr(live, "WindowsJob", Mock(side_effect=jobs))
    monkeypatch.setattr(live.subprocess, "Popen", Mock(side_effect=roots))
    monkeypatch.setattr(live, "wait_ready", Mock())
    monkeypatch.setattr(live.sys, "argv", ["live_support", "run", "--approve-live"])
    return cleanup, registry, jobs


def test_overall_pass_is_after_browser_shutdown_and_committed_cleanup(monkeypatch, capsys):
    cleanup, registry, jobs = completed_browser_world(monkeypatch)
    during_cleanup = []

    async def committed(run):
        assert all(entry.shutdown_confirmed for entry in registry.entries)
        output = capsys.readouterr().out
        assert "Browser suite: PASS" in output and "Process shutdown: PASS" in output
        assert "Schema cleanup: PASS" not in output and "Overall launcher: PASS" not in output
        during_cleanup.append(output)

    cleanup.side_effect = committed
    assert live.main() == 0
    output = "".join(during_cleanup) + capsys.readouterr().out
    phases = [
        "Browser suite: PASS",
        "Process shutdown: PASS",
        "Schema cleanup: PASS",
        "Overall launcher: PASS",
    ]
    assert [output.index(phase) for phase in phases] == sorted(output.index(p) for p in phases)
    assert "run_id=" in output and "schema=cc_live_" in output
    assert registry.entries and all(entry.shutdown_confirmed for entry in registry.entries)
    cleanup.assert_awaited_once()


@pytest.mark.parametrize(
    "failure", [RuntimeError("redacted-detail"), TimeoutError(), KeyboardInterrupt(), SystemExit(0)]
)
def test_browser_pass_cleanup_failure_never_reports_overall_pass(monkeypatch, capsys, failure):
    cleanup, registry, jobs = completed_browser_world(monkeypatch)
    cleanup.side_effect = failure
    assert live.main() == 1
    captured = capsys.readouterr()
    assert "Browser suite: PASS" in captured.out and "Process shutdown: PASS" in captured.out
    assert "Schema cleanup: PASS" not in captured.out
    assert "Overall launcher: PASS" not in captured.out + captured.err
    assert "SCHEMA_CLEANUP_FAILED" in captured.err and "redacted-detail" not in captured.err


@pytest.mark.parametrize("failure", [RuntimeError(), KeyboardInterrupt(), SystemExit(0)])
def test_uncertain_shutdown_skips_schema_cleanup_and_final_pass(monkeypatch, capsys, failure):
    cleanup, registry, jobs = completed_browser_world(monkeypatch)
    jobs[0].stop.side_effect = failure
    assert live.main() == 1
    cleanup.assert_not_called()
    captured = capsys.readouterr()
    assert "Browser suite: PASS" in captured.out
    assert "SCHEMA_CLEANUP_FAILED" not in captured.err
    assert "PROCESS_SHUTDOWN_UNCERTAIN" in captured.err
    assert "SKIPPED" in captured.err and "Overall launcher: PASS" not in captured.out


def test_failed_drop_commit_never_produces_schema_or_overall_pass(monkeypatch, capsys):
    real_cleanup = live.cleanup
    cleanup, registry, jobs = completed_browser_world(monkeypatch)
    connections = []

    def engine_for(run):
        connection, engine = connection_engine(live.DATABASE, (run.schema, run.marker, True))
        connection.scalar.side_effect = [live.DATABASE, 0]
        connection.commit.side_effect = RuntimeError("redacted-commit-error")
        connections.append(connection)
        return engine

    monkeypatch.setattr(live, "isolated_engine", Mock(side_effect=engine_for))
    monkeypatch.setattr(live, "cleanup", real_cleanup)
    assert live.main() == 1
    assert any("DROP SCHEMA" in str(c.args[0]) for c in connections[0].execute.call_args_list)
    connections[0].commit.assert_awaited_once()
    captured = capsys.readouterr()
    assert "SCHEMA_CLEANUP_FAILED" in captured.err
    assert "redacted-commit-error" not in captured.err
    assert "Schema cleanup: PASS" not in captured.out
    assert "Overall launcher: PASS" not in captured.out


def test_browser_failure_still_cleans_up_but_never_prints_overall_pass(monkeypatch, capsys):
    cleanup, registry, jobs = completed_browser_world(monkeypatch, exit_code=1)
    assert live.main() == 1
    cleanup.assert_awaited_once()
    captured = capsys.readouterr()
    assert "Browser suite: FAIL" in captured.out and "Schema cleanup: PASS" in captured.out
    assert "BROWSER_FAILED" in captured.err and "Overall launcher: PASS" not in captured.out


@pytest.mark.parametrize(
    "run_id,schema",
    [
        (None, None),
        ("", None),
        ("public", None),
        ("a" * 31, None),
        (None, "public"),
        (None, "cc_test_" + "a" * 32),
        (None, 'cc_live_"; DROP SCHEMA public;--'),
        ("a" * 32, "cc_live_" + "a" * 32),
    ],
)
async def test_inspection_rejects_missing_or_invalid_target_before_connect(
    monkeypatch, run_id, schema
):
    engine = Mock()
    monkeypatch.setattr(live, "create_async_engine", engine)
    with pytest.raises(live.SafetyError):
        await live.inspect_schema(run_id, schema)
    engine.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "malformed",
        SAFE.replace("_db_test", "_db"),
        SAFE.replace("asyncpg", "psycopg"),
        SAFE.replace("127.0.0.1", "remote.invalid"),
        SAFE.replace("5432", "5433"),
        SAFE + "?host=remote.invalid",
    ],
)
async def test_inspection_requires_explicit_valid_test_url_without_fallback(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("TEST_DATABASE_URL", value)
    monkeypatch.setenv("DATABASE_URL", SAFE)
    engine = Mock()
    monkeypatch.setattr(live, "create_async_engine", engine)
    with pytest.raises(live.SafetyError):
        await live.inspect_schema("a" * 32)
    engine.assert_not_called()


@pytest.mark.parametrize("present", [True, False])
@pytest.mark.parametrize("by_schema", [True, False])
async def test_inspection_is_read_only_exact_target_and_metadata_only(
    monkeypatch, run, present, by_schema
):
    monkeypatch.setenv("TEST_DATABASE_URL", SAFE)
    connection, engine = connection_engine(live.DATABASE, None)
    connection.scalar.side_effect = [live.DATABASE, present]
    construct = Mock(return_value=engine)
    monkeypatch.setattr(live, "create_async_engine", construct)
    result = (
        await live.inspect_schema(None, run.schema)
        if by_schema
        else await live.inspect_schema(run.run_id)
    )
    assert result == {
        "schema": run.schema,
        "status": "PRESENT" if present else "ABSENT",
        "read_only": True,
    }
    statements = [str(c.args[0]) for c in connection.execute.call_args_list]
    statements += [str(c.args[0]) for c in connection.scalar.call_args_list]
    assert all(sql.startswith(("SELECT", "SET TRANSACTION READ ONLY")) for sql in statements)
    lookup = connection.scalar.call_args
    assert lookup.args[1] == {"schema": run.schema}
    assert "nspname = :schema" in str(lookup.args[0]) and "LIKE" not in str(lookup.args[0])
    options = construct.call_args.kwargs["connect_args"]
    assert options["server_settings"]["default_transaction_read_only"] == "on"
    assert options["server_settings"]["search_path"] == "pg_catalog"
    connection.commit.assert_not_called()
    engine.dispose.assert_awaited_once()
    assert run.owner not in str(result) and "placeholder" not in str(result)


async def test_inspection_verifies_server_database_before_catalog_lookup(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", SAFE)
    connection, engine = connection_engine("clincoherence_db", None)
    monkeypatch.setattr(live, "create_async_engine", Mock(return_value=engine))
    with pytest.raises(live.SafetyError):
        await live.inspect_schema("a" * 32)
    assert connection.scalar.await_count == 1
    connection.commit.assert_not_called()
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("status,code", [("ABSENT", 0), ("PRESENT", 1)])
def test_inspection_cli_reports_only_named_target_without_overall_pass(
    monkeypatch, capsys, run, status, code
):
    result = {"schema": run.schema, "status": status, "read_only": True}
    inspection = AsyncMock(return_value=result)
    monkeypatch.setattr(live, "inspect_schema", inspection)
    monkeypatch.setattr(
        live.sys, "argv", ["live_support", "inspect-schema", "--run-id", run.run_id]
    )
    assert live.main() == code
    captured = capsys.readouterr()
    assert "Overall launcher" not in captured.out and status in captured.out
    inspection.assert_awaited_once_with(run.run_id, None)


def test_invalid_inspection_cli_returns_nonzero_without_echoing_target(monkeypatch, capsys):
    monkeypatch.setattr(live.sys, "argv", ["live_support", "inspect-schema", "--schema", "private"])
    construct = Mock()
    monkeypatch.setattr(live, "create_async_engine", construct)
    assert live.main() == 1
    construct.assert_not_called()
    captured = capsys.readouterr()
    assert "INSPECTION_REJECTED" in captured.err and "private" not in captured.err


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("redacted-query-detail"), TimeoutError(), KeyboardInterrupt(), SystemExit(0)],
)
def test_inspection_failure_never_asserts_absence_or_exposes_exception(
    monkeypatch, capsys, run, failure
):
    monkeypatch.setattr(live, "inspect_schema", AsyncMock(side_effect=failure))
    monkeypatch.setattr(
        live.sys, "argv", ["live_support", "inspect-schema", "--run-id", run.run_id]
    )
    assert live.main() == 1
    captured = capsys.readouterr()
    assert not captured.out and "INSPECTION_FAILED" in captured.err
    assert "redacted-query-detail" not in captured.err


@pytest.mark.parametrize("result", [None, 0, "false"])
async def test_inspection_unknown_catalog_result_cannot_be_reported_absent(monkeypatch, result):
    monkeypatch.setenv("TEST_DATABASE_URL", SAFE)
    connection, engine = connection_engine(live.DATABASE, None)
    connection.scalar.side_effect = [live.DATABASE, result]
    monkeypatch.setattr(live, "create_async_engine", Mock(return_value=engine))
    with pytest.raises(live.SafetyError, match="Unverifiable"):
        await live.inspect_schema("a" * 32)
    connection.commit.assert_not_called()
    engine.dispose.assert_awaited_once()
