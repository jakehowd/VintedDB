"""Endpoints for Deal Sniper live monitoring."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services.sniper import deal_sniper

router = APIRouter(prefix="/api/sniper", tags=["sniper"])


class SniperStartRequest(BaseModel):
    search_ids: List[int] = Field(..., description="Target search IDs to continuously snipe")
    max_price: float = Field(50.0, description="Global default maximum price (£) threshold")
    item_max_prices: Optional[Dict[int, float]] = Field(None, description="Per-item maximum price (£) overrides")
    max_age_minutes: int = Field(15, description="Maximum listing age in minutes to qualify as a deal")
    poll_delay_seconds: float = Field(5.0, description="Polite pause between targets in seconds")


class SniperScheduleRequest(BaseModel):
    interval_minutes: Optional[int] = None
    constant_scrape: Optional[bool] = None


@router.get("/status")
def get_sniper_status():
    """Return live sniper state, current target IDs, sniped deals stream, and activity logs."""
    return deal_sniper.get_status()


@router.get("/targets-info")
def get_sniper_targets_info():
    """Return tracked items with sold item counts, average sold price, and recommended profitable buy price."""
    from ..database import get_db
    from ..models import TrackedSearch, SoldItem
    from .variants import serialize_search_variants
    from sqlalchemy import func

    db_gen = get_db()
    db = next(db_gen)
    try:
        searches = db.query(TrackedSearch).filter(TrackedSearch.is_active == True).all()
        results = []
        for s in searches:
            sold_stats = (
                db.query(
                    func.count(SoldItem.id).label("count"),
                    func.avg(SoldItem.price).label("avg_price"),
                    func.min(SoldItem.price).label("min_price"),
                    func.max(SoldItem.price).label("max_price"),
                )
                .filter(
                    SoldItem.tracked_search_id == s.id,
                    SoldItem.is_sold == True,
                    SoldItem.is_replica.isnot(True),
                )
                .first()
            )

            sold_count = sold_stats.count or 0
            if sold_count > 0:
                avg_sold_price = float(sold_stats.avg_price or 0.0)
                min_sold_price = float(sold_stats.min_price or 0.0)
                max_sold_price = float(sold_stats.max_price or 0.0)
            else:
                clean_stats = (
                    db.query(
                        func.count(SoldItem.id).label("count"),
                        func.avg(SoldItem.price).label("avg_price"),
                        func.min(SoldItem.price).label("min_price"),
                        func.max(SoldItem.price).label("max_price"),
                    )
                    .filter(
                        SoldItem.tracked_search_id == s.id,
                        SoldItem.is_replica.isnot(True),
                    )
                    .first()
                )
                avg_sold_price = float(clean_stats.avg_price or 0.0) if clean_stats else 0.0
                min_sold_price = float(clean_stats.min_price or 0.0) if clean_stats else 0.0
                max_sold_price = float(clean_stats.max_price or 0.0) if clean_stats else 0.0

            # Recommended Profitable Price:
            # 35% margin under true average sold price
            if avg_sold_price > 0:
                rec_price = max(5.0, round(avg_sold_price * 0.65, 0))
            else:
                rec_price = 40.0

            variant_data = serialize_search_variants(db, s)
            variants_list = variant_data.get("variants", [])
            enabled_variants_count = sum(1 for v in variants_list if v.get("is_sniping_enabled"))

            results.append({
                "id": s.id,
                "search_text": s.search_text,
                "brand": s.brand or "",
                "total_sold_count": sold_count,
                "avg_sold_price": round(avg_sold_price, 2),
                "min_sold_price": round(min_sold_price, 2),
                "max_sold_price": round(max_sold_price, 2),
                "recommended_profitable_price": rec_price,
                "target_margin_pct": 35.0,
                "variants_enabled": bool(getattr(s, "variants_enabled", False)),
                "variants": variants_list,
                "variants_count": len(variants_list),
                "enabled_variants_count": enabled_variants_count,
            })
        return results
    finally:
        db.close()


@router.post("/start")
def start_sniper(data: SniperStartRequest):
    """Start high-frequency live sniper on selected searches with per-item price thresholds."""
    if not data.search_ids:
        raise HTTPException(status_code=400, detail="Please select at least one item to snipe.")

    status = deal_sniper.start(
        search_ids=data.search_ids,
        max_price=data.max_price,
        max_age_minutes=data.max_age_minutes,
        poll_delay_seconds=data.poll_delay_seconds,
        item_max_prices=data.item_max_prices,
    )
    return status


@router.post("/stop")
def stop_sniper():
    """Stop active live sniper."""
    return deal_sniper.stop()


@router.post("/clear")
def clear_sniper_deals():
    """Clear sniped deals buffer."""
    deal_sniper.clear_deals()
    return {"ok": True}


@router.post("/deals/{deal_id}/dismiss")
def dismiss_sniper_deal(deal_id: str):
    """Dismiss a single deal from the sniper stream and prevent it from reappearing."""
    removed = deal_sniper.dismiss_deal(deal_id)
    return {"ok": True, "deal_id": deal_id, "dismissed": removed}


