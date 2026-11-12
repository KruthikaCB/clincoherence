"""
Application configuration using Pydantic Settings.

Loads configuration from environment variables and .env files.
"""

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        # Root template is documented; backend-local values may override it.
        env_file=(
            Path(__file__).resolve().parents[3] / ".env",
            Path(__file__).resolve().parents[2] / ".env",
        ),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- Application ---
    PROJECT_NAME: str = "ClinCoherence"
    API_V1_PREFIX: str = "/api/v1"
    APP_ENV: str = "production"
    ENABLE_DEMO_REGISTRATION: bool = False
    ENABLE_DEMO_PATIENTS: bool = False
    # Migration 0011 is not validated. Existing demo paths must not expose M8 APIs.
    ENABLE_CLINICIAN_FACT_REVIEW: bool = False

    # --- Database ---
    DATABASE_URL: str = (
        "postgresql+asyncpg://clincoherence_user:changeme@localhost:5432/clincoherence_db"
    )

    # --- Authentication ---
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Controlled synthetic-user onboarding only; no production identity assurance.
    INVITATION_URL_ORIGIN: str = "http://localhost:3000"
    INVITATION_EXPIRE_MINUTES: int = Field(default=1440, ge=1, le=10080)

    @field_validator("INVITATION_URL_ORIGIN")
    @classmethod
    def invitation_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        # Accessing port also rejects malformed/out-of-range port values.
        _ = parsed.port
        if (
            any(ord(character) <= 32 for character in value)
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (
                parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            )
        ):
            raise ValueError("Use an HTTPS origin, or HTTP loopback origin for local research")
        return value.rstrip("/")

    # --- CORS ---
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # --- LLM ---
    LLM_PROVIDER: str = "mock"
    LLM_API_KEY: str | None = None
    LLM_MODEL_NAME: str | None = None
    LLM_BASE_URL: str | None = None
    LLM_EXTRACTION_TIMEOUT_SECONDS: int = Field(default=120, ge=5, le=300)


settings = Settings()
