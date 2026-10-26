"""Market Inefficiency & Arbitrage Radar API Router."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import TrackedSearch
from ..services.arbitrage_radar import arbitrage_radar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/arbitrage", tags=["arbitrage"])


class ArbitrageScanRequest(BaseModel):
    include_live_scan: bool = True
    min_spread_gbp: float = 15.0
    min_roi_pct: float = 40.0


class AddArbitrageToSniperRequest(BaseModel):
    search_text: str
    brand: Optional[str] = None
    category_id: Optional[int] = None
    recommended_max_price: Optional[float] = None


@router.get("/radar")
def get_arbitrage_radar(
    include_live_scan: bool = False,
    min_spread: float = 15.0,
    min_roi: float = 40.0,
    db: Session = Depends(get_db),
):
    """Retrieve ranked market inefficiency & fast-flipping arbitrage opportunities."""
    return arbitrage_radar.get_radar_opportunities(
        db=db,
        include_live_scan=include_live_scan,
        min_spread_gbp=min_spread,
        min_roi_pct=min_roi,
    )


@router.post("/scan")
def trigger_arbitrage_scan(
    payload: ArbitrageScanRequest,
    db: Session = Depends(get_db),
):
    """Trigger a fresh live Vinted market discovery scan for high-spread, fast-turnover items."""
    return arbitrage_radar.get_radar_opportunities(
        db=db,
        include_live_scan=True,
        min_spread_gbp=payload.min_spread_gbp,
        min_roi_pct=payload.min_roi_pct,
    )


@router.post("/add-to-sniper")
def add_arbitrage_to_sniper(
    payload: AddArbitrageToSniperRequest,
    db: Session = Depends(get_db),
):
    """Add a discovered arbitrage item into Tracked Searches and auto-configure Deal Sniper for it."""
    q_str = payload.search_text.strip()
    if not q_str:
        raise HTTPException(status_code=400, detail="Search text cannot be empty.")

    existing = db.query(TrackedSearch).filter(TrackedSearch.search_text.ilike(q_str)).first()
    if existing:
        target_search = existing
    else:
        target_search = TrackedSearch(
            search_text=q_str,
            brand=payload.brand,
            category_id=payload.category_id,
            is_active=True,
            variants_enabled=True,
        )
        db.add(target_search)
        db.commit()
        db.refresh(target_search)

    # Configure Deal Sniper for this target
    from ..routers.sniper import deal_sniper

    with deal_sniper._lock:
        if target_search.id not in deal_sniper.selected_search_ids:
            deal_sniper.selected_search_ids.append(target_search.id)
        if payload.recommended_max_price and payload.recommended_max_price > 0:
            deal_sniper.item_max_prices[target_search.id] = float(payload.recommended_max_price)

    return {
        "ok": True,
        "search_id": target_search.id,
        "search_text": target_search.search_text,
        "brand": target_search.brand,
        "max_price": deal_sniper.item_max_prices.get(target_search.id, deal_sniper.max_price),
        "message": f"Successfully added '{target_search.search_text}' to Tracked Searches and Deal Sniper!",
    }

