"""Offline bootstrap tests: no database connections or provisioning execution."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import provision_demo as demo
from app.core.security import verify_password
from app.models.identity import PractitionerProfile, User

URL = "postgresql+asyncpg://synthetic:placeholder@localhost:5432/clincoherence_db"


@pytest.mark.parametrize(
    "value",
    [
        "",
        URL.replace("localhost", "127.0.0.1"),
        URL.replace("5432", "5433"),
        URL.replace(":5432", ""),
        URL.replace("clincoherence_db", "clincoherence_db_test"),
        URL.replace("localhost", "remote.invalid"),
        URL + "?host=remote.invalid",
        URL.replace("+asyncpg", ""),
        URL.replace("placeholder", ""),
    ],
)
def test_reject_target(value):
    with pytest.raises(ValueError):
        demo.validate_target(value)


def test_valid_target():
    demo.validate_target(URL)


def test_prompt():
    with (
        patch.object(demo.sys.stdin, "isatty", return_value=True),
        patch.object(demo, "getpass", side_effect=["synthetic-password", "synthetic-password"]),
    ):
        assert demo.read_password() == "synthetic-password"


@pytest.mark.parametrize(
    "values", [["short", "short"], ["a" * 1025] * 2, ["synthetic-password", "different-password"]]
)
def test_bad_password(values):
    with (
        patch.object(demo.sys.stdin, "isatty", return_value=True),
        patch.object(demo, "getpass", side_effect=values),
        pytest.raises(ValueError),
    ):
        demo.read_password()


def test_noninteractive_refused():
    with (
        patch.object(demo.sys.stdin, "isatty", return_value=False),
        patch.object(demo, "getpass") as prompt,
        pytest.raises(ValueError),
    ):
        demo.read_password()
    prompt.assert_not_called()


def session_for(row, existing=None):
    session = MagicMock()
    result = MagicMock()
    result.one.return_value = row
    session.execute = AsyncMock(return_value=result)
    session.scalar = AsyncMock(return_value=existing)
    session.flush = AsyncMock()
    return session


async def test_creates_only_practitioner_with_real_hash():
    session = session_for(("clincoherence_db", "127.0.0.1", 5432))
    await demo.insert_practitioner(session, "synthetic-password")
    account, profile = [call.args[0] for call in session.add.call_args_list]
    assert isinstance(account, User) and isinstance(profile, PractitionerProfile)
    assert account.identity == demo.IDENTITY and account.role == "practitioner"
    assert account.is_active and profile.is_authorized and profile.account is account
    assert account.hashed_password != "synthetic-password"
    assert verify_password("synthetic-password", account.hashed_password)
    session.commit.assert_not_called()


async def test_duplicate_never_modified():
    session = session_for(("clincoherence_db", "::1", 5432), existing="existing")
    with pytest.raises(ValueError):
        await demo.insert_practitioner(session, "synthetic-password")
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.parametrize(
    "row",
    [
        ("clincoherence_db_test", "127.0.0.1", 5432),
        ("clincoherence_db", "192.0.2.1", 5432),
        ("clincoherence_db", "::1", 5433),
    ],
)
async def test_connected_target_checked_before_writes(row):
    session = session_for(row)
    with pytest.raises(ValueError):
        await demo.insert_practitioner(session, "synthetic-password")
    session.scalar.assert_not_awaited()
    session.add.assert_not_called()


async def test_invalid_url_never_creates_engine():
    with patch.object(demo, "create_async_engine") as engine, pytest.raises(ValueError):
        await demo.provision(URL + "?host=remote.invalid", "synthetic-password")
    engine.assert_not_called()


def test_errors_sanitized(capsys):
    with (
        patch.object(demo.sys, "argv", ["provision"]),
        patch.object(demo, "validate_target", side_effect=RuntimeError("SECRET_SENTINEL")),
    ):
        assert demo.main() == 1
    captured = capsys.readouterr()
    assert "SECRET_SENTINEL" not in captured.err and not captured.out


def test_echo_fallback_refused():
    with (
        patch.object(demo.sys.stdin, "isatty", return_value=True),
        patch.object(demo, "getpass", side_effect=demo.GetPassWarning("cannot hide input")),
        pytest.raises(demo.GetPassWarning),
    ):
        demo.read_password()


@pytest.mark.parametrize("failed", [False, True])
async def test_transaction_exit_and_disposal(failed):
    session = MagicMock()
    session.execute = AsyncMock()
    transaction = AsyncMock()
    session.begin.return_value = transaction
    context = AsyncMock()
    context.__aenter__.return_value = session
    factory = MagicMock(return_value=context)
    engine = MagicMock()
    engine.dispose = AsyncMock()
    insertion = AsyncMock(side_effect=RuntimeError("insertion failed") if failed else None)
    with (
        patch.object(demo, "create_async_engine", return_value=engine),
        patch.object(demo, "async_sessionmaker", return_value=factory),
        patch.object(demo, "insert_practitioner", insertion),
    ):
        if failed:
            with pytest.raises(RuntimeError):
                await demo.provision(URL, "synthetic-password")
        else:
            await demo.provision(URL, "synthetic-password")
    transaction.__aenter__.assert_awaited_once()
    exception_type = transaction.__aexit__.await_args.args[0]
    assert exception_type is (RuntimeError if failed else None)
    engine.dispose.assert_awaited_once()
