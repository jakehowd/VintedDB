"""Authentication and profile management router."""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import (
    SESSION_COOKIE_NAME,
    SESSION_DURATION_DAYS,
    create_session,
    delete_session,
    extract_token_from_request,
    get_current_user,
    hash_password,
    verify_password,
)
from ..database import get_db
from ..models import TrackedSearch, User, UserSession

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Schemas ─────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., description="User profile username")
    password: str = Field(..., description="User profile password")


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=40, description="Desired username")
    password: str = Field(..., min_length=3, max_length=128, description="Desired password")


class UserResponse(BaseModel):
    id: int
    username: str
    is_admin: bool
    created_at: Optional[str] = None
    search_count: int = 0


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/login")
def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    """Authenticate with username and password, setting secure session cookie and returning session token."""
    username_clean = payload.username.strip()
    if not username_clean:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username cannot be empty",
        )

    # Case-insensitive username lookup
    user = (
        db.query(User)
        .filter(func.lower(User.username) == func.lower(username_clean))
        .first()
    )

    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Create session
    session = create_session(db, user.id)

    # Set secure HttpOnly cookie
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session.session_token,
        max_age=SESSION_DURATION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=False,  # Allow both local HTTP development and HTTPS
        path="/",
    )

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
    """Register a new user profile with isolated data storage and log in immediately."""
    username_clean = payload.username.strip()
    if len(username_clean) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username must be at least 2 characters long",
        )

    # Basic safe characters check
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

    return {
        "ok": True,
        "user": {
            "id": current_user.id,
            "username": current_user.username,
            "is_admin": current_user.is_admin,
            "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
            "search_count": search_count,
        },
    }


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


@router.get("/profiles")
def list_profiles(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List registered profiles (for quick profile inspection/switching)."""
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

