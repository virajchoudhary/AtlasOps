"""Bcrypt hashing and verification utility using the standard `bcrypt` library."""

from __future__ import annotations

from datetime import datetime, timezone
import bcrypt

DEFAULT_BCRYPT_COST = 12


def hash_bcrypt(password: str | bytes, rounds: int | None = None, cost: int | None = None) -> str:
    """Generate an OpenBSD-compatible bcrypt hash ($2b$) using the maintained bcrypt package.

    Args:
        password: The plaintext password (str or bytes).
        rounds: The bcrypt cost factor / logarithmic work factor (default: 12, min: 4, max: 31).
        cost: Alias for rounds.

    Returns:
        The bcrypt hash as a UTF-8 string (e.g. '$2b$12$...').
    """
    effective_rounds = rounds if rounds is not None else (cost if cost is not None else DEFAULT_BCRYPT_COST)
    if effective_rounds < 4 or effective_rounds > 31:
        raise ValueError(f"Bcrypt cost rounds must be between 4 and 31 (got {effective_rounds})")

    pwd_bytes = password.encode("utf-8") if isinstance(password, str) else password
    salt = bcrypt.gensalt(rounds=effective_rounds)
    hashed_bytes = bcrypt.hashpw(pwd_bytes, salt)
    return hashed_bytes.decode("utf-8")


def verify_bcrypt(password: str | bytes, bcrypt_hash: str | bytes) -> bool:
    """Verify a plaintext password against a bcrypt hash ($2a$, $2b$, or $2y$).

    Args:
        password: The plaintext password to test.
        bcrypt_hash: The bcrypt hash string or bytes to test against.

    Returns:
        True if the password matches the hash, False otherwise.
    """
    try:
        pwd_bytes = password.encode("utf-8") if isinstance(password, str) else password
        hash_bytes = bcrypt_hash.encode("utf-8") if isinstance(bcrypt_hash, str) else bcrypt_hash
        return bcrypt.checkpw(pwd_bytes, hash_bytes)
    except Exception:
        return False


def format_iso_timestamp(dt: datetime | None = None) -> str:
    """Format datetime as ISO 8601 UTC string (e.g. 2026-08-16T08:00:00Z)."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
