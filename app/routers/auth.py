"""Authentication and profile management router."""

from datetime import datetime, timezone
import re
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import (
    SESSION_COOKIE_NAME,
    SESSION_DURATION_DAYS,
    check_login_rate_limit,
    clear_failed_logins,
    create_session,
    delete_all_user_sessions,
    delete_session,
    extract_token_from_request,
    get_current_user,
    hash_password,
    record_failed_login,
    verify_password,
    verify_password_constant_time,
)
from ..database import get_db
from ..models import TrackedSearch, User, UserSession

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Schemas ─────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., description="User profile username")
    password: str = Field(..., description="User profile password")
    remember_me: bool = Field(True, description="Keep session active for 30 days")


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=40, description="Desired username")
    password: str = Field(..., min_length=3, max_length=128, description="Desired password")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., description="Current account password")
    new_password: str = Field(..., min_length=4, max_length=128, description="New account password")


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Authenticate with username and password, enforcing brute-force rate limits and constant-time verification."""
    username_clean = payload.username.strip()
    if not username_clean:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username cannot be empty",
        )

    client_ip = (request.client.host if request.client else None) or "127.0.0.1"
    allowed, retry_after = check_login_rate_limit(client_ip, username_clean)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed sign-in attempts. Please wait {retry_after} seconds before trying again.",
            headers={"Retry-After": str(retry_after)},
        )

    # Case-insensitive username lookup
    user = (
        db.query(User)
        .filter(func.lower(User.username) == func.lower(username_clean))
        .first()
    )

    # Constant-time password check prevents username enumeration via timing side-channels
    is_valid = verify_password_constant_time(payload.password, user.password_hash if user else None)
    if not user or not is_valid:
        remaining = record_failed_login(client_ip, username_clean)
        msg = "Invalid username or password."
        if 0 < remaining <= 3:
            msg = f"Invalid username or password ({remaining} attempts remaining before temporary lockout)."
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=msg,
        )

    clear_failed_logins(client_ip, username_clean)

    # Create session (30 days if remember_me, else 1 day)
    duration_days = SESSION_DURATION_DAYS if payload.remember_me else 1
    session = create_session(db, user.id, duration_days=duration_days)

    # Set secure HttpOnly cookie
    cookie_kwargs: Dict[str, Any] = {
        "key": SESSION_COOKIE_NAME,
        "value": session.session_token,
        "httponly": True,
        "samesite": "lax",
        "secure": False,  # Allow both local HTTP development and HTTPS
        "path": "/",
    }
    if payload.remember_me:
        cookie_kwargs["max_age"] = SESSION_DURATION_DAYS * 86400
    response.set_cookie(**cookie_kwargs)

    search_count = (
        db.query(func.count(TrackedSearch.id))
        .filter(TrackedSearch.user_id == user.id)
        .scalar()
        or 0
    )

    return {
        "ok": True,
        "token": session.session_token,
        "user": {
            "id": user.id,
            "username": user.username,
            "is_admin": user.is_admin,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "search_count": search_count,
        },
    }


@router.post("/register")
def register(
    payload: RegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    """Register a new user profile with 100% isolated data storage and log in immediately."""
    username_clean = payload.username.strip()
    if len(username_clean) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username must be at least 2 characters long",
        )

    # Safe characters check
    if not re.match(r"^[a-zA-Z0-9_\-\. ]+$", username_clean):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username can only contain letters, numbers, spaces, underscores, and dashes",
        )

    if len(payload.password) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 3 characters long",
        )

    # Check for existing user (case-insensitive)
    existing = (
        db.query(User)
        .filter(func.lower(User.username) == func.lower(username_clean))
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Username '{username_clean}' is already registered. Please sign in instead.",
        )

    p_hash = hash_password(payload.password)
    new_user = User(
        username=username_clean,
        password_hash=p_hash,
        is_admin=False,
        antiban_config_json=None,
        ai_config_json=None,
        scheduler_config_json=None,
        sniper_cleared_deals_json=None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Create session
    session = create_session(db, new_user.id)

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session.session_token,
        max_age=SESSION_DURATION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )

    return {
        "ok": True,
        "token": session.session_token,
        "user": {
            "id": new_user.id,
            "username": new_user.username,
            "is_admin": new_user.is_admin,
            "created_at": new_user.created_at.isoformat() if new_user.created_at else None,
            "search_count": 0,
        },
    }


@router.get("/me")
def get_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return currently authenticated profile details."""
    search_count = (
        db.query(func.count(TrackedSearch.id))
        .filter(TrackedSearch.user_id == current_user.id)
        .scalar()
        or 0
    )
    active_sessions_count = (
        db.query(func.count(UserSession.id))
        .filter(UserSession.user_id == current_user.id)
        .scalar()
        or 1
    )

    return {
        "ok": True,
        "user": {
            "id": current_user.id,
            "username": current_user.username,
            "is_admin": current_user.is_admin,
            "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
            "search_count": search_count,
            "active_sessions_count": active_sessions_count,
        },
    }


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verify current password and update to a new PBKDF2-HMAC-SHA256 password hash."""
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    if len(payload.new_password) < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 4 characters long.",
        )

    current_user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True, "message": "Password updated successfully."}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Invalidate current session and clear session cookie."""
    token = extract_token_from_request(request)
    if token:
        delete_session(db, token)

    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"ok": True, "message": "Signed out successfully"}


@router.post("/logout-all")
def logout_all_sessions(
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Revoke all active sessions for the current user across all devices."""
    revoked = delete_all_user_sessions(db, current_user.id)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"ok": True, "revoked_count": revoked, "message": f"Signed out of all {revoked} active session(s)."}


@router.get("/profiles")
def list_profiles(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin-only endpoint to inspect registered profiles. Non-admin users cannot enumerate accounts."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    users = db.query(User).order_by(User.id).all()
    results = []
    for u in users:
        cnt = (
            db.query(func.count(TrackedSearch.id))
            .filter(TrackedSearch.user_id == u.id)
            .scalar()
            or 0
        )
        results.append(
            {
                "id": u.id,
                "username": u.username,
                "is_admin": u.is_admin,
                "search_count": cnt,
                "is_current": (u.id == current_user.id),
            }
        )
    return {"ok": True, "profiles": results}

