"""Endpoints for batch scraping and scheduler/polling controls."""

from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..scheduler import (
    batch_scrape_searches,
    get_scheduler_status,
    update_scheduler_settings,
)

router = APIRouter(prefix="/api/scraper", tags=["scraper-ops"])


class BatchScrapeRequest(BaseModel):
    search_ids: Optional[List[int]] = Field(None, description="Optional search IDs to scrape. If omitted, scrapes all active.")


class ScheduleUpdateRequest(BaseModel):
    interval_minutes: Optional[int] = Field(None, description="Polling interval in minutes")
    constant_scrape: Optional[bool] = Field(None, description="Toggle continuous background scraping")


@router.post("/batch-scrape")
def run_batch_scrape(data: BatchScrapeRequest):
    """Trigger an immediate batch scrape across multiple selected or all active items."""
    try:
        summary = batch_scrape_searches(search_ids=data.search_ids)
        return summary
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Batch scrape failed: {exc}")


@router.get("/schedule")
def get_schedule():
    """Get current scheduler configuration, interval, and constant scrape state."""
    return get_scheduler_status()


@router.post("/schedule")
def update_schedule(data: ScheduleUpdateRequest):
    """Update auto-poll interval or toggle constant continuous scrape."""
    new_status = update_scheduler_settings(
        interval_minutes=data.interval_minutes,
        constant_scrape=data.constant_scrape,
    )
    return new_status


@router.get("/live-feed")
def get_live_scraper_feed(
    since_id: Optional[int] = None,
    filter_type: Optional[str] = "all",
    limit: Optional[int] = 150,
):
    """Return recent scraper and sniper activity events with highlight metadata."""
    from ..services.scraper_logger import scraper_logger
    feed_data = scraper_logger.get_feed(
        since_id=since_id,
        filter_type=filter_type,
        limit=min(300, max(10, limit or 150)),
    )
    # Include current scheduler telemetry for the terminal header
    scheduler_status = get_scheduler_status()
    feed_data["scheduler"] = {
        "is_cycle_running": scheduler_status.get("is_cycle_running", False),
        "current_search_text": scheduler_status.get("current_search_text"),
        "current_search_index": scheduler_status.get("current_search_index", 0),
        "total_searches": scheduler_status.get("total_searches_in_cycle", 0),
        "constant_scrape_active": scheduler_status.get("constant_scrape", False),
        "constant_phase": scheduler_status.get("constant_scrape_telemetry", {}).get("phase", "idle"),
        "interval_minutes": scheduler_status.get("interval_minutes", 30),
    }
    return feed_data


@router.post("/clear-feed")
def clear_live_scraper_feed():
    """Clear in-memory activity events and reset session counters."""
    from ..services.scraper_logger import scraper_logger
    scraper_logger.clear()
    return {"status": "ok", "message": "Scraper terminal feed cleared"}


