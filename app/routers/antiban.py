"""Anti-Ban & IP Shield Management API router — strictly isolated per user profile."""

from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..models import User
from ..services.http_client import get_antiban_client

router = APIRouter(prefix="/api/antiban", tags=["antiban"])


class AntiBanConfigRequest(BaseModel):
    proxies: Optional[str] = Field(None, description="One or more proxy URLs (comma/newline separated)")
    cookies: Optional[str] = Field(None, description="Vinted browser session cookies (e.g. cf_clearance=...; access_token_web=...)")


@router.get("/status")
def get_antiban_status(current_user: User = Depends(get_current_user)):
    """Return live status of the Anti-Ban TLS engine, active proxy, and circuit breaker for current user."""
    client = get_antiban_client(current_user.id)
    return client.get_status()


@router.post("/config")
def update_antiban_config(
    data: AntiBanConfigRequest,
    current_user: User = Depends(get_current_user),
):
    """Save updated proxy pool and browser session cookies isolated to the current user profile."""
    client = get_antiban_client(current_user.id)
    client.save_config(
        proxy_str=data.proxies or "",
        cookie_str=data.cookies or "",
    )
    return {
        "ok": True,
        "message": "Anti-Ban configuration updated for your account.",
        "status": client.get_status(),
    }


@router.post("/test")
def test_connection_probe(current_user: User = Depends(get_current_user)):
    """Perform a live probe against Vinted UK using the current user's proxy & cookie configuration."""
    client = get_antiban_client(current_user.id)
    probe_result = client.probe_connection()
    return probe_result


@router.post("/reset-cooldown")
def reset_cooldown(current_user: User = Depends(get_current_user)):
    """Manually reset the circuit breaker cooldown for the current user's client."""
    client = get_antiban_client(current_user.id)
    client.reset_circuit_breaker()
    return {
        "ok": True,
        "message": "Circuit breaker cooldown reset.",
        "status": client.get_status(),
    }


@router.post("/harvest")
def harvest_browser_session(current_user: User = Depends(get_current_user)):
    """Launch local Chromium to solve Cloudflare challenge and harvest cookies for the current user."""
    from ..services.session_harvester import session_harvester
    client = get_antiban_client(current_user.id)
    result = session_harvester.harvest(headless=True)
    probe_result = client.probe_connection()
    return {
        "ok": result.get("ok", False),
        "message": result.get("message", "Session harvest completed."),
        "cookies_count": result.get("cookies_count", 0),
        "probe": probe_result,
        "status": client.get_status(),
    }


