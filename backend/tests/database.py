"""Shared opt-in PostgreSQL fixture; no development database fallback."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


def migration(name="0001_foundational_identity"):
    path = Path(__file__).resolve().parents[1] / f"alembic/versions/{name}.py"
    spec = importlib.util.spec_from_file_location("initial_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrations():
    return [
        migration("0001_foundational_identity"),
        migration("0002_patient_invitations"),
        migration("0003_consent_history"),
    ]


@pytest.fixture
async def db():
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip(
            "PostgreSQL integration requires explicit TEST_DATABASE_URL (database ending _test)"
        )
    url = make_url(value)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").endswith("_test"):
        pytest.fail(
            "Refusing integration target: require postgresql+asyncpg and database ending _test"
        )
    engine = create_async_engine(url, connect_args={"timeout": 5})
    schema = "cc_test_" + uuid4().hex
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'SET search_path TO "{schema}"'))

            def upgrade(sync_connection):
                context = MigrationContext.configure(sync_connection)
                with Operations.context(context):
                    for revision in migrations():
                        revision.upgrade()

            await connection.run_sync(upgrade)
            await connection.commit()
            try:
                async with AsyncSession(connection, expire_on_commit=False) as session:
                    yield session
            finally:
                await connection.rollback()
                # Only this fixture's random schema in an explicitly selected test DB.
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                await connection.commit()
    finally:
        await engine.dispose()
