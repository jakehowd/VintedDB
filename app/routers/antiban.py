"""Anti-Ban & IP Shield Management API router."""

from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services.http_client import antiban_client

router = APIRouter(prefix="/api/antiban", tags=["antiban"])


class AntiBanConfigRequest(BaseModel):
    proxies: Optional[str] = Field(None, description="One or more proxy URLs (comma/newline separated)")
    cookies: Optional[str] = Field(None, description="Vinted browser session cookies (e.g. cf_clearance=...; access_token_web=...)")


@router.get("/status")
def get_antiban_status():
    """Return live status of the Anti-Ban TLS engine, active proxy, and circuit breaker."""
    return antiban_client.get_status()


@router.post("/config")
def update_antiban_config(data: AntiBanConfigRequest):
    """Save updated proxy pool and browser session cookies."""
    antiban_client.save_config(
        proxy_str=data.proxies or "",
        cookie_str=data.cookies or ""
    )
    return {
        "ok": True,
        "message": "Anti-Ban configuration updated successfully.",
        "status": antiban_client.get_status()
    }


@router.post("/test")
def test_connection_probe():
    """Perform a live probe against Vinted UK to check for Cloudflare challenges or blocks."""
    probe_result = antiban_client.probe_connection()
    return probe_result


@router.post("/reset-cooldown")
def reset_cooldown():
    """Manually reset the circuit breaker cooldown."""
    antiban_client.reset_circuit_breaker()
    return {
        "ok": True,
        "message": "Circuit breaker cooldown reset.",
        "status": antiban_client.get_status()
    }


@router.post("/harvest")
def harvest_browser_session():
    """Launch local Chromium to solve Cloudflare challenge and harvest cookies automatically."""
    from ..services.session_harvester import session_harvester
    result = session_harvester.harvest(headless=True)
    probe_result = antiban_client.probe_connection()
    return {
        "ok": result.get("ok", False),
        "message": result.get("message", "Session harvest completed."),
        "cookies_count": result.get("cookies_count", 0),
        "probe": probe_result,
        "status": antiban_client.get_status(),
    }


