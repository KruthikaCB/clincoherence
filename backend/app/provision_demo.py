"""Explicit local-development-only practitioner bootstrap; never imported by the API."""

import asyncio
import sys
import warnings
from getpass import GetPassWarning, getpass

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.security import get_password_hash
from app.models.identity import PractitionerProfile, User

IDENTITY = "doctor-demo@synthetic.invalid"


def validate_target(value: str) -> None:
    """No aliases, alternate ports, query overrides, or non-development databases."""
    try:
        url = make_url(value)
        valid = (
            url.drivername == "postgresql+asyncpg"
            and url.host == "localhost"
            and url.port == 5432
            and url.database == "clincoherence_db"
            and not url.query
            and bool(url.username)
            and bool(url.password)
        )
    except Exception:
        valid = False
    if not valid:
        raise ValueError("Refused: explicit localhost:5432/clincoherence_db required")


def read_password() -> str:
    """Refuse redirected input and getpass's insecure echo fallback."""
    if not sys.stdin.isatty():
        raise ValueError("Interactive terminal required")
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        password = getpass("Demo practitioner password (12–1024 characters): ")
        confirmation = getpass("Confirm password: ")
    if not 12 <= len(password) <= 1024 or password != confirmation:
        raise ValueError("Password length or confirmation invalid")
    return password


async def insert_practitioner(session: AsyncSession, password: str) -> None:
    """Caller owns the transaction. No existing account is ever changed."""
    if not 12 <= len(password) <= 1024:
        raise ValueError("Invalid password length")
    row = (
        await session.execute(
            text("SELECT current_database(), inet_server_addr()::text, inet_server_port()")
        )
    ).one()
    if row[0] != "clincoherence_db" or row[1] not in {"127.0.0.1", "::1"} or row[2] != 5432:
        raise ValueError("Connected database is not the local development target")
    if await session.scalar(select(User.id).where(User.identity == IDENTITY)) is not None:
        raise ValueError("Demo account already exists; no changes made")
    account = User(
        identity=IDENTITY,
        hashed_password=get_password_hash(password),
        role="practitioner",
        is_active=True,
    )
    session.add(account)
    await session.flush()
    session.add(PractitionerProfile(account=account, is_authorized=True))
    await session.flush()


async def provision(url: str, password: str) -> None:
    validate_target(url)
    engine = create_async_engine(
        url,
        echo=False,
        hide_parameters=True,
        connect_args={"timeout": 10, "command_timeout": 15},
    )
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '5s'"))
            await insert_practitioner(session, password)
    finally:
        await engine.dispose()


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise ValueError("No command arguments accepted")
        validate_target(settings.DATABASE_URL)
        password = read_password()
        asyncio.run(provision(settings.DATABASE_URL, password))
    except (Exception, KeyboardInterrupt):
        # Never print driver exceptions, parameters, URLs, hashes, or input.
        print(
            "Provisioning unsuccessful. No success confirmed; check target, migrations and "
            "duplicate account status. Do not assume a connection-loss retry is safe.",
            file=sys.stderr,
        )
        return 1
    print("Synthetic demo practitioner created. No patient or consent created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
