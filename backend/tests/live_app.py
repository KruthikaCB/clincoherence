"""Dedicated test-only ASGI factory; never registered as a production endpoint."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from sqlalchemy import event

from tests.live_support import DATABASE, Run, SafetyError, connection_options, dedicated_url

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


def verify_factories(engine: Any, factory: Any, release_factory: Any, run: Run) -> None:
    if (
        engine.url != run.url
        or factory.kw.get("bind") is not engine
        or release_factory() is not factory
    ):
        raise SafetyError("Application session factory isolation failed")


def install_guards(engine: Any, run: Run) -> None:
    @event.listens_for(engine.sync_engine, "do_connect")
    def configure(dialect: Any, record: Any, args: Any, params: Any) -> None:
        params.update(connection_options(run))

    @event.listens_for(engine.sync_engine, "checkout")
    def verify(connection: Any, record: Any, proxy: Any) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT current_database(), current_schema(), "
                "current_setting('search_path'), obj_description(oid, 'pg_namespace'), "
                "nspowner = (SELECT oid FROM pg_roles WHERE rolname = current_user) "
                "FROM pg_namespace WHERE nspname = '" + run.schema + "'"
            )
            if cursor.fetchone() != (DATABASE, run.schema, run.schema, run.marker, True):
                raise SafetyError("Application database/schema checkout verification failed")
        finally:
            cursor.close()
            # Checkout verification must not leave an implicit transaction open.
            connection.rollback()


class IdentityWrapper:
    """Pure ASGI header injection: never buffers or replaces the 4H send boundary."""

    def __init__(self, app: Any, run_id: str) -> None:
        self.app = app
        self.run_id = run_id.encode("ascii")

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        async def identified(message: Any) -> None:
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (b"x-clincoherence-live-run", self.run_id),
                    ],
                }
            await send(message)

        await self.app(scope, receive, identified)


def create_live_app() -> Any:
    run = Run.from_env()
    if dedicated_url(os.environ.get("DATABASE_URL")) != run.url:
        raise SafetyError("Application DATABASE_URL must equal the explicit test URL")
    # Validate before any app imports, including import-time settings and engine construction.
    from app.core import database
    from app.core.config import settings

    if run.url.render_as_string(hide_password=False) != settings.DATABASE_URL:
        raise SafetyError("Effective application configuration mismatch")
    if settings.LLM_PROVIDER != "mock" or settings.INVITATION_URL_ORIGIN != "http://127.0.0.1:3010":
        raise SafetyError("Live fixture configuration mismatch")
    from app.api.v1.verified_records import release_session_factory
    from app.main import create_app

    verify_factories(database.engine, database.async_session_factory, release_session_factory, run)
    install_guards(database.engine, run)
    app = create_app()
    original_lifespan = app.router.lifespan_context

    from tests.live_support import verify_connection

    @asynccontextmanager
    async def guarded_lifespan(application: Any) -> AsyncGenerator[None, None]:
        try:
            async with database.engine.connect() as connection:
                await verify_connection(connection, run, owned=True)
            async with original_lifespan(application):
                yield
        finally:
            await database.engine.dispose()

    app.router.lifespan_context = guarded_lifespan

    return IdentityWrapper(app, run.run_id)
