"""
Application configuration using Pydantic Settings.

Loads configuration from environment variables and .env files.
"""

from pathlib import Path

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

    # --- Database ---
    DATABASE_URL: str = (
        "postgresql+asyncpg://clincoherence_user:changeme@localhost:5432/clincoherence_db"
    )

    # --- Authentication ---
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # --- CORS ---
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # --- LLM ---
    LLM_PROVIDER: str = "mock"
    LLM_API_KEY: str | None = None
    LLM_MODEL_NAME: str | None = None
    LLM_BASE_URL: str | None = None


settings = Settings()
