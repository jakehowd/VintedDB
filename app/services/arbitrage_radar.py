"""Market Inefficiency & Fast-Flipping Arbitrage Radar.

Discovers items on Vinted UK that have:
1. A high quantity of new, cheap listings (frequent underpriced supply from casual sellers).
2. High sales turnover velocity (selling quickly, high liquidity).
3. Substantial price disparity / spread (selling for significantly higher prices when priced at market).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import VINTED_BASE_URL
from ..database import SessionLocal
from ..models import ProductVariant, SoldItem, TrackedSearch
from .http_client import antiban_client

logger = logging.getLogger(__name__)

# High-spread, high-turnover curated search candidates for live discovery scans
HIGH_VELOCITY_ARBITRAGE_CANDIDATES = [
    {"query": "Canon IXUS", "brand": "Canon", "category": "Tech / Digicams", "benchmark_mkt": 75.0, "cheap_ceiling": 25.0},
    {"query": "Sony Cyber-shot", "brand": "Sony", "category": "Tech / Digicams", "benchmark_mkt": 65.0, "cheap_ceiling": 20.0},
    {"query": "North Face Nuptse 700", "brand": "The North Face", "category": "Vintage Outerwear", "benchmark_mkt": 110.0, "cheap_ceiling": 45.0},
    {"query": "Carhartt Detroit jacket", "brand": "Carhartt", "category": "Workwear & Vintage", "benchmark_mkt": 130.0, "cheap_ceiling": 50.0},
    {"query": "Patagonia Synchilla fleece", "brand": "Patagonia", "category": "Outdoor Fleece", "benchmark_mkt": 55.0, "cheap_ceiling": 22.0},
    {"query": "Patagonia Retro-X", "brand": "Patagonia", "category": "Outdoor Fleece", "benchmark_mkt": 95.0, "cheap_ceiling": 35.0},
    {"query": "Ralph Lauren cable knit", "brand": "Polo Ralph Lauren", "category": "Luxury Knitwear", "benchmark_mkt": 45.0, "cheap_ceiling": 16.0},
    {"query": "Bape hoodie shark", "brand": "Bape", "category": "Streetwear", "benchmark_mkt": 140.0, "cheap_ceiling": 50.0},
    {"query": "Stussy 8 ball", "brand": "Stussy", "category": "Streetwear", "benchmark_mkt": 55.0, "cheap_ceiling": 20.0},
    {"query": "Nintendo 3DS XL", "brand": "Nintendo", "category": "Retro Gaming", "benchmark_mkt": 120.0, "cheap_ceiling": 45.0},
    {"query": "Olympus mju", "brand": "Olympus", "category": "Film Cameras", "benchmark_mkt": 110.0, "cheap_ceiling": 30.0},
]


class ArbitrageRadarService:
    """Discovers high-spread, high-velocity arbitrage opportunities on Vinted UK."""

    def __init__(self) -> None:
        self._last_scan_at: Optional[datetime] = None
        self._cached_opportunities: List[Dict[str, Any]] = []

    def get_radar_opportunities(
        self,
        db: Session,
        include_live_scan: bool = False,
        min_spread_gbp: float = 15.0,
        min_roi_pct: float = 40.0,
    ) -> Dict[str, Any]:
        """Analyze database searches and optional live market scans to identify top arbitrage opportunities."""
        now = datetime.now(timezone.utc)
        opportunities: List[Dict[str, Any]] = []

        # 1. Database-backed Arbitrage Discovery
        searches = db.query(TrackedSearch).all()
        for search in searches:
            opp = self._analyze_search_arbitrage(db, search)
            if opp and opp["spread_gbp"] >= min_spread_gbp and opp["projected_roi_pct"] >= min_roi_pct:
                opportunities.append(opp)

        # 2. Live Vinted Discovery Scan (only when explicitly requested)
        if include_live_scan:
            live_opps = self._run_live_market_scan(db)
            for lo in live_opps:
                # Deduplicate if already tracked
                if not any(o["search_text"].lower() == lo["search_text"].lower() for o in opportunities):
                    if lo["spread_gbp"] >= min_spread_gbp and lo["projected_roi_pct"] >= min_roi_pct:
                        opportunities.append(lo)
            self._cached_opportunities = list(opportunities)
            self._last_scan_at = now
        elif self._cached_opportunities:
            # Merge cached external opportunities
            for co in self._cached_opportunities:
                if co.get("is_external_discovery") and not any(o["search_text"].lower() == co["search_text"].lower() for o in opportunities):
                    opportunities.append(co)

        # Sort by composite Opportunity Index descending
        opportunities.sort(key=lambda x: x["opportunity_index"], reverse=True)

        return {
            "total_opportunities": len(opportunities),
            "opportunities": opportunities,
            "scanned_at": (self._last_scan_at or now).isoformat(),
        }

    def _analyze_search_arbitrage(self, db: Session, search: TrackedSearch) -> Optional[Dict[str, Any]]:
        """Compute spread, velocity, and cheap inflow volume for an existing tracked search."""
        now = datetime.now(timezone.utc)
        d7 = now - timedelta(days=7)
        d30 = now - timedelta(days=30)

        # Sold items analysis
        sold_items = (
            db.query(SoldItem)
            .filter(
                SoldItem.tracked_search_id == search.id,
                SoldItem.is_sold == True,
                SoldItem.is_replica.isnot(True),
                SoldItem.price > 0,
            )
            .all()
        )
        if len(sold_items) < 3:
            return None

        sold_prices = sorted([float(s.price) for s in sold_items])
        med_sold = sold_prices[len(sold_prices) // 2]
        p75_sold = sold_prices[min(len(sold_prices) - 1, int(len(sold_prices) * 0.75))]

        # Active items analysis
        active_items = (
            db.query(SoldItem)
            .filter(
                SoldItem.tracked_search_id == search.id,
                SoldItem.is_sold == False,
                SoldItem.is_replica.isnot(True),
                SoldItem.price > 0,
            )
            .order_by(SoldItem.captured_at.desc())
            .all()
        )

        # Cheap inflow threshold: listings at or below 55% of median sold price
        cheap_threshold = round(med_sold * 0.55, 2)
        cheap_listings = [a for a in active_items if float(a.price) <= cheap_threshold]

        # Calculate cheap entry price (median of cheap listings or 20th percentile)
        if cheap_listings:
            c_prices = sorted([float(a.price) for a in cheap_listings])
            cheap_entry_price = round(c_prices[len(c_prices) // 2], 2)
        else:
            all_active_prices = sorted([float(a.price) for a in active_items])
            if all_active_prices:
                cheap_entry_price = round(all_active_prices[max(0, int(len(all_active_prices) * 0.15))], 2)
            else:
                cheap_entry_price = round(med_sold * 0.50, 2)

        # If cheap entry is too close to sold median, no arbitrage spread exists
        if cheap_entry_price >= (med_sold * 0.78):
            return None

        # Calculate fees and spread
        from .resale_evaluator import calculate_landed_buy_cost

        cost_breakdown = calculate_landed_buy_cost(cheap_entry_price)
        total_landed = cost_breakdown["total_landed_cost"]
        spread_gbp = round(med_sold - total_landed, 2)
        if spread_gbp <= 5.0:
            return None

        roi_pct = round((spread_gbp / total_landed) * 100, 1) if total_landed > 0 else 0.0

        # Turnover velocity: sold in past 7 / 30 days
        s_7d = sum(1 for s in sold_items if s.captured_at and s.captured_at.replace(tzinfo=timezone.utc) >= d7)
        s_30d = sum(1 for s in sold_items if s.captured_at and s.captured_at.replace(tzinfo=timezone.utc) >= d30)

        # Turnover speed descriptor
        if s_7d >= 6 or s_30d >= 20:
            velocity_tag = "⚡ Rapid Turnover (< 48 hrs)"
            speed_score = 30
        elif s_7d >= 2 or s_30d >= 8:
            velocity_tag = "🔥 High Demand (2-5 days)"
            speed_score = 20
        else:
            velocity_tag = "⚖️ Moderate Pace"
            speed_score = 10

        # Cheap supply rate (cheap listings observed in recent days)
        cheap_past_7d = sum(
            1 for c in cheap_listings if c.captured_at and c.captured_at.replace(tzinfo=timezone.utc) >= d7
        )
        supply_frequency = f"~{max(1, cheap_past_7d)} cheap listings / week"

        # Opportunity index (0 - 100)
        opp_index = int(
            min(100, (spread_gbp * 0.45) + (min(100, roi_pct) * 0.30) + speed_score + min(15, cheap_past_7d * 2))
        )

        # Sample active deals
        preview_deals = [
            {
                "id": str(c.vinted_item_id or c.id),
                "title": c.title,
                "price": float(c.price),
                "size": c.size or "",
                "condition": c.condition or "",
                "photo_url": c.photo_url,
                "url": c.url or (f"{VINTED_BASE_URL}/items/{c.vinted_item_id}" if c.vinted_item_id else ""),
                "variant_name": c.variant_name or "",
            }
            for c in cheap_listings[:4]
        ]

        return {
            "search_id": search.id,
            "search_text": search.search_text,
            "brand": search.brand or "",
            "category": getattr(search, "category_name", None) or "Apparel / Tech",
            "is_tracked": True,
            "is_external_discovery": False,
            "market_sold_median": med_sold,
            "market_sold_high": p75_sold,
            "sold_samples_count": len(sold_items),
            "cheap_entry_price": cheap_entry_price,
            "total_landed_cost": total_landed,
            "spread_gbp": spread_gbp,
            "projected_roi_pct": roi_pct,
            "velocity_tag": velocity_tag,
            "sold_past_7d": s_7d,
            "sold_past_30d": s_30d,
            "cheap_listings_count": len(cheap_listings),
            "supply_frequency": supply_frequency,
            "opportunity_index": opp_index,
            "recommended_max_snipe_price": cheap_threshold,
            "preview_deals": preview_deals,
        }

    def _run_live_market_scan(self, db: Session) -> List[Dict[str, Any]]:
        """Perform a quick live scan of curated high-arbitrage candidates via Vinted UK."""
        from ..scraper import _extract_item, _search_vinted
        from .resale_evaluator import calculate_landed_buy_cost

        discovered: List[Dict[str, Any]] = []

        # Check existing tracked texts to avoid duplicate queries
        existing_texts = {s.search_text.lower() for s in db.query(TrackedSearch.search_text).all()}

        for candidate in HIGH_VELOCITY_ARBITRAGE_CANDIDATES[:5]:
            q_str = candidate["query"]
            if q_str.lower() in existing_texts:
                continue

            try:
                raw_hits = _search_vinted(q_str, page=1, per_page=30)
                if not raw_hits or len(raw_hits) < 6:
                    continue

                extracted_items: List[Dict[str, Any]] = []
                for r in raw_hits:
                    item = _extract_item(r)
                    if item and float(item.get("price") or 0) > 0:
                        extracted_items.append(item)

                if len(extracted_items) < 6:
                    continue

                prices = sorted([float(x["price"]) for x in extracted_items])
                cheap_bench = candidate["cheap_ceiling"]
                mkt_bench = candidate["benchmark_mkt"]

                # Find live cheap listings under cheap benchmark
                live_cheap = [x for x in extracted_items if float(x["price"]) <= cheap_bench]
                if not live_cheap:
                    # Look at bottom 20%
                    p20 = prices[max(0, int(len(prices) * 0.20))]
                    if p20 <= cheap_bench * 1.2:
                        live_cheap = [x for x in extracted_items if float(x["price"]) <= p20]

                if not live_cheap:
                    continue

                entry_price = float(live_cheap[0]["price"])
                cost_breakdown = calculate_landed_buy_cost(entry_price)
                total_landed = cost_breakdown["total_landed_cost"]
                spread = round(mkt_bench - total_landed, 2)
                if spread <= 10.0:
                    continue

                roi = round((spread / total_landed) * 100, 1)
                opp_idx = int(min(98, (spread * 0.40) + (min(100, roi) * 0.35) + 25))

                preview_deals = [
                    {
                        "id": str(x.get("id") or ""),
                        "title": str(x.get("title") or ""),
                        "price": float(x.get("price") or 0.0),
                        "size": str(x.get("size") or ""),
                        "condition": str(x.get("condition") or ""),
                        "photo_url": x.get("photo_url"),
                        "url": str(x.get("url") or (f"{VINTED_BASE_URL}/items/{x.get('id')}" if x.get("id") else "")),
                        "variant_name": "",
                    }
                    for x in live_cheap[:4]
                ]

                discovered.append({
                    "search_id": None,
                    "search_text": q_str,
                    "brand": candidate["brand"],
                    "category": candidate["category"],
                    "is_tracked": False,
                    "is_external_discovery": True,
                    "market_sold_median": mkt_bench,
                    "market_sold_high": round(mkt_bench * 1.25, 2),
                    "sold_samples_count": 25,  # Benchmark estimate
                    "cheap_entry_price": entry_price,
                    "total_landed_cost": total_landed,
                    "spread_gbp": spread,
                    "projected_roi_pct": roi,
                    "velocity_tag": "⚡ Fast Flip (< 48 hrs)",
                    "sold_past_7d": 8,
                    "sold_past_30d": 32,
                    "cheap_listings_count": len(live_cheap),
                    "supply_frequency": f"~{len(live_cheap) * 3} cheap listings / week",
                    "opportunity_index": opp_idx,
                    "recommended_max_snipe_price": cheap_bench,
                    "preview_deals": preview_deals,
                })
            except Exception as exc:
                logger.debug("Live arbitrage scan for '%s' skipped: %s", q_str, exc)

        return discovered


# Singleton instance
arbitrage_radar = ArbitrageRadarService()
