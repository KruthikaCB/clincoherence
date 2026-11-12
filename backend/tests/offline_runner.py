"""Run pytest without dotenv loading, a database target, or database/HTTP connections.

Usage from backend: python -m tests.offline_runner [pytest arguments]
Existing opt-in PostgreSQL tests skip. This runner never reads project credentials.
"""

import importlib.util
import os
import secrets
import sys
from pathlib import Path
from unittest.mock import patch

import asyncpg
import httpx
from pydantic_settings.sources import DotEnvSettingsSource

try:
    import pytest
except ModuleNotFoundError:
    # Reuse an already installed test runner if the application venv has only
    # runtime dependencies. No downloads or environment modifications on disk.
    sys.path.append(str(Path(sys.base_prefix) / "Lib" / "site-packages"))
    import pytest


def deny_connection(*args: object, **kwargs: object) -> None:
    raise RuntimeError("Network connections are disabled by the offline test runner")


async def deny_async_connection(*args: object, **kwargs: object) -> None:
    deny_connection()


class OfflinePlugin:
    @pytest.fixture(scope="module")
    def anyio_backend(self) -> str:
        return "asyncio"


def main() -> int:
    os.environ.pop("TEST_DATABASE_URL", None)
    os.environ.update(
        DATABASE_URL="postgresql+asyncpg://offline:offline@127.0.0.1:1/offline_unused",
        SECRET_KEY=secrets.token_urlsafe(48),
        APP_ENV="production", ENABLE_DEMO_REGISTRATION="false", ENABLE_DEMO_PATIENTS="false",
        LLM_PROVIDER="mock", LLM_BASE_URL="http://127.0.0.1:1", LLM_MODEL_NAME="qwen3:0.6b",
    )
    with (
        patch.object(DotEnvSettingsSource, "_read_env_files", return_value={}),
        patch.object(asyncpg, "connect", deny_async_connection),
        patch.object(httpx.HTTPTransport, "handle_request", deny_connection),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", deny_async_connection),
    ):
        # AnyIO is already a FastAPI dependency. Its asyncio backend can execute
        # coroutine tests when this local environment lacks pytest-asyncio.
        fallback = (
            ["--anyio-mode=auto"] if importlib.util.find_spec("pytest_asyncio") is None else []
        )
        return pytest.main([*fallback, *sys.argv[1:]], plugins=[OfflinePlugin()])


if __name__ == "__main__":
    raise SystemExit(main())
