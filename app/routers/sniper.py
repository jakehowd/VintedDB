"""Endpoints for Deal Sniper live monitoring."""

from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import SoldItem, TrackedSearch, User
from ..services.sniper import get_user_sniper

router = APIRouter(prefix="/api/sniper", tags=["sniper"])


class SniperStartRequest(BaseModel):
    search_ids: List[int] = Field(..., description="Target search IDs to continuously snipe")
    max_price: float = Field(50.0, description="Global default maximum price (£) threshold")
    item_max_prices: Optional[Dict[int, float]] = Field(None, description="Per-item maximum price (£) overrides")
    max_age_minutes: int = Field(15, description="Maximum listing age in minutes to qualify as a deal")
    poll_delay_seconds: float = Field(5.0, description="Polite pause between targets in seconds")
    ai_resale_gate_enabled: Optional[bool] = Field(False, description="Enforce AI Resale Profitability Gate")
    min_net_profit: Optional[float] = Field(0.0, description="Minimum projected net profit (£) threshold")
    min_roi_pct: Optional[float] = Field(0.0, description="Minimum projected ROI (%) threshold")


@router.get("/status")
def get_sniper_status(
    current_user: User = Depends(get_current_user),
):
    """Return live sniper state, current target IDs, sniped deals stream, and activity logs strictly scoped to the current user."""
    user_sniper = get_user_sniper(current_user.id)
    return user_sniper.get_status()


@router.get("/targets-info")
def get_sniper_targets_info(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return tracked items with sold item counts, average sold price, and recommended profitable buy price."""
    from .variants import serialize_search_variants

    searches = db.query(TrackedSearch).filter(
        TrackedSearch.is_active == True,
        TrackedSearch.user_id == current_user.id,
    ).all()
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
        models_list = variant_data.get("models", [])
        enabled_variants_count = sum(1 for v in variants_list if v.get("is_sniping_enabled"))
        enabled_models_count = sum(1 for m in models_list if m.get("is_sniping_enabled"))

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
            "dimensions": variant_data.get("dimensions", []),
            "models": models_list,
            "models_count": len(models_list),
            "enabled_models_count": enabled_models_count,
            "variants": variants_list,
            "variants_count": len(variants_list),
            "enabled_variants_count": enabled_variants_count,
        })
    return results


@router.post("/start")
def start_sniper(
    data: SniperStartRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start high-frequency live sniper on selected searches with per-item price thresholds."""
    if not data.search_ids:
        raise HTTPException(status_code=400, detail="Please select at least one item to snipe.")

    user_ids = {
        s_id for (s_id,) in db.query(TrackedSearch.id).filter(
            TrackedSearch.user_id == current_user.id,
            TrackedSearch.id.in_(data.search_ids),
        ).all()
    }
    valid_ids = [s_id for s_id in data.search_ids if s_id in user_ids]
    if not valid_ids:
        raise HTTPException(status_code=403, detail="None of the selected items belong to your account.")

    # Filter item_max_prices to only valid search IDs
    scoped_item_prices = None
    if data.item_max_prices:
        scoped_item_prices = {
            s_id: price for s_id, price in data.item_max_prices.items()
            if s_id in user_ids
        }

    user_sniper = get_user_sniper(current_user.id)
    status = user_sniper.start(
        search_ids=valid_ids,
        max_price=data.max_price,
        max_age_minutes=data.max_age_minutes,
        poll_delay_seconds=data.poll_delay_seconds,
        item_max_prices=scoped_item_prices,
        ai_resale_gate_enabled=data.ai_resale_gate_enabled,
        min_net_profit=data.min_net_profit,
        min_roi_pct=data.min_roi_pct,
    )
    return status


@router.post("/stop")
def stop_sniper(current_user: User = Depends(get_current_user)):
    """Stop active live sniper for the current user."""
    return get_user_sniper(current_user.id).stop()


@router.post("/clear")
def clear_sniper_deals(current_user: User = Depends(get_current_user)):
    """Clear sniped deals buffer for the current user."""
    get_user_sniper(current_user.id).clear_deals()
    return {"ok": True}


@router.post("/deals/{deal_id}/dismiss")
def dismiss_sniper_deal(deal_id: str, current_user: User = Depends(get_current_user)):
    """Dismiss a single deal from the current user's sniper stream and prevent it from reappearing."""
    removed = get_user_sniper(current_user.id).dismiss_deal(deal_id)
    return {"ok": True, "deal_id": deal_id, "dismissed": removed}


