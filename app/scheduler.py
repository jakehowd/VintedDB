"""Background scheduler & batch scraper manager.

Supports:
- Periodic auto-polling with dynamically adjustable interval and live countdown timer.
- High-efficiency Constant Scrape mode (continuous 24/7 background cycle with live telemetry).
- Batch scraping of multiple / all selected tracked items on demand.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler

from .config import POLL_INTERVAL_MINUTES, SCHEDULER_CONFIG_PATH
from .database import SessionLocal
from .models import TrackedSearch
from .scraper import scrape_and_store

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone=timezone.utc)

# Global state for scheduler & constant scrape management
_scheduler_lock = threading.RLock()
_cycle_lock = threading.Lock()

_current_interval_minutes: int = POLL_INTERVAL_MINUTES
_constant_scrape_active: bool = False
_constant_scrape_thread: Optional[threading.Thread] = None
_constant_scrape_stop = threading.Event()

# Live cycle telemetry
_is_cycle_running: bool = False
_current_search_text: Optional[str] = None
_current_search_index: int = 0
_total_searches_in_cycle: int = 0
_constant_scrape_phase: str = "idle"  # "idle" | "scraping" | "auditing" | "cooldown"
_constant_scrape_next_cycle_at: Optional[datetime] = None
_constant_scrape_cycles_count: int = 0
_constant_scrape_total_new: int = 0

_last_cycle_time: Optional[datetime] = None
_last_cycle_new_items: int = 0
_last_cycle_blocked_items: int = 0


def _load_scheduler_config() -> None:
    """Restore persisted interval_minutes and constant_scrape state from disk."""
    global _current_interval_minutes, _constant_scrape_active
    if os.path.exists(SCHEDULER_CONFIG_PATH) and os.path.getsize(SCHEDULER_CONFIG_PATH) > 0:
        try:
            with open(SCHEDULER_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    if "interval_minutes" in data and data["interval_minutes"]:
                        _current_interval_minutes = max(1, int(data["interval_minutes"]))
                    if "constant_scrape" in data:
                        _constant_scrape_active = bool(data["constant_scrape"])
        except Exception as exc:
            logger.warning("Could not load scheduler_config.json: %s", exc)


def _save_scheduler_config() -> None:
    """Persist current interval_minutes and constant_scrape state to disk."""
    try:
        os.makedirs(os.path.dirname(SCHEDULER_CONFIG_PATH), exist_ok=True)
        with open(SCHEDULER_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "interval_minutes": _current_interval_minutes,
                    "constant_scrape": _constant_scrape_active,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                f,
                indent=2,
            )
    except Exception as exc:
        logger.warning("Could not save scheduler_config.json: %s", exc)


def _ensure_constant_scrape_thread_running() -> None:
    """Ensure ConstantScrapeThread is alive whenever _constant_scrape_active is True."""
    global _constant_scrape_thread
    with _scheduler_lock:
        if not _constant_scrape_active:
            return
        if _constant_scrape_thread is not None and _constant_scrape_thread.is_alive():
            return
        _constant_scrape_stop.clear()
        _constant_scrape_thread = threading.Thread(
            target=_constant_scrape_loop,
            daemon=True,
            name="ConstantScrapeThread",
        )
        _constant_scrape_thread.start()
        logger.info("🔄 Constant Continuous Scrape worker thread started.")


def _run_scrape_cycle(
    search_ids: Optional[List[int]] = None,
    is_constant_loop: bool = False,
) -> dict:
    """Run one scrape cycle across active or specified tracked searches."""
    global _is_cycle_running, _current_search_text, _current_search_index, _total_searches_in_cycle
    global _last_cycle_time, _last_cycle_new_items, _last_cycle_blocked_items
    global _constant_scrape_phase, _constant_scrape_cycles_count, _constant_scrape_total_new

    # Prevent overlapping cycles from colliding on SQLite
    if not _cycle_lock.acquire(blocking=not is_constant_loop):
        return {
            "total_searches": 0,
            "total_new_items": 0,
            "total_spam_blocked": 0,
            "total_rep_blocked": 0,
            "results": [],
            "skipped_already_running": True,
        }

    _is_cycle_running = True
    if is_constant_loop:
        _constant_scrape_phase = "scraping"
    logger.info("⏳ Starting %sscrape cycle…", "constant " if is_constant_loop else "")

    db = SessionLocal()
    total_new = 0
    total_spam_blocked = 0
    total_rep_blocked = 0
    results: List[Dict[str, Any]] = []

    try:
        query = db.query(TrackedSearch).filter(TrackedSearch.is_active.is_(True)).order_by(TrackedSearch.id)
        if search_ids:
            query = query.filter(TrackedSearch.id.in_(search_ids))
        searches = query.all()
        _total_searches_in_cycle = len(searches)

        for idx, search in enumerate(searches, start=1):
            if is_constant_loop and (_constant_scrape_stop.is_set() or not _constant_scrape_active):
                logger.info("Constant scrape interrupted by stop signal.")
                break

            _current_search_index = idx
            _current_search_text = search.search_text

            try:
                details = scrape_and_store(db, search, return_details=True)
                new_cnt = details.get("new_count", 0) if isinstance(details, dict) else details
                spam_cnt = details.get("filtered_spam_count", 0) if isinstance(details, dict) else 0
                rep_cnt = details.get("filtered_antirep_count", 0) if isinstance(details, dict) else 0

                total_new += new_cnt
                total_spam_blocked += spam_cnt
                total_rep_blocked += rep_cnt

                results.append(
                    {
                        "search_id": search.id,
                        "search_text": search.search_text,
                        "new_items": new_cnt,
                        "spam_blocked": spam_cnt,
                        "rep_blocked": rep_cnt,
                    }
                )
            except Exception as exc:
                logger.exception("Error scraping '%s': %s", search.search_text, exc)
                results.append(
                    {
                        "search_id": search.id,
                        "search_text": search.search_text,
                        "error": str(exc),
                    }
                )

            # Polite 2s delay between searches (interruptible in constant scrape mode)
            if idx < len(searches):
                if is_constant_loop:
                    if _constant_scrape_stop.wait(2.0):
                        break
                else:
                    time.sleep(2.0)

        # Audit a batch of active items to detect newly sold items and price drops
        if not (is_constant_loop and (_constant_scrape_stop.is_set() or not _constant_scrape_active)):
            if is_constant_loop:
                _constant_scrape_phase = "auditing"
            _current_search_text = "Auditing sold statuses & price drops"
            try:
                from .services.status_checker import batch_audit_and_sync_statuses
                audit_batch_size = 30 if is_constant_loop else 80
                status_res = batch_audit_and_sync_statuses(
                    db=db,
                    batch_size=audit_batch_size,
                    search_id=search_ids[0] if (search_ids and len(search_ids) == 1) else None,
                    only_unverified_or_active=True,
                    max_workers=6,
                )
                logger.info(
                    "🔍 Background status sync: %d sold, %d active, %d prices updated.",
                    status_res.get("marked_sold", 0),
                    status_res.get("still_active", 0),
                    status_res.get("price_updated_count", 0),
                )
                from .services.scraper_logger import scraper_logger
                scraper_logger.log_status_audit(
                    sold_count=status_res.get("marked_sold", 0),
                    active_count=status_res.get("still_active", 0),
                    price_updates=status_res.get("price_updated_count", 0),
                )
            except Exception as status_exc:
                logger.warning("Background status sync skipped: %s", status_exc)

        _last_cycle_time = datetime.now(timezone.utc)
        _last_cycle_new_items = total_new
        _last_cycle_blocked_items = total_spam_blocked + total_rep_blocked

        if is_constant_loop:
            _constant_scrape_cycles_count += 1
            _constant_scrape_total_new += total_new

        from .services.scraper_logger import scraper_logger
        scraper_logger.log_cycle_complete(
            total_searches=len(searches),
            total_new=total_new,
            total_blocked=total_spam_blocked + total_rep_blocked,
            is_constant=is_constant_loop,
        )

        logger.info(
            "✅ Scrape cycle complete — %d new items stored (%d spam, %d replica blocked).",
            total_new,
            total_spam_blocked,
            total_rep_blocked,
        )

    except Exception:
        logger.exception("Scrape cycle failed")
    finally:
        _is_cycle_running = False
        _current_search_text = None
        _current_search_index = 0
        _total_searches_in_cycle = 0
        db.close()
        _cycle_lock.release()

    return {
        "total_searches": len(results),
        "total_new_items": total_new,
        "total_spam_blocked": total_spam_blocked,
        "total_rep_blocked": total_rep_blocked,
        "results": results,
    }


def _constant_scrape_loop() -> None:
    """Worker loop for Constant Scrape mode — cycles continuously with polite pauses."""
    global _constant_scrape_phase, _constant_scrape_next_cycle_at
    logger.info("Constant Scrape 24/7 background loop active.")
    cooldown_seconds = 12.0

    while not _constant_scrape_stop.is_set() and _constant_scrape_active:
        try:
            _constant_scrape_next_cycle_at = None
            _run_scrape_cycle(is_constant_loop=True)
        except Exception as exc:
            logger.debug("Error in constant scrape cycle: %s", exc)

        if _constant_scrape_stop.is_set() or not _constant_scrape_active:
            break

        # Brief polite pause between full cycles before repeating
        _constant_scrape_phase = "cooldown"
        _constant_scrape_next_cycle_at = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)
        _constant_scrape_stop.wait(cooldown_seconds)

    _constant_scrape_phase = "idle"
    _constant_scrape_next_cycle_at = None
    logger.info("Constant Scrape background loop stopped.")


def start_scheduler() -> None:
    """Start the background polling scheduler and restore persisted settings."""
    _load_scheduler_config()
    with _scheduler_lock:
        if not scheduler.running:
            scheduler.add_job(
                _run_scrape_cycle,
                trigger="interval",
                minutes=_current_interval_minutes,
                id="vinted_scraper",
                replace_existing=True,
            )
            scheduler.start()
            logger.info(
                "🕐 Scheduler started — polling every %d min.", _current_interval_minutes
            )
        if _constant_scrape_active:
            _ensure_constant_scrape_thread_running()


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler and constant scrape worker."""
    with _scheduler_lock:
        _constant_scrape_stop.set()
        if scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped.")


def update_scheduler_settings(
    interval_minutes: Optional[int] = None,
    constant_scrape: Optional[bool] = None,
) -> dict:
    """Dynamically update polling regularity or toggle constant scrape."""
    global _current_interval_minutes, _constant_scrape_active, _constant_scrape_phase, _constant_scrape_next_cycle_at

    with _scheduler_lock:
        if interval_minutes is not None:
            _current_interval_minutes = max(1, int(interval_minutes))
            if scheduler.running:
                next_run = datetime.now(timezone.utc) + timedelta(minutes=_current_interval_minutes)
                scheduler.add_job(
                    _run_scrape_cycle,
                    trigger="interval",
                    minutes=_current_interval_minutes,
                    id="vinted_scraper",
                    next_run_time=next_run,
                    replace_existing=True,
                )
                logger.info("Rescheduled auto-poll to every %d min (next at %s).", _current_interval_minutes, next_run.isoformat())

        if constant_scrape is not None:
            want_active = bool(constant_scrape)
            if want_active:
                _constant_scrape_active = True
                _ensure_constant_scrape_thread_running()
                logger.info("Constant scrape mode enabled.")
            else:
                _constant_scrape_active = False
                _constant_scrape_stop.set()
                _constant_scrape_phase = "idle"
                _constant_scrape_next_cycle_at = None
                logger.info("Constant scrape mode disabled.")

        _save_scheduler_config()

    return get_scheduler_status()


def get_scheduler_status() -> dict:
    """Return scheduler configuration, live countdown, and constant scrape telemetry."""
    if _constant_scrape_active:
        _ensure_constant_scrape_thread_running()

    now_utc = datetime.now(timezone.utc)
    next_run_iso: Optional[str] = None
    seconds_until_next_run: Optional[int] = None

    if scheduler.running:
        job = scheduler.get_job("vinted_scraper")
        if job and job.next_run_time:
            nrt = job.next_run_time
            if nrt.tzinfo is None:
                nrt = nrt.replace(tzinfo=timezone.utc)
            else:
                nrt = nrt.astimezone(timezone.utc)
            next_run_iso = nrt.isoformat()
            seconds_until_next_run = max(0, int((nrt - now_utc).total_seconds()))

    constant_cooldown_secs: Optional[int] = None
    if _constant_scrape_active and _constant_scrape_next_cycle_at:
        constant_cooldown_secs = max(0, int((_constant_scrape_next_cycle_at - now_utc).total_seconds()))

    return {
        "interval_minutes": _current_interval_minutes,
        "constant_scrape": _constant_scrape_active,
        "constant_scrape_thread_alive": bool(_constant_scrape_thread and _constant_scrape_thread.is_alive()),
        "constant_scrape_phase": _constant_scrape_phase if _constant_scrape_active else "idle",
        "constant_scrape_cycles": _constant_scrape_cycles_count,
        "constant_scrape_total_new": _constant_scrape_total_new,
        "constant_cooldown_seconds": constant_cooldown_secs,
        "is_scheduler_running": scheduler.running,
        "is_cycle_running": _is_cycle_running,
        "current_search_text": _current_search_text,
        "current_search_index": _current_search_index,
        "total_searches_in_cycle": _total_searches_in_cycle,
        "last_cycle_time": _last_cycle_time.isoformat() if _last_cycle_time else None,
        "last_cycle_new_items": _last_cycle_new_items,
        "last_cycle_blocked_items": _last_cycle_blocked_items,
        "next_run_time": next_run_iso,
        "seconds_until_next_run": seconds_until_next_run,
    }


def batch_scrape_searches(search_ids: Optional[List[int]] = None) -> dict:
    """Trigger an immediate scrape across selected or all active searches."""
    res = _run_scrape_cycle(search_ids=search_ids, is_constant_loop=False)
    # Also reset the auto-poll timer from now when a full manual scrape runs
    if not search_ids and scheduler.running:
        with _scheduler_lock:
            next_run = datetime.now(timezone.utc) + timedelta(minutes=_current_interval_minutes)
            scheduler.add_job(
                _run_scrape_cycle,
                trigger="interval",
                minutes=_current_interval_minutes,
                id="vinted_scraper",
                next_run_time=next_run,
                replace_existing=True,
            )
    return res
