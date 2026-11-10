"""Fail-closed local deployment and connected database guard for demo ownership."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.provision_demo import validate_target


def enabled() -> bool:
    if not (
        settings.APP_ENV == "development"
        and settings.ENABLE_DEMO_PATIENTS
        and settings.LLM_PROVIDER in {"mock", "ollama"}
    ):
        return False
    try:
        validate_target(settings.DATABASE_URL)
    except ValueError:
        return False
    return True


async def verified_database(db: AsyncSession) -> bool:
    if not enabled():
        return False
    row = (
        await db.execute(
            text("SELECT current_database(), host(inet_server_addr()), inet_server_port()")
        )
    ).one()
    return row[0] == "clincoherence_db" and row[1] in {"127.0.0.1", "::1"} and row[2] == 5432
