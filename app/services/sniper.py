"""Deal Sniper Service.

High-frequency, real-time polling engine for instant deal detection.
Constantly monitors targeted Vinted searches for newly listed underpriced steals.
Ensures all items passing tracked search filters are stored in the database
(preventing analytic skew), while deals meeting sniper constraints are surfaced
immediately in the live deal stream.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import VINTED_BASE_URL
from ..database import SessionLocal
from ..models import ProductVariant, SoldItem, TrackedSearch
from ..scraper import _extract_item, _get_vinted_scraper, _search_vinted
from ..services.antirep import (
    evaluate_antirep_rules,
    get_item_description,
    get_item_uploaded_at,
)
from ..services.item_classifier import is_child_size, is_item_relevant, normalize_categories
from ..services.resale_evaluator import resale_evaluator
from ..services.variant_vision import variant_vision_service

logger = logging.getLogger(__name__)


class DealSniperService:
    """Manages active live sniping across selected tracked searches isolated per user profile."""

    def __init__(self, user_id: Optional[int] = None):
        self.user_id: Optional[int] = user_id
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        self.is_running = False
        self.selected_search_ids: List[int] = []
        self.max_price: float = 50.0
        self.item_max_prices: Dict[int, float] = {}
        self.max_age_minutes: int = 15
        self.poll_delay_seconds: float = 6.0
        self.started_at: Optional[datetime] = None
        self.last_poll_at: Optional[datetime] = None
        self.total_polls_count: int = 0
        self.total_items_inspected: int = 0
        self.total_items_saved: int = 0
        self.total_snipes_found: int = 0
        self.ai_resale_gate_enabled: bool = True
        self.min_net_profit: float = 12.0
        self.min_roi_pct: float = 30.0

        # Monotonic Vinted ID cutoff: any item with int(id) <= _oldest_expired_vinted_id
        # is guaranteed to have been uploaded before that item (> max_age_minutes ago)
        self._oldest_expired_vinted_id: int = 0
        self._oldest_expired_age_mins: int = 60

        # In-memory storage for live deals & logs
        self.sniped_deals: List[Dict[str, Any]] = []
        self.activity_logs: List[Dict[str, Any]] = []
        self._seen_deal_ids: set[str] = set()
        self._cleared_deal_ids: set[str] = set()
        self._load_cleared_deals()

    def _load_cleared_deals(self) -> None:
        """Load previously cleared deal IDs strictly for self.user_id."""
        if self.user_id is not None:
            try:
                from ..models import User
                db = SessionLocal()
                try:
                    user = db.query(User).filter(User.id == self.user_id).first()
                    if user and user.sniper_cleared_deals_json:
                        ids = json.loads(user.sniper_cleared_deals_json)
                        if isinstance(ids, list):
                            self._cleared_deal_ids = {str(i) for i in ids}
                            self._seen_deal_ids.update(self._cleared_deal_ids)
                finally:
                    db.close()
            except Exception as e:
                logger.warning("Could not load user %s cleared deals from DB: %s", self.user_id, e)

    def _save_cleared_deals(self) -> None:
        """Persist cleared deal IDs strictly for self.user_id."""
        if self.user_id is not None:
            try:
                from ..models import User
                db = SessionLocal()
                try:
                    user = db.query(User).filter(User.id == self.user_id).first()
                    if user:
                        user.sniper_cleared_deals_json = json.dumps(list(self._cleared_deal_ids))
                        db.commit()
                finally:
                    db.close()
            except Exception as e:
                logger.warning("Could not save user %s cleared deals: %s", self.user_id, e)

    def start(
        self,
        search_ids: List[int],
        max_price: float = 50.0,
        max_age_minutes: int = 15,
        poll_delay_seconds: float = 6.0,
        item_max_prices: Optional[Dict[int, float]] = None,
        ai_resale_gate_enabled: Optional[bool] = None,
        min_net_profit: Optional[float] = None,
        min_roi_pct: Optional[float] = None,
    ) -> dict:
        """Start the background sniper loop with specified parameters."""
        with self._lock:
            if item_max_prices:
                self.item_max_prices = {int(k): float(v) for k, v in item_max_prices.items()}
            else:
                self.item_max_prices = {}

            if ai_resale_gate_enabled is not None:
                self.ai_resale_gate_enabled = bool(ai_resale_gate_enabled)
            if min_net_profit is not None:
                self.min_net_profit = float(min_net_profit)
            if min_roi_pct is not None:
                self.min_roi_pct = float(min_roi_pct)

            new_max_age = int(max_age_minutes)
            if new_max_age != self.max_age_minutes:
                self._oldest_expired_vinted_id = 0

            if self.is_running and self._worker_thread and self._worker_thread.is_alive():
                # Update parameters on the fly
                self.selected_search_ids = list(search_ids)
                self.max_price = float(max_price)
                self.max_age_minutes = new_max_age
                self.poll_delay_seconds = max(2.0, float(poll_delay_seconds))
                custom_count = len(self.item_max_prices)
                self._add_log(
                    "SYSTEM", 
                    f"Sniper updated: {len(search_ids)} targets ({custom_count} custom prices), Default Max £{max_price:.2f}, Age <={max_age_minutes}m, AI Gate {'ON' if self.ai_resale_gate_enabled else 'OFF'} (Min £{self.min_net_profit:.2f})"
                )
                self.revalidate_deals()
                return self.get_status()

            self.selected_search_ids = list(search_ids)
            self.max_price = float(max_price)
            self.max_age_minutes = new_max_age
            self.poll_delay_seconds = max(2.0, float(poll_delay_seconds))
            self.is_running = True
            self.started_at = datetime.now(timezone.utc)
            self._stop_event.clear()

            self.revalidate_deals()

            custom_count = len(self.item_max_prices)
            self._add_log(
                "SYSTEM",
                f"🚀 Deal Sniper launched on {len(search_ids)} products ({custom_count} custom price rules, default £{max_price:.2f}, Age <={max_age_minutes}m, AI Gate {'ON' if self.ai_resale_gate_enabled else 'OFF'} Min £{self.min_net_profit:.2f})",
            )

            self._worker_thread = threading.Thread(
                target=self._run_sniper_loop,
                daemon=True,
                name="DealSniperThread",
            )
            self._worker_thread.start()

        return self.get_status()

    def revalidate_deals(self, search_id: Optional[int] = None) -> int:
        """Audit all currently sniped deals against max_age_minutes, price limits, and anti-rep / seller review filters."""
        from ..auth import set_active_user_id
        set_active_user_id(self.user_id)

        with self._lock:
            if not self.sniped_deals:
                return 0
            deals_snapshot = list(self.sniped_deals)

        db: Session = SessionLocal()
        removed_ids: set[str] = set()
        now_utc = datetime.now(timezone.utc)
        try:
            search_query = db.query(TrackedSearch)
            if self.user_id is not None:
                search_query = search_query.filter(TrackedSearch.user_id == self.user_id)
            searches_map: Dict[int, TrackedSearch] = {
                s.id: s for s in search_query.all()
            }
            variants_by_search: Dict[int, List[ProductVariant]] = {}
            if searches_map:
                for pv in db.query(ProductVariant).filter(ProductVariant.tracked_search_id.in_(list(searches_map.keys()))).order_by(ProductVariant.id).all():
                    variants_by_search.setdefault(pv.tracked_search_id, []).append(pv)

            scraper = _get_vinted_scraper()
            db_dirty = False

            for deal in deals_snapshot:
                s_id = deal.get("search_id")
                if search_id is not None and s_id != search_id:
                    continue

                vinted_id = str(deal.get("id") or "")
                search = searches_map.get(s_id)
                if not search:
                    removed_ids.add(vinted_id)
                    continue

                # 1. Verify real Vinted upload age against self.max_age_minutes
                real_uploaded_at = get_item_uploaded_at(vinted_id)
                if real_uploaded_at:
                    if real_uploaded_at.tzinfo is None:
                        real_uploaded_at = real_uploaded_at.replace(tzinfo=timezone.utc)
                    real_age_mins = max(0, int((now_utc - real_uploaded_at).total_seconds() // 60))
                    deal["uploaded_at"] = real_uploaded_at.isoformat()
                    deal["age_minutes"] = real_age_mins
                    if real_age_mins > self.max_age_minutes:
                        removed_ids.add(vinted_id)
                        continue
                else:
                    # Fallback to deal's stored age if live check unavailable
                    if int(deal.get("age_minutes") or 0) > self.max_age_minutes:
                        removed_ids.add(vinted_id)
                        continue

                db_item = (
                    db.query(SoldItem)
                    .filter(
                        SoldItem.tracked_search_id == search.id,
                        SoldItem.vinted_item_id == vinted_id,
                    )
                    .first()
                )

                if db_item and real_uploaded_at:
                    db_item.captured_at = real_uploaded_at
                    db_dirty = True

                item_title = deal.get("title") or (db_item.title if db_item else "")
                item_brand = deal.get("brand") or (db_item.brand if db_item else "")
                item_size = deal.get("size") or (db_item.size if db_item else "")
                item_condition = deal.get("condition") or (db_item.condition if db_item else "")
                seller_id = deal.get("seller_id") or (db_item.seller_id if db_item else "")
                seller_username = deal.get("seller_username") or (db_item.seller_username if db_item else "")

                if getattr(search, "exclude_child_sizes", False):
                    is_child, _ = is_child_size(item_title, item_size)
                    if is_child or (db_item and getattr(db_item, "is_child_size", False)):
                        removed_ids.add(vinted_id)
                        continue

                if db_item and db_item.is_replica:
                    removed_ids.add(vinted_id)
                    continue

                # Multi-category variant selection & max price revalidation
                s_variants = variants_by_search.get(search.id, []) if getattr(search, "variants_enabled", False) else []
                eff_max_price = self.item_max_prices.get(search.id, self.max_price)
                if s_variants:
                    from ..routers.variants import evaluate_sniper_variant_match

                    d_vids = deal.get("variant_ids") or (db_item.parsed_variant_ids() if db_item else [])
                    if not d_vids and deal.get("variant_id"):
                        d_vids = [int(deal["variant_id"])]

                    if not d_vids:
                        v_res = variant_vision_service.classify_item_variant(
                            db=db,
                            tracked_search=search,
                            item_data={
                                "title": item_title,
                                "photo_url": deal.get("photo_url") or (db_item.photo_url if db_item else None),
                            },
                            variants=s_variants,
                            allow_vision=True,
                        )
                        if v_res:
                            d_vids = list(v_res.get("variant_ids") or ([v_res["variant_id"]] if v_res.get("variant_id") else []))
                            deal["variant_id"] = v_res["variant_id"]
                            deal["variant_ids"] = d_vids
                            deal["variant_name"] = v_res["variant_name"]
                            deal["variant_names"] = v_res.get("variant_names", [])
                            deal["variant_confidence"] = v_res["confidence"]
                            deal["variant_method"] = v_res["method"]
                            deal["variant_evidence"] = v_res["evidence"]
                            if db_item and db_item.variant_method != "manual":
                                db_item.apply_variant_result(v_res)
                                db_dirty = True

                    allowed_var, var_price_cap = evaluate_sniper_variant_match(d_vids, s_variants)
                    if not allowed_var:
                        removed_ids.add(vinted_id)
                        continue
                    if var_price_cap is not None and var_price_cap > 0:
                        eff_max_price = var_price_cap

                if float(deal.get("price") or 0.0) > eff_max_price:
                    removed_ids.add(vinted_id)
                    continue

                item_data = {
                    "id": vinted_id,
                    "title": item_title,
                    "brand": item_brand,
                    "size": item_size,
                    "condition": item_condition,
                    "seller_id": seller_id,
                    "seller_username": seller_username,
                    "photo_url": deal.get("photo_url") or (db_item.photo_url if db_item else None),
                    "url": deal.get("url") or (db_item.url if db_item else None),
                    "raw_item": None,
                }

                clean, rep_reason = evaluate_antirep_rules(
                    db=db,
                    tracked_search_id=search.id,
                    item_data=item_data,
                    antirep_config=search.antirep_config,
                    scraper=scraper,
                    deep_inspection=False,
                )
                if not clean:
                    removed_ids.add(vinted_id)
                    if db_item:
                        db_item.is_replica = True
                        db_item.replica_reason = rep_reason
                        if item_data.get("seller_id") and not db_item.seller_id:
                            db_item.seller_id = str(item_data["seller_id"])
                        db_dirty = True
                    continue

                # Refresh AI Resale Evaluation
                resale_eval = resale_evaluator.evaluate_deal(db=db, tracked_search=search, deal=deal)
                deal.update({
                    "ai_score": resale_eval["ai_score"],
                    "ai_verdict": resale_eval["verdict"],
                    "is_recommended": resale_eval["is_recommended"],
                    "projected_net_profit": resale_eval["projected_net_profit"],
                    "projected_roi_pct": resale_eval["roi_pct"],
                    "projected_resale_price": resale_eval["projected_resale_price"],
                    "total_landed_cost": resale_eval["total_landed_cost"],
                    "cost_breakdown": resale_eval["cost_breakdown"],
                    "liquidity_rating": resale_eval["liquidity_rating"],
                    "estimated_days_to_sell": resale_eval["estimated_days_to_sell"],
                    "seller_risk_level": resale_eval["seller_risk_level"],
                    "ai_reasoning": resale_eval["reasoning"],
                })
                if self.ai_resale_gate_enabled:
                    if (
                        resale_eval["projected_net_profit"] < self.min_net_profit
                        or resale_eval["roi_pct"] < self.min_roi_pct
                        or resale_eval["verdict"] == "PASS"
                    ):
                        removed_ids.add(vinted_id)

            if db_dirty:
                db.commit()
        except Exception as exc:
            db.rollback()
            logger.debug("Error revalidating sniper deals: %s", exc)
        finally:
            db.close()

        if removed_ids:
            with self._lock:
                self.sniped_deals = [
                    d for d in self.sniped_deals if str(d.get("id")) not in removed_ids
                ]
                self.total_snipes_found = max(0, len(self.sniped_deals))
                self._add_log(
                    "SHIELD",
                    f"Filtered {len(removed_ids)} sniped deal(s) exceeding age limit (<={self.max_age_minutes}m) or failing anti-rep rules.",
                )

        return len(removed_ids)

    def stop(self) -> dict:
        """Stop the background sniper loop."""
        with self._lock:
            if not self.is_running:
                return self.get_status()
            self.is_running = False
            self._stop_event.set()
            self._add_log("SYSTEM", "🛑 Deal Sniper stopped.")

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)

        return self.get_status()

    def clear_deals(self) -> None:
        """Clear the sniped deals buffer and permanently record them so they are not re-sniped."""
        with self._lock:
            for deal in self.sniped_deals:
                deal_id = str(deal.get("id"))
                self._cleared_deal_ids.add(deal_id)
                self._seen_deal_ids.add(deal_id)

            self._cleared_deal_ids.update(self._seen_deal_ids)
            self.sniped_deals.clear()
            self.total_snipes_found = 0
            self._save_cleared_deals()
            self._add_log("SYSTEM", "Cleared sniped deals stream.")

    def dismiss_deal(self, deal_id: str) -> bool:
        """Dismiss an individual deal by ID and ensure it is never re-sniped."""
        str_id = str(deal_id)
        with self._lock:
            self._cleared_deal_ids.add(str_id)
            self._seen_deal_ids.add(str_id)
            initial_count = len(self.sniped_deals)
            self.sniped_deals = [d for d in self.sniped_deals if str(d.get("id")) != str_id]
            removed = len(self.sniped_deals) < initial_count
            if self.total_snipes_found > len(self.sniped_deals):
                self.total_snipes_found = len(self.sniped_deals)
            self._save_cleared_deals()
            if removed:
                self._add_log("SYSTEM", f"Dismissed deal #{str_id}.")
            return removed

    def get_status(self) -> dict:
        """Return full live status dictionary scoped strictly to self.user_id."""
        now_utc = datetime.now(timezone.utc)
        total_db_items = 0
        try:
            db_stat = SessionLocal()
            try:
                q_stat = db_stat.query(func.count(SoldItem.id))
                if self.user_id is not None:
                    q_stat = q_stat.join(TrackedSearch, SoldItem.tracked_search_id == TrackedSearch.id).filter(
                        TrackedSearch.user_id == self.user_id
                    )
                total_db_items = q_stat.scalar() or 0
            finally:
                db_stat.close()
        except Exception:
            pass

        with self._lock:
            valid_deals: List[Dict[str, Any]] = []
            for d in self.sniped_deals:
                up_str = d.get("uploaded_at") or d.get("discovered_at")
                if up_str:
                    try:
                        up_dt = datetime.fromisoformat(up_str)
                        if up_dt.tzinfo is None:
                            up_dt = up_dt.replace(tzinfo=timezone.utc)
                        live_age = max(0, int((now_utc - up_dt).total_seconds() // 60))
                        # If deal didn't have uploaded_at yet, add initial age_minutes
                        if not d.get("uploaded_at"):
                            live_age = max(live_age, int(d.get("age_minutes") or 0))
                        d["age_minutes"] = live_age
                    except Exception:
                        pass
                if int(d.get("age_minutes") or 0) <= self.max_age_minutes:
                    valid_deals.append(d)
            if len(valid_deals) != len(self.sniped_deals):
                self.sniped_deals = valid_deals
                self.total_snipes_found = max(0, len(self.sniped_deals))

            from ..services.http_client import get_antiban_client
            return {
                "is_running": self.is_running,
                "selected_search_ids": list(self.selected_search_ids),
                "max_price": self.max_price,
                "item_max_prices": {int(k): float(v) for k, v in self.item_max_prices.items()},
                "max_age_minutes": self.max_age_minutes,
                "poll_delay_seconds": self.poll_delay_seconds,
                "ai_resale_gate_enabled": self.ai_resale_gate_enabled,
                "min_net_profit": self.min_net_profit,
                "min_roi_pct": self.min_roi_pct,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "last_poll_at": self.last_poll_at.isoformat() if self.last_poll_at else None,
                "total_polls_count": self.total_polls_count,
                "total_items_inspected": self.total_items_inspected,
                "total_items_saved": self.total_items_saved,
                "total_db_items": total_db_items,
                "total_snipes_found": self.total_snipes_found,
                "deals_count": len(self.sniped_deals),
                "deals": list(self.sniped_deals[:50]),  # Latest 50
                "logs": list(self.activity_logs[:40]),    # Latest 40
                "antiban": get_antiban_client(self.user_id).get_status(),
            }

    def _add_log(self, category: str, message: str) -> None:
        """Append an activity log entry."""
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": category,
            "message": message,
        }
        self.activity_logs.insert(0, log_entry)
        if len(self.activity_logs) > 100:
            self.activity_logs = self.activity_logs[:100]

    def _run_sniper_loop(self) -> None:
        """High-frequency continuous polling loop running in worker thread with per-user anti-ban defenses."""
        import random
        from ..auth import set_active_user_id
        from ..services.http_client import get_antiban_client, VintedBlockedException, VintedRateLimitException

        set_active_user_id(self.user_id)
        user_antiban = get_antiban_client(self.user_id)
        logger.info("Deal Sniper background worker started for user %s with Anti-Ban Shield active.", self.user_id)
        last_cooldown_log = 0

        while not self._stop_event.is_set():
            if not self.selected_search_ids:
                time.sleep(1.0)
                continue

            # 1. Circuit Breaker Check: if Cloudflare or rate-limit tripped, sleep through cooldown
            is_blocked, remaining_secs, reason = user_antiban.check_circuit_breaker()
            if is_blocked:
                now_t = time.time()
                if now_t - last_cooldown_log > 15:
                    self._add_log("SHIELD", f"🛡️ [Anti-Ban Cooldown] Pausing radar for {remaining_secs}s ({reason})")
                    last_cooldown_log = now_t
                self._stop_event.wait(min(3.0, float(remaining_secs)))
                continue

            # 2. Iterate through targets with staggered pacing
            for search_id in list(self.selected_search_ids):
                if self._stop_event.is_set():
                    break

                # Re-check circuit breaker before each target
                is_blocked, remaining_secs, _ = user_antiban.check_circuit_breaker()
                if is_blocked:
                    break

                try:
                    self._poll_single_search(search_id)
                except VintedBlockedException as exc:
                    self._add_log("SHIELD", f"⚠️ Cloudflare challenge detected! Circuit breaker entered {user_antiban.cooldown_remaining_seconds if hasattr(user_antiban, 'cooldown_remaining_seconds') else 60}s protection cooldown.")
                    break
                except VintedRateLimitException as exc:
                    self._add_log("SHIELD", f"⚠️ Vinted rate limit (429) hit. Pausing radar for {exc.retry_after}s.")
                    break
                except Exception as exc:
                    logger.debug("Error in sniper poll for search %s: %s", search_id, exc)

                # Staggered delay with human-like jitter between individual search targets
                jitter_factor = random.uniform(0.85, 1.35)
                target_delay = max(2.5, (self.poll_delay_seconds * 0.6) * jitter_factor)
                self._stop_event.wait(target_delay)

            # Polite pause between full radar cycles with randomized Gaussian jitter
            cycle_jitter = random.uniform(0.9, 1.3)
            base_delay = max(4.0, self.poll_delay_seconds * cycle_jitter)
            self._stop_event.wait(base_delay)

        logger.info("Deal Sniper background worker exited for user %s.", self.user_id)

    def _poll_single_search(self, search_id: int) -> None:
        """Perform a rapid page 1 scrape for search_id."""
        from ..auth import set_active_user_id
        set_active_user_id(self.user_id)

        db: Session = SessionLocal()
        try:
            search_q = db.query(TrackedSearch).filter(TrackedSearch.id == search_id)
            if self.user_id is not None:
                search_q = search_q.filter(TrackedSearch.user_id == self.user_id)
            search = search_q.first()
            if not search or not search.is_active:
                return

            self.last_poll_at = datetime.now(timezone.utc)
            self.total_polls_count += 1

            # Fetch newest items (page 1 only for ultra-low latency & efficiency)
            raw_items = _search_vinted(search.search_text, page=1, per_page=36)
            if not raw_items:
                return

            self.total_items_inspected += len(raw_items)
            target_categories = normalize_categories(search.category)
            antirep_config = search.antirep_config
            scraper = _get_vinted_scraper()

            # Precalculate market stats for discount calculation based on TRUE SOLD prices (excluding replicas)
            market_sold_avg = (
                db.query(func.avg(SoldItem.price))
                .filter(
                    SoldItem.tracked_search_id == search.id,
                    SoldItem.price > 0,
                    SoldItem.is_sold == True,
                    SoldItem.is_replica.isnot(True),
                )
                .scalar()
            )
            if market_sold_avg and market_sold_avg > 0:
                market_avg = float(market_sold_avg)
            else:
                market_avg = (
                    db.query(func.avg(SoldItem.price))
                    .filter(
                        SoldItem.tracked_search_id == search.id,
                        SoldItem.price > 0,
                        SoldItem.is_replica.isnot(True),
                    )
                    .scalar()
                ) or 0.0

            active_variants: List[ProductVariant] = []
            if getattr(search, "variants_enabled", False):
                active_variants = (
                    db.query(ProductVariant)
                    .filter(ProductVariant.tracked_search_id == search.id)
                    .order_by(ProductVariant.id)
                    .all()
                )
            variants_map: Dict[int, ProductVariant] = {v.id: v for v in active_variants}
            variant_market_avgs: Dict[int, float] = {}
            if active_variants:
                from ..routers.variants import item_has_variant_clause
                for v in active_variants:
                    has_v = item_has_variant_clause(v.id)
                    v_sold_avg = (
                        db.query(func.avg(SoldItem.price))
                        .filter(
                            SoldItem.tracked_search_id == search.id,
                            has_v,
                            SoldItem.price > 0,
                            SoldItem.is_sold == True,
                            SoldItem.is_replica.isnot(True),
                        )
                        .scalar()
                    )
                    if v_sold_avg and float(v_sold_avg) > 0:
                        variant_market_avgs[v.id] = float(v_sold_avg)
                    else:
                        v_all_avg = (
                            db.query(func.avg(SoldItem.price))
                            .filter(
                                SoldItem.tracked_search_id == search.id,
                                has_v,
                                SoldItem.price > 0,
                                SoldItem.is_replica.isnot(True),
                            )
                            .scalar()
                        )
                        if v_all_avg and float(v_all_avg) > 0:
                            variant_market_avgs[v.id] = float(v_all_avg)
                        elif v.est_market_value_gbp and float(v.est_market_value_gbp) > 0:
                            variant_market_avgs[v.id] = float(v.est_market_value_gbp)
                        else:
                            variant_market_avgs[v.id] = market_avg

            new_saved_count = 0
            new_snipes_count = 0
            updated_existing = False
            seller_batch_counts: Dict[str, int] = {}
            seen_batch_ids: set[str] = set()

            for raw in raw_items:
                item_data = _extract_item(raw)
                if not item_data:
                    continue

                vinted_id = str(item_data.get("id") or "").strip()
                if not vinted_id or vinted_id in seen_batch_ids:
                    continue
                seen_batch_ids.add(vinted_id)

                vid_int = int(vinted_id) if vinted_id.isdigit() else 0
                item_title = item_data.get("title", "")
                item_brand = item_data.get("brand")
                item_price = float(item_data.get("price", 0.0))
                item_size = item_data.get("size")
                item_desc = str(item_data.get("description", "") or "").strip()
                item_url = str(item_data.get("url", "") or "").strip()

                # Check if item is already in DB for this tracked search
                existing_item = (
                    db.query(SoldItem)
                    .filter(
                        SoldItem.tracked_search_id == search.id,
                        SoldItem.vinted_item_id == vinted_id,
                    )
                    .first()
                )

                if existing_item:
                    # Sync price updates
                    live_price = item_data.get("price", 0.0)
                    if live_price and live_price > 0 and abs(float(existing_item.price or 0.0) - float(live_price)) >= 0.01:
                        old_p = float(existing_item.price or 0.0)
                        existing_item.price = round(float(live_price), 2)
                        updated_existing = True
                        from .scraper_logger import scraper_logger
                        scraper_logger.log_price_updated(
                            title=existing_item.title or "Item",
                            old_price=old_p,
                            new_price=float(live_price),
                            search_text=search.search_text,
                            item_id=str(existing_item.vinted_item_id or ""),
                            url=existing_item.url or "",
                        )
                    # Re-activate if was marked sold/inactive previously
                    if existing_item.is_sold or existing_item.status != "active":
                        existing_item.is_sold = False
                        existing_item.status = "active"
                        updated_existing = True
                    if not existing_item.seller_id and item_data.get("seller_id"):
                        existing_item.seller_id = str(item_data.get("seller_id"))
                        updated_existing = True
                    if not existing_item.seller_username and item_data.get("seller_username"):
                        existing_item.seller_username = str(item_data.get("seller_username"))
                        updated_existing = True

                # 1. Spam & Coherence Filter
                relevant, _ = is_item_relevant(
                    target_brand=search.brand,
                    target_categories=target_categories,
                    must_exclude=search.must_exclude,
                    item_brand=item_brand,
                    item_title=item_title,
                    exclude_child_sizes=bool(getattr(search, "exclude_child_sizes", False)),
                    item_size=item_size,
                    item_description=item_desc,
                    item_url=item_url,
                )
                if not relevant:
                    continue

                is_child, child_reason = is_child_size(item_title, item_size)

                # Track seller listing count within this batch
                sid = str(item_data.get("seller_id") or "").strip()
                if sid:
                    seller_batch_counts[sid] = seller_batch_counts.get(sid, 0) + 1

                # 2. Anti-Rep & Seller Review Filter
                clean, rep_reason = evaluate_antirep_rules(
                    db=db,
                    tracked_search_id=search.id,
                    item_data=item_data,
                    antirep_config=antirep_config,
                    scraper=scraper,
                    seller_batch_counts=seller_batch_counts,
                    deep_inspection=False,
                )

                now_utc = datetime.now(timezone.utc)
                base_search_max_price = self.item_max_prices.get(search.id, self.max_price)
                target_max_price = base_search_max_price

                # 3. Local Multi-Category Variant Classification & Faceted Sniper Filtering
                matched_var_res: Optional[Dict[str, Any]] = None
                matched_v_ids: List[int] = []
                matched_v_obj: Optional[ProductVariant] = None
                variant_allowed_for_snipe = True

                if active_variants:
                    from ..routers.variants import evaluate_sniper_variant_match

                    cached_ids = [
                        vid for vid in (existing_item.parsed_variant_ids() if existing_item else [])
                        if vid in variants_map
                    ]
                    if existing_item and cached_ids:
                        matched_v_ids = cached_ids
                        matched_v_obj = variants_map.get(cached_ids[-1])
                        matched_var_res = {
                            "variant_id": cached_ids[-1],
                            "variant_ids": cached_ids,
                            "variant_name": existing_item.variant_name or " › ".join(variants_map[i].variant_name for i in cached_ids),
                            "variant_names": [variants_map[i].variant_name for i in cached_ids],
                            "confidence": float(existing_item.variant_confidence or 0.78),
                            "method": existing_item.variant_method or "cached",
                            "evidence": existing_item.variant_evidence or "",
                        }
                    else:
                        max_possible_price = max(
                            [base_search_max_price]
                            + [float(v.target_max_price) for v in active_variants if v.target_max_price and v.target_max_price > 0]
                        )
                        should_use_vision = (existing_item is None) or (0 < item_price <= max_possible_price)
                        matched_var_res = variant_vision_service.classify_item_variant(
                            db=db,
                            tracked_search=search,
                            item_data=item_data,
                            variants=active_variants,
                            allow_vision=should_use_vision,
                        )
                        if matched_var_res:
                            matched_v_ids = list(matched_var_res.get("variant_ids") or ([matched_var_res["variant_id"]] if matched_var_res.get("variant_id") else []))
                            matched_v_obj = variants_map.get(matched_v_ids[-1]) if matched_v_ids else None
                            if existing_item and existing_item.variant_method != "manual":
                                existing_item.apply_variant_result(matched_var_res)
                                updated_existing = True

                    variant_allowed_for_snipe, var_price_cap = evaluate_sniper_variant_match(matched_v_ids, active_variants)
                    if var_price_cap is not None and var_price_cap > 0:
                        target_max_price = var_price_cap

                is_sniper_candidate = (
                    clean
                    and variant_allowed_for_snipe
                    and not (existing_item and existing_item.is_replica)
                    and (0 < item_price <= target_max_price)
                    and vinted_id not in self._seen_deal_ids
                    and vinted_id not in self._cleared_deal_ids
                )

                # Determine real Vinted upload timestamp for sniper candidates (or use monotonic cutoff for older IDs)
                real_uploaded_at: Optional[datetime] = None
                real_age_minutes: Optional[int] = None

                if is_sniper_candidate:
                    if self._oldest_expired_vinted_id > 0 and 0 < vid_int <= self._oldest_expired_vinted_id:
                        # Monotonic Vinted ID proof: this item was created before an item already proven > max_age_minutes old
                        real_age_minutes = max(self.max_age_minutes + 1, self._oldest_expired_age_mins)
                        real_uploaded_at = now_utc - timedelta(minutes=real_age_minutes)
                        self._seen_deal_ids.add(vinted_id)
                    else:
                        # Live-verify the exact Vinted 'Uploaded' timestamp on the item page
                        real_uploaded_at = get_item_uploaded_at(vinted_id)
                        if real_uploaded_at:
                            if real_uploaded_at.tzinfo is None:
                                real_uploaded_at = real_uploaded_at.replace(tzinfo=timezone.utc)
                            real_age_minutes = max(0, int((now_utc - real_uploaded_at).total_seconds() // 60))
                            if real_age_minutes > self.max_age_minutes:
                                self._seen_deal_ids.add(vinted_id)
                                if vid_int > self._oldest_expired_vinted_id:
                                    self._oldest_expired_vinted_id = vid_int
                                    self._oldest_expired_age_mins = real_age_minutes

                    # Live description category coherence check (filters out shoes/mismatches with generic titles)
                    live_desc = get_item_description(vinted_id)
                    if live_desc:
                        item_desc = live_desc
                        item_data["description"] = live_desc
                        still_relevant, leak_reason = is_item_relevant(
                            target_brand=search.brand,
                            target_categories=target_categories,
                            must_exclude=search.must_exclude,
                            item_brand=item_brand,
                            item_title=item_title,
                            exclude_child_sizes=bool(getattr(search, "exclude_child_sizes", False)),
                            item_size=item_size,
                            item_description=live_desc,
                            item_url=item_url,
                        )
                        if not still_relevant:
                            self._seen_deal_ids.add(vinted_id)
                            logger.info("Candidate deal '%s' rejected by description filter: %s", item_title, leak_reason)
                            continue
                        # Re-evaluate multi-category classification now that full Vinted description is available
                        if active_variants:
                            refined_var = variant_vision_service.classify_item_variant(
                                db=db,
                                tracked_search=search,
                                item_data=item_data,
                                variants=active_variants,
                                allow_vision=True,
                            )
                            if refined_var:
                                matched_var_res = refined_var
                                matched_v_ids = list(refined_var.get("variant_ids") or ([refined_var["variant_id"]] if refined_var.get("variant_id") else []))
                                matched_v_obj = variants_map.get(matched_v_ids[0]) if matched_v_ids else None
                                allowed_after_desc, cap_after_desc = evaluate_sniper_variant_match(matched_v_ids, active_variants)
                                if not allowed_after_desc:
                                    is_sniper_candidate = False
                                elif cap_after_desc is not None and cap_after_desc > 0:
                                    target_max_price = cap_after_desc
                                    if item_price > target_max_price:
                                        is_sniper_candidate = False

                # Persist item if not already in DB
                if not existing_item:
                    url = item_data.get("url", "")
                    if url and not url.startswith("http"):
                        url = f"{VINTED_BASE_URL}{url}"

                    serializable_data = dict(item_data)
                    serializable_data.pop("raw_item", None)
                    serializable_data.pop("raw_photo", None)

                    new_sold = SoldItem(
                        tracked_search_id=search.id,
                        vinted_item_id=vinted_id,
                        title=item_title,
                        price=item_price,
                        currency=item_data.get("currency", "GBP"),
                        brand=item_brand or None,
                        size=item_size or None,
                        condition=item_data.get("condition") or None,
                        seller_id=item_data.get("seller_id") or None,
                        seller_username=item_data.get("seller_username") or None,
                        photo_url=item_data.get("photo_url"),
                        url=url or None,
                        status=item_data.get("status") or "active",
                        is_sold=False,
                        is_replica=not clean,
                        replica_reason=rep_reason if not clean else None,
                        is_child_size=is_child,
                        child_size_reason=child_reason if is_child else None,
                        variant_id=matched_var_res["variant_id"] if matched_var_res else None,
                        variant_ids_json=json.dumps(matched_v_ids, separators=(",", ":")) if matched_v_ids else None,
                        variant_name=matched_var_res["variant_name"] if matched_var_res else None,
                        variant_names_json=json.dumps(matched_var_res["variant_names"]) if (matched_var_res and matched_var_res.get("variant_names")) else None,
                        variant_confidence=matched_var_res["confidence"] if matched_var_res else 0.0,
                        variant_method=matched_var_res["method"] if matched_var_res else None,
                        variant_evidence=matched_var_res["evidence"] if matched_var_res else None,
                        captured_at=now_utc,
                        item_created_at=real_uploaded_at,
                        raw_data=json.dumps(serializable_data),
                    )
                    db.add(new_sold)
                    new_saved_count += 1
                    from .scraper_logger import scraper_logger
                    scraper_logger.log_item_added(
                        item_data={
                            "id": vinted_id,
                            "title": item_title,
                            "price": item_price,
                            "currency": item_data.get("currency", "GBP"),
                            "brand": item_brand or search.brand or "",
                            "size": item_size or "",
                            "condition": item_data.get("condition") or "",
                            "seller_username": item_data.get("seller_username") or "",
                            "photo_url": item_data.get("photo_url"),
                            "url": url,
                            "variant_name": matched_var_res["variant_name"] if matched_var_res else "",
                        },
                        search_text=search.search_text,
                        source="sniper",
                    )
                else:
                    # Synchronize newly resolved seller metadata, real upload time, and replica status onto existing DB item
                    if real_uploaded_at and not existing_item.item_created_at:
                        existing_item.item_created_at = real_uploaded_at
                        updated_existing = True
                    if not clean and not existing_item.is_replica:
                        existing_item.is_replica = True
                        existing_item.replica_reason = rep_reason
                        updated_existing = True
                    if matched_var_res and existing_item.variant_method != "manual":
                        existing_item.apply_variant_result(matched_var_res)
                        updated_existing = True

                # If item failed anti-rep or seller review check, never trigger deal sniper alerts
                if not clean or (existing_item and existing_item.is_replica):
                    with self._lock:
                        self.sniped_deals = [
                            d for d in self.sniped_deals if str(d.get("id")) != str(vinted_id)
                        ]
                    continue

                # 4. Trigger Snipe ONLY if real Vinted upload age is verified and <= self.max_age_minutes
                if (
                    is_sniper_candidate
                    and real_age_minutes is not None
                    and real_age_minutes <= self.max_age_minutes
                ):
                    self._seen_deal_ids.add(vinted_id)
                    new_snipes_count += 1
                    self.total_snipes_found += 1

                    matched_avgs = [variant_market_avgs[vid] for vid in matched_v_ids if vid in variant_market_avgs]
                    effective_market_avg = max(matched_avgs) if matched_avgs else market_avg

                    discount_pct = 0.0
                    if effective_market_avg > item_price:
                        discount_pct = round(((effective_market_avg - item_price) / effective_market_avg) * 100, 1)

                    savings = round(max(0.0, effective_market_avg - item_price), 2) if effective_market_avg > item_price else 0.0

                    raw_url = str(item_data.get("url") or "").strip()
                    if raw_url.startswith(("http://", "https://")):
                        deal_url = raw_url
                    elif raw_url:
                        deal_url = f"https://www.vinted.co.uk/{raw_url.lstrip('/')}"
                    else:
                        deal_url = f"https://www.vinted.co.uk/items/{vinted_id}"

                    deal_card = {
                        "id": vinted_id,
                        "search_id": search.id,
                        "search_text": search.search_text,
                        "title": item_title,
                        "price": item_price,
                        "currency": item_data.get("currency", "GBP"),
                        "brand": item_brand or search.brand or "",
                        "size": item_data.get("size") or "",
                        "condition": item_data.get("condition") or "",
                        "seller_id": item_data.get("seller_id") or "",
                        "seller_username": item_data.get("seller_username") or "",
                        "photo_url": item_data.get("photo_url"),
                        "url": deal_url,
                        "uploaded_at": real_uploaded_at.isoformat() if real_uploaded_at else now_utc.isoformat(),
                        "discovered_at": now_utc.isoformat(),
                        "age_minutes": real_age_minutes,
                        "market_avg_price": round(effective_market_avg, 2),
                        "discount_pct": discount_pct,
                        "savings": savings,
                        "threshold_price": target_max_price,
                        "variant_id": matched_var_res["variant_id"] if matched_var_res else None,
                        "variant_ids": matched_v_ids,
                        "variant_name": matched_var_res["variant_name"] if matched_var_res else None,
                        "variant_names": matched_var_res.get("variant_names", []) if matched_var_res else [],
                        "variant_confidence": matched_var_res["confidence"] if matched_var_res else None,
                        "variant_method": matched_var_res["method"] if matched_var_res else None,
                        "variant_evidence": matched_var_res["evidence"] if matched_var_res else None,
                        "resale_tier": matched_v_obj.resale_tier if matched_v_obj else None,
                    }

                    # Advanced AI Resale Profitability & Risk Evaluation
                    resale_eval = resale_evaluator.evaluate_deal(
                        db=db,
                        tracked_search=search,
                        deal=deal_card,
                        custom_variant=matched_v_obj,
                    )
                    deal_card.update({
                        "ai_score": resale_eval["ai_score"],
                        "ai_verdict": resale_eval["verdict"],
                        "is_recommended": resale_eval["is_recommended"],
                        "projected_net_profit": resale_eval["projected_net_profit"],
                        "projected_roi_pct": resale_eval["roi_pct"],
                        "projected_resale_price": resale_eval["projected_resale_price"],
                        "total_landed_cost": resale_eval["total_landed_cost"],
                        "cost_breakdown": resale_eval["cost_breakdown"],
                        "liquidity_rating": resale_eval["liquidity_rating"],
                        "estimated_days_to_sell": resale_eval["estimated_days_to_sell"],
                        "seller_risk_level": resale_eval["seller_risk_level"],
                        "ai_reasoning": resale_eval["reasoning"],
                    })

                    # AI Resale Quality Gate: prevent low-margin or scam items from polluting stream
                    if self.ai_resale_gate_enabled:
                        if (
                            resale_eval["projected_net_profit"] < self.min_net_profit
                            or resale_eval["roi_pct"] < self.min_roi_pct
                            or resale_eval["verdict"] == "PASS"
                        ):
                            self._add_log(
                                "SHIELD",
                                f"⚠️ Filtered by AI Profit Gate: '{item_title}' (£{item_price:.2f}) projected profit £{resale_eval['projected_net_profit']:+.2f} ({resale_eval['roi_pct']:.0f}% ROI, {resale_eval['verdict']}) below threshold (Min £{self.min_net_profit:.2f}, {self.min_roi_pct:.0f}% ROI).",
                            )
                            continue

                    with self._lock:
                        self.sniped_deals.insert(0, deal_card)
                        if len(self.sniped_deals) > 100:
                            self.sniped_deals = self.sniped_deals[:100]

                    var_tag = f" [{matched_var_res['variant_name']}]" if matched_var_res and matched_var_res.get("variant_name") else ""
                    self._add_log(
                        "SNIPE",
                        f"🔥 SNIPED{var_tag}: '{item_title}' for £{item_price:.2f} (AI {resale_eval['verdict']} {resale_eval['ai_score']}/100, Net +£{resale_eval['projected_net_profit']:.2f}, {real_age_minutes}m ago)",
                    )

            if new_saved_count > 0 or updated_existing or len(db.new) > 0 or len(db.dirty) > 0:
                try:
                    db.commit()
                    if new_saved_count > 0:
                        with self._lock:
                            self.total_items_saved += new_saved_count
                except Exception as commit_err:
                    db.rollback()
                    logger.warning("Error committing sniper items to DB: %s", commit_err)

            if new_snipes_count > 0 or new_saved_count > 0:
                self._add_log(
                    "SCAN",
                    f"Checked '{search.search_text}': {new_saved_count} new items added to database, {new_snipes_count} snipe bargains found!",
                )

        except Exception as exc:
            db.rollback()
            logger.debug("Error in sniper poll: %s", exc)
        finally:
            db.close()


# Per-user DealSniperService registry
_user_snipers: Dict[int, DealSniperService] = {}
_user_snipers_lock = threading.Lock()
_fallback_sniper: Optional[DealSniperService] = None


def get_user_sniper(user_id: Optional[int] = None) -> DealSniperService:
    """Return the isolated DealSniperService instance for the given user_id."""
    global _fallback_sniper
    if user_id is None:
        from ..auth import get_active_user_id
        user_id = get_active_user_id()

    with _user_snipers_lock:
        if user_id is None:
            if _fallback_sniper is None:
                _fallback_sniper = DealSniperService(user_id=None)
            return _fallback_sniper
        if user_id not in _user_snipers:
            _user_snipers[user_id] = DealSniperService(user_id=user_id)
        return _user_snipers[user_id]


class _ContextualDealSniperProxy:
    """Routes deal_sniper calls to the active user's isolated DealSniperService."""

    def __getattr__(self, name: str):
        return getattr(get_user_sniper(), name)


deal_sniper = _ContextualDealSniperProxy()


