"""
Security utilities for authentication and authorization.

Provides JWT token management and password hashing.
"""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import bcrypt
from jose import jwt  # type: ignore[import-untyped]
from passlib.context import CryptContext  # type: ignore[import-untyped]

from app.core.config import settings

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], pbkdf2_sha256__rounds=600_000)


def signing_key() -> str:
    """Never issue or accept tokens signed with the scaffold's public default."""
    key = settings.SECRET_KEY
    if (
        settings.ALGORITHM != "HS256"
        or len(key.encode("utf-8")) < 32
        or key
        in {"dev-secret-key-change-in-production", "your-secret-key-here-change-in-production"}
    ):
        raise ValueError("Configure a private SECRET_KEY of at least 32 bytes and HS256")
    return key


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed password."""
    try:
        if hashed_password.startswith(("$2a$", "$2b$", "$2y$")):
            # Passlib 1.7's bcrypt backend fails with bcrypt 5; use its existing
            # installed dependency directly, never silently truncate passwords.
            encoded = plain_password.encode("utf-8")
            return len(encoded) <= 72 and bcrypt.checkpw(encoded, hashed_password.encode("ascii"))
        return cast("bool", pwd_context.verify(plain_password, hashed_password))
    except (ValueError, TypeError, UnicodeError):
        return False


def get_password_hash(password: str) -> str:
    """Hash a password using salted PBKDF2-SHA256 (600,000 iterations)."""
    return cast("str", pwd_context.hash(password))


def create_access_token(
    subject: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT access token.

    Args:
        subject: The account UUID; no role or patient permission is embedded.
        expires_delta: Optional custom expiration duration.

    Returns:
        Encoded JWT token string.
    """
    subject = str(UUID(subject))
    now = datetime.now(UTC)
    expire = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    to_encode = {
        "sub": subject,
        "exp": expire,
        "iat": now,
        "iss": "clincoherence",
        "aud": "clincoherence-api",
        "token_type": "access",
    }
    return cast("str", jwt.encode(to_encode, signing_key(), algorithm="HS256"))


def decode_access_token(token: str) -> UUID:
    """Validate signature, algorithm, required claims, expiry and account ID."""
    claims = jwt.decode(
        token,
        signing_key(),
        algorithms=["HS256"],
        audience="clincoherence-api",
        issuer="clincoherence",
        options={
            "require_exp": True,
            "require_sub": True,
            "require_iat": True,
            "require_aud": True,
            "require_iss": True,
        },
    )
    if claims.get("token_type") != "access":
        raise ValueError("Not an access token")
    return UUID(claims["sub"])
