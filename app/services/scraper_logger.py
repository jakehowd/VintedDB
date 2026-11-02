"""Thread-safe live activity logger for Vinted scraper and sniper operations.

Captures clean, structured activity events with prominent highlighting for items
newly saved to the database. Excludes internal debug clutter and raw tracebacks.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class ScraperActivityLogger:
    """Thread-safe ring-buffer activity logger for scraper and sniper feeds."""

    def __init__(self, maxlen: int = 300) -> None:
        self._maxlen = maxlen
        self._events: deque[Dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._counter: int = 0

        # Session metrics
        self.total_added_to_db: int = 0
        self.total_inspected: int = 0
        self.total_price_updates: int = 0
        self.total_scans_completed: int = 0
        self.last_activity_at: Optional[datetime] = None

    def _resolve_user_id(self, user_id: Optional[int] = None) -> Optional[int]:
        if user_id is not None:
            return user_id
        from ..auth import get_active_user_id
        return get_active_user_id()

    def _format_time(self, dt: datetime) -> str:
        return dt.strftime("%H:%M:%S")

    def log_item_added(
        self,
        item_data: Dict[str, Any],
        search_text: str,
        source: str = "scraper",
        is_deal: bool = False,
        discount_pct: float = 0.0,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Record an item newly scraped and saved to the database (High Priority Highlight)."""
        uid = self._resolve_user_id(user_id)
        now = datetime.now(timezone.utc)
        title = str(item_data.get("title") or "Unknown Item").strip()
        price = float(item_data.get("price") or 0.0)
        currency = str(item_data.get("currency") or "GBP")
        vinted_id = str(item_data.get("id") or item_data.get("vinted_item_id") or "")

        url = item_data.get("url") or ""
        if url and not url.startswith("http"):
            url = f"https://www.vinted.co.uk/{url.lstrip('/')}"
        elif not url and vinted_id:
            url = f"https://www.vinted.co.uk/items/{vinted_id}"

        clean_event = {
            "id": 0,  # assigned under lock
            "user_id": uid,
            "type": "db_added",
            "time": self._format_time(now),
            "iso_time": now.isoformat(),
            "source": source,
            "search_text": search_text,
            "vinted_id": vinted_id,
            "title": title,
            "price": price,
            "currency": currency,
            "brand": item_data.get("brand") or "",
            "size": item_data.get("size") or "",
            "condition": item_data.get("condition") or "",
            "seller_username": item_data.get("seller_username") or "",
            "photo_url": item_data.get("photo_url"),
            "url": url,
            "is_deal": is_deal,
            "discount_pct": discount_pct,
            "message": f"Added to DB: '{title}' • £{price:.2f}",
        }

        with self._lock:
            self._counter += 1
            clean_event["id"] = self._counter
            self._events.append(clean_event)
            self.total_added_to_db += 1
            self.last_activity_at = now

        return clean_event

    def log_price_updated(
        self,
        title: str,
        old_price: float,
        new_price: float,
        search_text: str = "",
        item_id: str = "",
        url: str = "",
    ) -> Dict[str, Any]:
        """Record an existing item whose price changed on Vinted."""
        now = datetime.now(timezone.utc)
        drop = round(old_price - new_price, 2)
        diff_str = f"(-£{drop:.2f})" if drop > 0 else f"(+£{-drop:.2f})"

        event = {
            "id": 0,
            "user_id": self._resolve_user_id(),
            "type": "price_updated",
            "time": self._format_time(now),
            "iso_time": now.isoformat(),
            "search_text": search_text,
            "vinted_id": str(item_id or ""),
            "title": title,
            "old_price": round(old_price, 2),
            "new_price": round(new_price, 2),
            "url": url,
            "message": f"Price adjusted: '{title}' £{old_price:.2f} ➔ £{new_price:.2f} {diff_str}",
        }

        with self._lock:
            self._counter += 1
            event["id"] = self._counter
            self._events.append(event)
            self.total_price_updates += 1
            self.last_activity_at = now

        return event

    def log_scan_start(self, search_text: str, source: str = "scraper") -> Dict[str, Any]:
        """Record the start of a scrape query."""
        now = datetime.now(timezone.utc)
        event = {
            "id": 0,
            "user_id": self._resolve_user_id(),
            "type": "scan_start",
            "time": self._format_time(now),
            "iso_time": now.isoformat(),
            "source": source,
            "search_text": search_text,
            "message": f"🔍 Scanning '{search_text}' on Vinted UK...",
        }
        with self._lock:
            self._counter += 1
            event["id"] = self._counter
            self._events.append(event)
            self.last_activity_at = now
        return event

    def log_scan_summary(
        self,
        search_text: str,
        total_found: int,
        new_count: int,
        spam_blocked: int,
        rep_blocked: int,
        source: str = "scraper",
    ) -> Dict[str, Any]:
        """Record completion summary for a single tracked search."""
        now = datetime.now(timezone.utc)
        filtered = spam_blocked + rep_blocked
        event = {
            "id": 0,
            "user_id": self._resolve_user_id(),
            "type": "scan_summary",
            "time": self._format_time(now),
            "iso_time": now.isoformat(),
            "source": source,
            "search_text": search_text,
            "total_found": total_found,
            "new_count": new_count,
            "spam_blocked": spam_blocked,
            "rep_blocked": rep_blocked,
            "message": f"Finished '{search_text}': {total_found} examined • {new_count} added to DB ({filtered} filtered)",
        }
        with self._lock:
            self._counter += 1
            event["id"] = self._counter
            self._events.append(event)
            self.total_inspected += total_found
            self.total_scans_completed += 1
            self.last_activity_at = now
        return event

    def log_cycle_complete(
        self,
        total_searches: int,
        total_new: int,
        total_blocked: int,
        is_constant: bool = False,
    ) -> Dict[str, Any]:
        """Record completion of an entire sweep across all tracked searches."""
        now = datetime.now(timezone.utc)
        mode_label = "Continuous 24/7 loop" if is_constant else "Auto-poll cycle"
        event = {
            "id": 0,
            "user_id": self._resolve_user_id(),
            "type": "cycle_complete",
            "time": self._format_time(now),
            "iso_time": now.isoformat(),
            "total_searches": total_searches,
            "total_new": total_new,
            "total_blocked": total_blocked,
            "message": f"🏁 {mode_label} complete: {total_searches} searches scanned • {total_new} new items saved to DB",
        }
        with self._lock:
            self._counter += 1
            event["id"] = self._counter
            self._events.append(event)
            self.last_activity_at = now
        return event

    def log_status_audit(
        self,
        sold_count: int,
        active_count: int,
        price_updates: int,
    ) -> Dict[str, Any]:
        """Record completion of sold status & price drop verification audit."""
        now = datetime.now(timezone.utc)
        event = {
            "id": 0,
            "user_id": self._resolve_user_id(),
            "type": "status_audit",
            "time": self._format_time(now),
            "iso_time": now.isoformat(),
            "sold_count": sold_count,
            "active_count": active_count,
            "price_updates": price_updates,
            "message": f"Sold audit: {sold_count} verified sold, {active_count} active, {price_updates} prices synced",
        }
        with self._lock:
            self._counter += 1
            event["id"] = self._counter
            self._events.append(event)
            self.last_activity_at = now
        return event

    def get_feed(
        self,
        since_id: Optional[int] = None,
        filter_type: Optional[str] = None,
        limit: int = 150,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Retrieve recent events scoped to user_id, optionally filtered by ID and type."""
        uid = self._resolve_user_id(user_id)
        with self._lock:
            all_events = [
                e for e in self._events
                if uid is None or e.get("user_id") == uid
            ]
            stats = {
                "total_added_to_db": sum(1 for e in all_events if e.get("type") == "db_added"),
                "total_inspected": sum(int(e.get("total_found", 0) or 0) for e in all_events if e.get("type") == "scan_summary"),
                "total_price_updates": sum(1 for e in all_events if e.get("type") == "price_updated"),
                "total_scans_completed": sum(1 for e in all_events if e.get("type") == "scan_summary"),
                "last_activity_at": all_events[-1].get("iso_time") if all_events else None,
            }

        # Apply since_id filter
        if since_id is not None and since_id > 0:
            events = [e for e in all_events if e["id"] > since_id]
        else:
            events = all_events[-limit:] if len(all_events) > limit else all_events

        # Apply event type filter if requested
        if filter_type and filter_type != "all":
            if filter_type == "db_added":
                events = [e for e in events if e.get("type") == "db_added"]
            elif filter_type == "scans":
                events = [e for e in events if e.get("type") in ("scan_start", "scan_summary", "cycle_complete")]

        return {
            "events": events,
            "stats": stats,
            "latest_event_id": self._counter,
        }

    def clear(self, user_id: Optional[int] = None) -> None:
        """Clear the in-memory events buffer for the given user (or all if None)."""
        uid = self._resolve_user_id(user_id)
        with self._lock:
            if uid is None:
                self._events.clear()
                self.total_added_to_db = 0
                self.total_price_updates = 0
                self.total_inspected = 0
                self.total_scans_completed = 0
                self.last_activity_at = None
            else:
                kept = [e for e in self._events if e.get("user_id") != uid]
                self._events.clear()
                self._events.extend(kept)


# Global singleton instance
scraper_logger = ScraperActivityLogger(maxlen=300)

