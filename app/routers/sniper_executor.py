"""Sniper Auto-Checkout & Paper Trading Simulation API Router."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import TrackedSearch
from ..routers.sniper import deal_sniper
from ..services.resale_evaluator import resale_evaluator
from ..services.sniper_executor import sniper_executor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/autobuy", tags=["autobuy"])


class UpdateCheckoutProfileRequest(BaseModel):
    auto_buy_enabled: Optional[bool] = None
    simulation_mode: Optional[bool] = None
    shipping_name: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    postcode: Optional[str] = None
    phone: Optional[str] = None
    shipping_method: Optional[str] = None
    cardholder_name: Optional[str] = None
    card_number: Optional[str] = None
    card_expiry: Optional[str] = None
    card_cvv: Optional[str] = None
    max_price_per_item: Optional[float] = None
    daily_spend_limit: Optional[float] = None
    min_ai_resale_score: Optional[int] = None
    min_net_profit: Optional[float] = None


class SimulateBuyRequest(BaseModel):
    deal_id: str
    force_simulate: bool = True


class EvaluateDealRequest(BaseModel):
    search_id: Optional[int] = None
    title: str
    price: float
    condition: Optional[str] = None
    size: Optional[str] = None
    seller_username: Optional[str] = None
    variant_id: Optional[int] = None


@router.get("/profile")
def get_checkout_profile():
    """Retrieve execution preferences and masked card/shipping profile."""
    return sniper_executor.get_public_profile()


@router.post("/profile")
def update_checkout_profile(payload: UpdateCheckoutProfileRequest):
    """Update auto-checkout profile, address, payment card, or simulation test mode."""
    data = payload.dict(exclude_unset=True)
    return sniper_executor.update_profile(data)


@router.get("/orders")
def get_order_ledger(limit: int = 50, db: Session = Depends(get_db)):
    """Retrieve historical simulated and live auto-buy orders with aggregate profit metrics."""
    return sniper_executor.get_order_ledger(db=db, limit=limit)


@router.post("/orders/clear")
def clear_order_ledger(db: Session = Depends(get_db)):
    """Clear historical order ledger."""
    count = sniper_executor.clear_ledger(db=db)
    return {"ok": True, "cleared_count": count}


@router.post("/simulate/{deal_id}")
def simulate_buy_deal(deal_id: str, db: Session = Depends(get_db)):
    """Simulate an immediate auto-buy on a specific deal in the sniper stream."""
    deal = next((d for d in deal_sniper.sniped_deals if str(d.get("id")) == str(deal_id)), None)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found in active sniped stream.")

    search = db.query(TrackedSearch).filter(TrackedSearch.id == deal.get("search_id")).first()
    eval_result = None
    if search:
        eval_result = resale_evaluator.evaluate_deal(db, search, deal)

    result = sniper_executor.execute_deal(
        db=db,
        deal=deal,
        evaluation=eval_result,
        force_simulate=True,
    )

    # Log to deal sniper terminal
    deal_sniper._add_log(
        "AUTO-BUY",
        f"🧪 SIMULATED AUTO-BUY: Acquired '{deal.get('title')}' for £{deal.get('price'):.2f} "
        f"(Total landed: £{result['total_landed_cost']:.2f}). Projected profit: +£{result['projected_net_profit']:.2f} "
        f"({result['projected_roi_pct']:.0f}% ROI). [Order #{result['order_id']}]",
    )

    return {"ok": True, "order": result}


@router.post("/evaluate-deal")
def evaluate_deal_endpoint(payload: EvaluateDealRequest, db: Session = Depends(get_db)):
    """Run on-demand AI Resale Profitability & Risk analysis for any item."""
    search = None
    if payload.search_id:
        search = db.query(TrackedSearch).filter(TrackedSearch.id == payload.search_id).first()
    if not search:
        search = db.query(TrackedSearch).first()
    if not search:
        raise HTTPException(status_code=400, detail="No tracked searches found.")

    deal_dict = {
        "id": "eval_test",
        "title": payload.title,
        "price": payload.price,
        "condition": payload.condition or "",
        "size": payload.size or "",
        "seller_username": payload.seller_username or "",
        "variant_id": payload.variant_id,
    }

    return resale_evaluator.evaluate_deal(db=db, tracked_search=search, deal=deal_dict)

