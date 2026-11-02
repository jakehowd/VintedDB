"""Authentication utilities, password hashing, rate limiting, and session verification."""

from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import logging
import secrets
import threading
import time
from typing import Dict, List, Optional, Tuple

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .database import get_db
from .models import User, UserSession

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "vinted_session"
SESSION_DURATION_DAYS = 30
PBKDF2_ITERATIONS = 200_000

# Context variable tracking the currently executing user_id across sync/async/worker contexts
_active_user_id_ctx: ContextVar[Optional[int]] = ContextVar("active_user_id", default=None)


def get_active_user_id() -> Optional[int]:
    """Return the user_id currently bound to the active request or background job context."""
    return _active_user_id_ctx.get()


def set_active_user_id(user_id: Optional[int]):
    """Bind user_id to the current execution context."""
    return _active_user_id_ctx.set(user_id)


# Pre-computed dummy hash for constant-time verification when username does not exist
_DUMMY_SALT = b"0123456789abcdef"
_DUMMY_KEY = hashlib.pbkdf2_hmac("sha256", b"dummy_password_constant_time", _DUMMY_SALT, PBKDF2_ITERATIONS)
_DUMMY_HASH = f"{_DUMMY_SALT.hex()}${_DUMMY_KEY.hex()}"

# Brute-force rate limiter state: key -> list of failed attempt timestamps
_rate_limit_lock = threading.Lock()
_failed_attempts: Dict[str, List[float]] = {}
MAX_FAILED_ATTEMPTS = 8
LOCKOUT_WINDOW_SECONDS = 300  # 5 minutes


def check_login_rate_limit(client_ip: str, username: str) -> Tuple[bool, int]:
    """Check if client_ip or username is currently rate-limited due to failed login attempts.

    Returns (is_allowed, retry_after_seconds).
    """
    now = time.time()
    cutoff = now - LOCKOUT_WINDOW_SECONDS
    keys = [f"ip:{client_ip}", f"user:{username.lower().strip()}"]
    with _rate_limit_lock:
        for k in keys:
            attempts = [ts for ts in _failed_attempts.get(k, []) if ts > cutoff]
            if attempts:
                _failed_attempts[k] = attempts
            else:
                _failed_attempts.pop(k, None)
            if len(attempts) >= MAX_FAILED_ATTEMPTS:
                oldest = min(attempts)
                retry_after = max(1, int(LOCKOUT_WINDOW_SECONDS - (now - oldest)))
                return False, retry_after
    return True, 0


def record_failed_login(client_ip: str, username: str) -> int:
    """Record a failed login attempt and return remaining attempts before temporary lockout."""
    now = time.time()
    cutoff = now - LOCKOUT_WINDOW_SECONDS
    ip_key = f"ip:{client_ip}"
    user_key = f"user:{username.lower().strip()}"
    with _rate_limit_lock:
        for k in (ip_key, user_key):
            attempts = [ts for ts in _failed_attempts.get(k, []) if ts > cutoff]
            attempts.append(now)
            _failed_attempts[k] = attempts
        used = max(len(_failed_attempts.get(ip_key, [])), len(_failed_attempts.get(user_key, [])))
        return max(0, MAX_FAILED_ATTEMPTS - used)


def clear_failed_logins(client_ip: str, username: str) -> None:
    """Clear failed login counters upon successful authentication."""
    with _rate_limit_lock:
        _failed_attempts.pop(f"ip:{client_ip}", None)
        _failed_attempts.pop(f"user:{username.lower().strip()}", None)


def hash_password(password: str) -> str:
    """Hash password with PBKDF2-HMAC-SHA256 and a random 16-byte salt."""
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${key.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against stored salt and PBKDF2 hash using timing-safe comparison."""
    if not hashed or "$" not in hashed:
        return False
    try:
        salt_hex, key_hex = hashed.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected_key = bytes.fromhex(key_hex)
        candidate_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
        return hmac.compare_digest(candidate_key, expected_key)
    except Exception as exc:
        logger.warning("Error verifying password: %s", exc)
        return False


def verify_password_constant_time(password: str, user_hash: Optional[str]) -> bool:
    """Verify password in constant time even if the user record does not exist."""
    target_hash = user_hash if (user_hash and "$" in user_hash) else _DUMMY_HASH
    matched = verify_password(password, target_hash)
    return bool(matched and user_hash)


def create_session(db: Session, user_id: int, duration_days: int = SESSION_DURATION_DAYS) -> UserSession:
    """Create and persist a new cryptographic session token for the user."""
    token = secrets.token_urlsafe(32)
    now_utc = datetime.now(timezone.utc)
    expires_at = now_utc + timedelta(days=max(1, duration_days))
    session = UserSession(
        user_id=user_id,
        session_token=token,
        created_at=now_utc,
        expires_at=expires_at,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def delete_session(db: Session, token: str) -> bool:
    """Delete a session token on logout."""
    if not token:
        return False
    sess = db.query(UserSession).filter(UserSession.session_token == token).first()
    if sess:
        db.delete(sess)
        db.commit()
        return True
    return False


def delete_all_user_sessions(db: Session, user_id: int) -> int:
    """Revoke all active sessions for a user across all browsers/devices."""
    deleted = db.query(UserSession).filter(UserSession.user_id == user_id).delete(synchronize_session=False)
    db.commit()
    return deleted


def extract_token_from_request(request: Request) -> Optional[str]:
    """Extract session token from Authorization header or HttpOnly cookie."""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token:
            return token

    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        return cookie_token.strip()

    return None


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency — returns the authenticated User or raises 401, binding active_user_id context."""
    token = extract_token_from_request(request)
    if not token:
        set_active_user_id(None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    now_utc = datetime.now(timezone.utc)
    session = (
        db.query(UserSession)
        .filter(UserSession.session_token == token)
        .first()
    )

    if not session:
        set_active_user_id(None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check expiry (make sure timezone-aware comparison)
    exp = session.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now_utc:
        db.delete(session)
        db.commit()
        set_active_user_id(None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = session.user
    if not user:
        set_active_user_id(None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    set_active_user_id(user.id)
    return user


