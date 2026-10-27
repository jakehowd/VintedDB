"""Autonomous High-Yield Cross-Market Arbitrage Radar & AI Top Picks Engine.

Completely rebuilt for real-world e-commerce & reselling arbitrage:
- Autonomous multi-sector market scanner across high-demand UK resale categories.
- Zero already-sold items guarantee via parallel live availability verification.
- AI Alpha Anomaly Score (0–100) detecting casual/uneducated seller mispricings.
- Potentially good product targets & blueprints calculated by AI for predictive sniping.
- Cross-market verification against eBay UK Sold & Completed realization benchmarks.
- Transparent net profit calculation factoring Vinted buyer protection + postage vs
  eBay seller final value fees (12.8%), regulatory fee, fixed fees, and outbound postage.
- Direct clickable links to live eBay UK sold listings and live Vinted listings.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..config import VINTED_BASE_URL
from ..models import ProductVariant, SoldItem, TrackedSearch
from .ai_arbitrage import ai_arbitrage
from .ebay_verifier import ebay_verifier

logger = logging.getLogger(__name__)

# Sector definitions for autonomous discovery scans
SECTORS_CATALOG = {
    "outerwear": {
        "label": "Gorpcore & Outerwear",
        "icon": "cloud-rain",
        "queries": [
            ("Carhartt Detroit jacket", "Carhartt"),
            ("Carhartt Active jacket", "Carhartt"),
            ("North Face Nuptse 700", "The North Face"),
            ("Patagonia Retro-X fleece", "Patagonia"),
            ("Patagonia Synchilla", "Patagonia"),
            ("Barbour Beaufort jacket", "Barbour"),
            ("Arcteryx Beta jacket", "Arc'teryx"),
        ],
    },
    "streetwear": {
        "label": "Cult Streetwear",
        "icon": "sparkles",
        "queries": [
            ("Stussy 8 ball hoodie", "Stussy"),
            ("Bape shark hoodie", "Bape"),
            ("Corteiz Alcatraz hoodie", "Corteiz"),
            ("Supreme box logo", "Supreme"),
            ("Palace Tri Ferg hoodie", "Palace"),
        ],
    },
    "knitwear": {
        "label": "Luxury Knitwear",
        "icon": "shirt",
        "queries": [
            ("Ralph Lauren cable knit jumper", "Polo Ralph Lauren"),
            ("Ralph Lauren quarter zip", "Polo Ralph Lauren"),
            ("Stone Island crewneck jumper", "Stone Island"),
            ("CP Company lens sweatshirt", "CP Company"),
        ],
    },
    "footwear": {
        "label": "High-Demand Footwear",
        "icon": "footprints",
        "queries": [
            ("New Balance 990v3", "New Balance"),
            ("Nike Dunk Low Retro", "Nike"),
            ("Adidas Samba OG", "Adidas"),
            ("Salomon XT-6", "Salomon"),
        ],
    },
    "sportswear": {
        "label": "Retro Sportswear",
        "icon": "trophy",
        "queries": [
            ("Vintage Nike centre swoosh hoodie", "Nike"),
            ("Vintage 90s football shirt", "Vintage Football"),
        ],
    },
}

# Generic / Unmarked Vinted queries to uncover mislabeled casual seller grails
UNMARKED_ASYMMETRY_QUERIES = [
    ("vintage canvas work jacket", "Vintage"),
    ("corduroy collar work jacket", "Vintage"),
    ("waterproof technical jacket hood", "Other"),
    ("fleece snap pullover", "Vintage"),
    ("grey running trainers retro", "Other"),
    ("shark camo hoodie", "Streetwear"),
]


class ArbitrageRadarService:
    """Discovers and verifies cross-platform arbitrage opportunities between Vinted UK and eBay UK."""

    def __init__(self) -> None:
        self._cached_picks: List[Dict[str, Any]] = []
        self._last_scan_at: Optional[datetime] = None

    def get_todays_top_picks(
        self,
        db: Session,
        sector: str = "all",
        force_refresh: bool = False,
        min_profit_gbp: float = 15.0,
        min_roi_pct: float = 35.0,
    ) -> Dict[str, Any]:
        """Retrieve today's verified cross-market arbitrage picks and AI predictive product targets."""
        now = datetime.now(timezone.utc)

        # Run fresh scan if forced or if no cached data exists
        if force_refresh or not self._cached_picks:
            logger.info("Executing autonomous AI market scan for Today's Arbitrage Top Picks...")
            self._cached_picks = self._run_market_analysis(
                db=db,
                min_profit=min_profit_gbp,
                min_roi=min_roi_pct,
            )
            self._last_scan_at = now

        # Filter active picks by sector if specified and not "all"
        all_picks = list(self._cached_picks)
        if sector and sector.lower() != "all":
            filtered_picks = [p for p in all_picks if p.get("sector") == sector.lower()]
        else:
            filtered_picks = all_picks

        # Sort strictly by AI Alpha Score descending (highest composite anomaly first)
        filtered_picks.sort(key=lambda x: x.get("ai_alpha", {}).get("alpha_score", x.get("opportunity_index", 0)), reverse=True)

        # Retrieve AI-forecasted product targets for this sector
        predicted_targets = ai_arbitrage.get_predicted_product_targets(sector=sector)

        # Aggregate Market Pulse statistics
        sector_counts: Dict[str, int] = {k: 0 for k in SECTORS_CATALOG}
        sector_counts["all"] = len(all_picks)
        for p in all_picks:
            sec = p.get("sector")
            if sec in sector_counts:
                sector_counts[sec] += 1

        max_profit = max([float(p.get("net_flip_profit", 0.0)) for p in all_picks], default=0.0)
        avg_roi = (
            sum([float(p.get("roi_pct", 0.0)) for p in all_picks]) / len(all_picks)
            if all_picks
            else 0.0
        )
        rapid_count = sum(1 for p in all_picks if "Rapid" in p.get("velocity_rating", ""))
        casual_seller_count = sum(1 for p in all_picks if p.get("casual_seller_info", {}).get("is_casual_seller"))

        top_sector_name = "Gorpcore & Outerwear"
        if all_picks:
            counts = {sec: sum(1 for p in all_picks if p.get("sector") == sec) for sec in SECTORS_CATALOG}
            top_key = max(counts, key=counts.get)
            top_sector_name = SECTORS_CATALOG.get(top_key, {}).get("label", "Outerwear")

        return {
            "status": "success",
            "scanned_at": (self._last_scan_at or now).isoformat(),
            "selected_sector": sector,
            "market_pulse": {
                "total_picks": len(all_picks),
                "filtered_picks_count": len(filtered_picks),
                "highest_net_profit_gbp": round(max_profit, 2),
                "average_roi_pct": round(avg_roi, 1),
                "rapid_flips_count": rapid_count,
                "casual_seller_picks_count": casual_seller_count,
                "top_sector": top_sector_name,
                "sector_counts": sector_counts,
                "predicted_products_count": len(predicted_targets),
            },
            "picks": filtered_picks,
            "predicted_products": predicted_targets,
        }

    def _run_market_analysis(
        self,
        db: Session,
        min_profit: float = 15.0,
        min_roi: float = 35.0,
    ) -> List[Dict[str, Any]]:
        """Scans live Vinted feeds & recent database items, then passes candidates through the Live Availability Shield."""
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(hours=48)
        candidates: List[Dict[str, Any]] = []
        seen_item_ids = set()

        # 1. Harvest candidates from recent database items (captured in last 48 hours only)
        db_recent = (
            db.query(SoldItem)
            .filter(
                SoldItem.is_sold == False,
                SoldItem.is_replica.isnot(True),
                SoldItem.price > 0,
                SoldItem.captured_at >= recent_cutoff,
            )
            .order_by(SoldItem.captured_at.desc())
            .limit(300)
            .all()
        )

        for s in db_recent:
            raw_id = str(s.vinted_item_id or s.id)
            if raw_id in seen_item_ids:
                continue

            price = float(s.price or 0.0)
            if price <= 0 or price > 160.0:
                continue

            # Product Recognition AI check for unmarked / vaguely listed grails
            desc_text = getattr(s, "description", "") or ""
            unmarked_info = ai_arbitrage.recognize_unmarked_product({
                "title": s.title,
                "description": desc_text,
                "brand": s.brand,
                "photo_url": s.photo_url or "",
            })

            override_model = unmarked_info["identified_model"] if unmarked_info else None
            effective_brand = unmarked_info["identified_brand"] if unmarked_info else s.brand

            verified_comp = ebay_verifier.verify_item(
                title=s.title,
                price=price,
                brand=effective_brand,
                condition=s.condition,
                size=s.size,
                description=desc_text,
                override_benchmark_model=override_model,
            )

            if verified_comp and verified_comp["net_flip_profit"] >= min_profit and verified_comp["roi_pct"] >= min_roi:
                seen_item_ids.add(raw_id)
                casual_info = ai_arbitrage.calculate_casual_seller_index(
                    title=s.title,
                    price=price,
                    description=desc_text,
                )
                ai_alpha = ai_arbitrage.compute_ai_alpha_score(
                    net_spread_gbp=verified_comp["net_flip_profit"],
                    roi_pct=verified_comp["roi_pct"],
                    ebay_sold_median=verified_comp["ebay_sold_median"],
                    vinted_asking_price=price,
                    recent_sales_count=verified_comp["recent_sales_count"],
                    casual_seller_score=casual_info["casual_seller_score"],
                    is_unmarked_asymmetry=bool(unmarked_info),
                )
                candidates.append({
                    "id": raw_id,
                    "title": s.title,
                    "brand": verified_comp["brand"],
                    "price": price,
                    "size": s.size or "Regular",
                    "condition": s.condition or "Good condition",
                    "photo_url": s.photo_url or "",
                    "url": s.url or (f"{VINTED_BASE_URL}/items/{s.vinted_item_id}" if s.vinted_item_id else ""),
                    "source": "database_stream",
                    "sector": verified_comp["sector"],
                    "sector_label": verified_comp["sector_label"],
                    "ebay_verified_model": verified_comp["model_name"],
                    "ebay_query": verified_comp["ebay_query"],
                    "base_ebay_sold_median": verified_comp["base_ebay_sold_median"],
                    "ebay_sold_median": verified_comp["ebay_sold_median"],
                    "ebay_sold_low": verified_comp["ebay_sold_low"],
                    "ebay_sold_high": verified_comp["ebay_sold_high"],
                    "ebay_sold_range_str": verified_comp["ebay_sold_range_str"],
                    "recent_sales_count": verified_comp["recent_sales_count"],
                    "velocity_rating": verified_comp["velocity_rating"],
                    "ebay_verification_url": verified_comp["ebay_verification_url"],
                    "condition_info": verified_comp["condition_info"],
                    "condition_badge": verified_comp["condition_badge"],
                    "condition_multiplier": verified_comp["condition_multiplier"],
                    "size_info": verified_comp["size_info"],
                    "size_badge": verified_comp["size_badge"],
                    "size_multiplier": verified_comp["size_multiplier"],
                    "colorway_info": verified_comp["colorway_info"],
                    "exact_colorway": verified_comp["exact_colorway"],
                    "exact_style": verified_comp["exact_style"],
                    "colorway_multiplier": verified_comp["colorway_multiplier"],
                    "colorway_premium_label": verified_comp["colorway_premium_label"],
                    "exact_ebay_query": verified_comp["exact_ebay_query"],
                    "exact_ebay_sold_url": verified_comp["exact_ebay_sold_url"],
                    "confirmed_profitable_sale": verified_comp["confirmed_profitable_sale"],
                    "combined_realization_multiplier": verified_comp["combined_realization_multiplier"],
                    "is_unmarked_asymmetry": bool(unmarked_info),
                    "unmarked_asymmetry": unmarked_info,
                    "vinted_asking_price": verified_comp["vinted_asking_price"],
                    "vinted_landed_cost": verified_comp["vinted_landed_cost"],
                    "vinted_cost_breakdown": verified_comp["vinted_cost_breakdown"],
                    "ebay_net_payout": verified_comp["ebay_net_payout"],
                    "ebay_fees_breakdown": verified_comp["ebay_fees_breakdown"],
                    "net_flip_profit": verified_comp["net_flip_profit"],
                    "roi_pct": verified_comp["roi_pct"],
                    "discount_pct": verified_comp["discount_pct"],
                    "opportunity_index": ai_alpha["alpha_score"],
                    "ai_alpha": ai_alpha,
                    "casual_seller_info": casual_info,
                    "recommended_snipe_cap": verified_comp["recommended_snipe_cap"],
                })

        # 2. Fresh live scan across profitable sector feeds + generic unmarked queries
        from ..scraper import _extract_item, _search_vinted

        queries_pool = []
        for sector_id, sector_info in SECTORS_CATALOG.items():
            for query_tuple in sector_info["queries"][:3]:  # Top 3 queries per sector
                queries_pool.append((query_tuple[0], query_tuple[1], sector_id))

        # Include unmarked / generic queries for extreme hidden grail discovery
        for unmark_q, unmark_brand in UNMARKED_ASYMMETRY_QUERIES[:3]:
            queries_pool.append((unmark_q, unmark_brand, "unmarked"))

        for query_tuple in queries_pool:
            query_str, expected_brand, query_cat = query_tuple
            try:
                raw_items = _search_vinted(query_str, page=1, per_page=25)
                if not raw_items:
                    continue

                for raw in raw_items:
                    extracted = _extract_item(raw)
                    if not extracted:
                        continue

                    str_id = str(extracted.get("id") or "")
                    if not str_id or str_id in seen_item_ids:
                        continue

                    price_val = float(extracted.get("price") or 0.0)
                    if price_val <= 0 or price_val > 160.0:
                        continue

                    raw_title = str(extracted.get("title") or "")
                    raw_desc = str(extracted.get("description") or "")
                    raw_brand = str(extracted.get("brand") or expected_brand)
                    raw_cond = str(extracted.get("condition") or "")
                    raw_size = str(extracted.get("size") or "Regular")
                    raw_photo = extracted.get("photo_url") or ""

                    # Run Product Recognition AI for unmarked grails
                    unmarked_info = ai_arbitrage.recognize_unmarked_product({
                        "title": raw_title,
                        "description": raw_desc,
                        "brand": raw_brand,
                        "photo_url": raw_photo,
                    })

                    override_model = unmarked_info["identified_model"] if unmarked_info else None
                    effective_brand = unmarked_info["identified_brand"] if unmarked_info else raw_brand

                    comp = ebay_verifier.verify_item(
                        title=raw_title,
                        price=price_val,
                        brand=effective_brand,
                        condition=raw_cond,
                        size=raw_size,
                        description=raw_desc,
                        override_benchmark_model=override_model,
                    )

                    if comp and comp["net_flip_profit"] >= min_profit and comp["roi_pct"] >= min_roi:
                        seen_item_ids.add(str_id)
                        casual_info = ai_arbitrage.calculate_casual_seller_index(
                            title=raw_title,
                            price=price_val,
                            description=raw_desc,
                        )
                        ai_alpha = ai_arbitrage.compute_ai_alpha_score(
                            net_spread_gbp=comp["net_flip_profit"],
                            roi_pct=comp["roi_pct"],
                            ebay_sold_median=comp["ebay_sold_median"],
                            vinted_asking_price=price_val,
                            recent_sales_count=comp["recent_sales_count"],
                            casual_seller_score=casual_info["casual_seller_score"],
                            is_unmarked_asymmetry=bool(unmarked_info),
                        )
                        candidates.append({
                            "id": str_id,
                            "title": raw_title,
                            "brand": comp["brand"],
                            "price": price_val,
                            "size": raw_size,
                            "condition": raw_cond or "Good condition",
                            "photo_url": raw_photo,
                            "url": str(extracted.get("url") or f"{VINTED_BASE_URL}/items/{str_id}"),
                            "source": "live_discovery",
                            "sector": comp["sector"],
                            "sector_label": comp["sector_label"],
                            "ebay_verified_model": comp["model_name"],
                            "ebay_query": comp["ebay_query"],
                            "base_ebay_sold_median": comp["base_ebay_sold_median"],
                            "ebay_sold_median": comp["ebay_sold_median"],
                            "ebay_sold_low": comp["ebay_sold_low"],
                            "ebay_sold_high": comp["ebay_sold_high"],
                            "ebay_sold_range_str": comp["ebay_sold_range_str"],
                            "recent_sales_count": comp["recent_sales_count"],
                            "velocity_rating": comp["velocity_rating"],
                            "ebay_verification_url": comp["ebay_verification_url"],
                            "condition_info": comp["condition_info"],
                            "condition_badge": comp["condition_badge"],
                            "condition_multiplier": comp["condition_multiplier"],
                            "size_info": comp["size_info"],
                            "size_badge": comp["size_badge"],
                            "size_multiplier": comp["size_multiplier"],
                            "colorway_info": comp["colorway_info"],
                            "exact_colorway": comp["exact_colorway"],
                            "exact_style": comp["exact_style"],
                            "colorway_multiplier": comp["colorway_multiplier"],
                            "colorway_premium_label": comp["colorway_premium_label"],
                            "exact_ebay_query": comp["exact_ebay_query"],
                            "exact_ebay_sold_url": comp["exact_ebay_sold_url"],
                            "confirmed_profitable_sale": comp["confirmed_profitable_sale"],
                            "combined_realization_multiplier": comp["combined_realization_multiplier"],
                            "is_unmarked_asymmetry": bool(unmarked_info),
                            "unmarked_asymmetry": unmarked_info,
                            "vinted_asking_price": comp["vinted_asking_price"],
                            "vinted_landed_cost": comp["vinted_landed_cost"],
                            "vinted_cost_breakdown": comp["vinted_cost_breakdown"],
                            "ebay_net_payout": comp["ebay_net_payout"],
                            "ebay_fees_breakdown": comp["ebay_fees_breakdown"],
                            "net_flip_profit": comp["net_flip_profit"],
                            "roi_pct": comp["roi_pct"],
                            "discount_pct": comp["discount_pct"],
                            "opportunity_index": ai_alpha["alpha_score"],
                            "ai_alpha": ai_alpha,
                            "casual_seller_info": casual_info,
                            "recommended_snipe_cap": comp["recommended_snipe_cap"],
                        })
            except Exception as exc:
                logger.debug("Live query '%s' skipped: %s", query_str, exc)

        # 3. Sort candidates by AI Alpha Score descending
        candidates.sort(key=lambda x: x.get("ai_alpha", {}).get("alpha_score", 0), reverse=True)

        # 4. Live Availability Shield: Concurrently verify top 35 candidates
        # Discards ANY item that is already sold, reserved, or 404 on Vinted
        top_candidates = candidates[:35]
        verified_active = ai_arbitrage.verify_active_items_parallel(top_candidates, max_workers=6, timeout=10.0)

        # Update SQLite if any database items turned out to be sold
        verified_ids = {c["id"] for c in verified_active}
        for cand in top_candidates:
            if cand["id"] not in verified_ids and cand.get("source") == "database_stream":
                db_item = db.query(SoldItem).filter(SoldItem.vinted_item_id == cand["id"]).first()
                if db_item and not db_item.is_sold:
                    db_item.is_sold = True
                    db_item.status = "sold"
                    logger.info("Marked stale database item #%s as sold in SQLite.", cand["id"])
        db.commit()

        logger.info(
            "AI Arbitrage scan complete: %d / %d candidates verified 100%% active & buyable.",
            len(verified_active),
            len(top_candidates),
        )
        return verified_active

    def add_pick_to_sniper(self, db: Session, pick: Dict[str, Any]) -> Dict[str, Any]:
        """Auto-configure Deal Sniper to track and snipe items matching this verified arbitrage pick."""
        search_text = pick.get("ebay_verified_model") or pick.get("title") or "Item"
        brand = pick.get("brand") or ""
        cap = float(pick.get("recommended_snipe_cap") or 35.0)

        existing = db.query(TrackedSearch).filter(TrackedSearch.search_text.ilike(search_text)).first()
        if existing:
            target_search = existing
        else:
            target_search = TrackedSearch(
                search_text=search_text,
                brand=brand,
                is_active=True,
                variants_enabled=True,
            )
            db.add(target_search)
            db.commit()
            db.refresh(target_search)

        from ..routers.sniper import deal_sniper

        with deal_sniper._lock:
            if target_search.id not in deal_sniper.selected_search_ids:
                deal_sniper.selected_search_ids.append(target_search.id)
            deal_sniper.item_max_prices[target_search.id] = cap

        return {
            "ok": True,
            "search_id": target_search.id,
            "search_text": target_search.search_text,
            "brand": target_search.brand,
            "max_price": cap,
            "message": f"Successfully added '{target_search.search_text}' to Deal Sniper with cap ≤ £{cap:.0f}!",
        }

    def target_predicted_product(self, db: Session, product_id: str) -> Dict[str, Any]:
        """1-Click launch an automated sniper targeting an AI-predicted high-alpha product blueprint."""
        blueprint = next((b for b in ai_arbitrage._predicted_blueprints if b["product_id"] == product_id), None)
        if not blueprint:
            raise ValueError(f"Unknown predicted product ID: {product_id}")

        search_text = blueprint["product_name"]
        brand = blueprint["brand"]
        cap = float(blueprint["target_buy_ceiling_gbp"])

        existing = db.query(TrackedSearch).filter(TrackedSearch.search_text.ilike(search_text)).first()
        if existing:
            target_search = existing
        else:
            target_search = TrackedSearch(
                search_text=search_text,
                brand=brand,
                is_active=True,
                variants_enabled=True,
            )
            db.add(target_search)
            db.commit()
            db.refresh(target_search)

        from ..routers.sniper import deal_sniper

        with deal_sniper._lock:
            if target_search.id not in deal_sniper.selected_search_ids:
                deal_sniper.selected_search_ids.append(target_search.id)
            deal_sniper.item_max_prices[target_search.id] = cap

        return {
            "ok": True,
            "search_id": target_search.id,
            "search_text": target_search.search_text,
            "brand": target_search.brand,
            "max_price": cap,
            "vague_exploits": blueprint.get("vague_search_exploits") or [],
            "message": f"🎯 Deal Sniper locked onto '{target_search.search_text}' with buy ceiling ≤ £{cap:.0f}!",
        }

    def simulate_pick_buy(self, pick: Dict[str, Any], db: Optional[Session] = None) -> Dict[str, Any]:
        """Execute a simulated paper trading buy order into the order ledger for this pick."""
        from ..database import SessionLocal
        from .sniper_executor import sniper_executor

        item_id = str(pick.get("id") or "sim-" + str(int(datetime.now().timestamp())))
        item_title = pick.get("title") or pick.get("ebay_verified_model") or "Arbitrage Pick"
        price = float(pick.get("price") or pick.get("vinted_asking_price") or 25.0)

        item_data = {
            "id": item_id,
            "title": item_title,
            "price": price,
            "url": pick.get("url") or f"{VINTED_BASE_URL}/items/{item_id}",
            "photo_url": pick.get("photo_url") or "",
            "search_name": pick.get("ebay_verified_model") or "Arbitrage Pick",
            "net_spread_gbp": pick.get("net_flip_profit") or 25.0,
            "projected_roi_pct": pick.get("roi_pct") or 80.0,
            "target_resale_gbp": pick.get("ebay_sold_median") or 65.0,
        }

        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        try:
            order = sniper_executor.execute_deal(
                db=db,
                deal=item_data,
                force_simulate=True,
            )
            return {
                "ok": True,
                "order": order,
                "message": f"Simulated paper flip executed for '{item_title[:40]}' at £{price:.2f}! Added to ledger.",
            }
        finally:
            if should_close:
                db.close()


# Singleton instance
arbitrage_radar = ArbitrageRadarService()
