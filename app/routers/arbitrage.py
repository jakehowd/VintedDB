"""Market Inefficiency & Cross-Platform Arbitrage Radar API Router."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.arbitrage_radar import arbitrage_radar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/arbitrage", tags=["arbitrage"])


class RefreshMarketScanRequest(BaseModel):
    sector: str = "all"
    min_profit_gbp: float = 15.0
    min_roi_pct: float = 35.0


class SnipePickRequest(BaseModel):
    title: str
    brand: Optional[str] = None
    ebay_verified_model: Optional[str] = None
    recommended_snipe_cap: Optional[float] = None
    vinted_item_id: Optional[str] = None


class TargetProductRequest(BaseModel):
    product_id: str


class SimBuyPickRequest(BaseModel):
    id: Optional[str] = None
    title: str
    price: float
    url: Optional[str] = None
    photo_url: Optional[str] = None
    ebay_verified_model: Optional[str] = None
    net_flip_profit: Optional[float] = None
    roi_pct: Optional[float] = None
    ebay_sold_median: Optional[float] = None


@router.get("/predicted-products")
def get_predicted_products(
    sector: str = Query("all", description="Sector filter (all, outerwear, streetwear, knitwear, footwear, sportswear)"),
):
    """Retrieve AI-calculated high-alpha product targets and blueprints to snipe."""
    from ..services.ai_arbitrage import ai_arbitrage
    return {
        "status": "success",
        "sector": sector,
        "predicted_products": ai_arbitrage.get_predicted_product_targets(sector=sector),
    }


@router.post("/target-product")
def target_predicted_product(
    payload: TargetProductRequest,
    db: Session = Depends(get_db),
):
    """1-Click launch automated Deal Sniper targeting an AI-predicted high-alpha product blueprint."""
    try:
        return arbitrage_radar.target_predicted_product(
            db=db,
            product_id=payload.product_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Modern Cross-Market Arbitrage Endpoints ───────────────────────────────────

@router.get("/top-picks")
def get_todays_top_picks(
    sector: str = Query("all", description="Category filter (all, outerwear, streetwear, knitwear, footwear, sportswear)"),
    force_refresh: bool = Query(False, description="Whether to execute a fresh autonomous market scan"),
    min_profit: float = Query(15.0, description="Minimum net profit in GBP"),
    min_roi: float = Query(35.0, description="Minimum ROI percentage"),
    db: Session = Depends(get_db),
):
    """Retrieve Today's Top Verified Cross-Market Arbitrage Picks with eBay UK sold comps."""
    return arbitrage_radar.get_todays_top_picks(
        db=db,
        sector=sector,
        force_refresh=force_refresh,
        min_profit_gbp=min_profit,
        min_roi_pct=min_roi,
    )


@router.post("/refresh")
def refresh_arbitrage_market(
    payload: RefreshMarketScanRequest,
    db: Session = Depends(get_db),
):
    """Trigger an autonomous live scan across UK resale sectors and re-verify against eBay UK sold data."""
    return arbitrage_radar.get_todays_top_picks(
        db=db,
        sector=payload.sector,
        force_refresh=True,
        min_profit_gbp=payload.min_profit_gbp,
        min_roi_pct=payload.min_roi_pct,
    )


@router.post("/snipe-pick")
def snipe_arbitrage_pick(
    payload: SnipePickRequest,
    db: Session = Depends(get_db),
):
    """1-Click configure Deal Sniper for this verified arbitrage pick with safe buy cap."""
    return arbitrage_radar.add_pick_to_sniper(
        db=db,
        pick=payload.model_dump(),
    )


@router.post("/sim-buy")
def simulate_arbitrage_paper_buy(
    payload: SimBuyPickRequest,
    db: Session = Depends(get_db),
):
    """1-Click execute a paper trading simulation purchase to test flipping P&L in the ledger."""
    return arbitrage_radar.simulate_pick_buy(
        pick=payload.model_dump(),
        db=db,
    )


# ── Backward-Compatibility Endpoints (Redirect to new engine) ────────────────

@router.get("/radar")
def get_arbitrage_radar_legacy(
    sector: str = "all",
    force_refresh: bool = False,
    min_spread: float = 15.0,
    min_roi: float = 35.0,
    db: Session = Depends(get_db),
):
    """Legacy endpoint redirecting to Today's Top Picks engine."""
    data = arbitrage_radar.get_todays_top_picks(
        db=db,
        sector=sector,
        force_refresh=force_refresh,
        min_profit_gbp=min_spread,
        min_roi_pct=min_roi,
    )
    return {
        "total_opportunities": data["market_pulse"]["total_picks"],
        "opportunities": data["picks"],
        "scanned_at": data["scanned_at"],
        "market_pulse": data["market_pulse"],
    }


@router.post("/scan")
def trigger_arbitrage_scan_legacy(
    db: Session = Depends(get_db),
):
    """Legacy trigger scan endpoint."""
    data = arbitrage_radar.get_todays_top_picks(
        db=db,
        sector="all",
        force_refresh=True,
    )
    return {
        "total_opportunities": data["market_pulse"]["total_picks"],
        "opportunities": data["picks"],
        "scanned_at": data["scanned_at"],
        "market_pulse": data["market_pulse"],
    }


@router.post("/add-to-sniper")
def add_arbitrage_to_sniper_legacy(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    """Legacy add-to-sniper endpoint."""
    return arbitrage_radar.add_pick_to_sniper(db=db, pick=payload)
