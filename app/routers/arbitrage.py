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


class AnalyzeMarketRequest(BaseModel):
    keyword: str
    brand: Optional[str] = None
    fetch_live: bool = True


class ActivateStrategyRequest(BaseModel):
    search_text: str
    brand: Optional[str] = None
    category_id: Optional[int] = None
    mode: str = "broad"  # "broad" or "model"
    target_model_names: Optional[list[str]] = None
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


@router.post("/analyze-market")
def analyze_market_arbitrage(
    payload: AnalyzeMarketRequest,
    db: Session = Depends(get_db),
):
    """Deeply inspect a broad market, separate into models, and evaluate model characteristics."""
    kw = payload.keyword.strip()
    if not kw:
        raise HTTPException(status_code=400, detail="Keyword cannot be empty.")
    return arbitrage_radar.analyze_broad_market(
        db=db,
        keyword=kw,
        brand=payload.brand,
        fetch_live=payload.fetch_live,
    )


@router.post("/activate-strategy")
def activate_arbitrage_strategy(
    payload: ActivateStrategyRequest,
    db: Session = Depends(get_db),
):
    """Activate an arbitrage strategy (broad or model-specific) into Tracked Searches and Deal Sniper."""
    q_str = payload.search_text.strip()
    if not q_str:
        raise HTTPException(status_code=400, detail="Search text cannot be empty.")

    from ..models import ProductVariant, TrackedSearch
    from ..routers.sniper import deal_sniper
    from ..services.llm_router import llm_router

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

    # Ensure ProductVariants exist for this search
    variants = db.query(ProductVariant).filter(ProductVariant.tracked_search_id == target_search.id).all()
    if not variants:
        # Generate variants using LLM Router catalogue
        catalogue = llm_router.deep_research_product_variants(search_text=q_str, brand=payload.brand)
        var_defs = catalogue.get("variants") or []
        created_vars = []
        name_to_var = {}

        # 1. Create top-level parent models first
        for v in var_defs:
            if not v.get("parent_variant_name"):
                pv = ProductVariant(
                    tracked_search_id=target_search.id,
                    variant_name=v["variant_name"],
                    parent_variant_id=None,
                    modality=v.get("modality") or "visual",
                    resale_tier=v.get("resale_tier") or "STANDARD",
                    short_description=v.get("short_description") or "",
                    text_keywords=v.get("text_keywords") or [],
                    ocr_signatures=v.get("ocr_signatures") or [],
                    negative_keywords=v.get("negative_keywords") or [],
                    target_max_price=v.get("target_max_price"),
                    is_sniping_enabled=True,
                )
                db.add(pv)
                db.flush()
                name_to_var[v["variant_name"].lower()] = pv
                created_vars.append(pv)

        # 2. Create sub-styles
        for v in var_defs:
            if v.get("parent_variant_name"):
                parent_name = v["parent_variant_name"].lower()
                parent_obj = name_to_var.get(parent_name)
                pv = ProductVariant(
                    tracked_search_id=target_search.id,
                    variant_name=v["variant_name"],
                    parent_variant_id=parent_obj.id if parent_obj else None,
                    modality=v.get("modality") or "visual",
                    resale_tier=v.get("resale_tier") or "STANDARD",
                    short_description=v.get("short_description") or "",
                    text_keywords=v.get("text_keywords") or [],
                    ocr_signatures=v.get("ocr_signatures") or [],
                    negative_keywords=v.get("negative_keywords") or [],
                    target_max_price=v.get("target_max_price"),
                    is_sniping_enabled=True,
                )
                db.add(pv)
                created_vars.append(pv)

        db.commit()
        variants = db.query(ProductVariant).filter(ProductVariant.tracked_search_id == target_search.id).all()

    # Apply Strategy (Broad vs Selective Models)
    target_names_set = {n.lower() for n in (payload.target_model_names or [])}

    if payload.mode == "model" and target_names_set:
        for v in variants:
            v_name_lower = v.variant_name.lower()
            # If this is a targeted model or style
            is_match = any(tn in v_name_lower or v_name_lower in tn for tn in target_names_set)
            v.is_sniping_enabled = is_match
            if is_match and payload.recommended_max_price:
                v.target_max_price = float(payload.recommended_max_price)
        db.commit()
    else:
        for v in variants:
            v.is_sniping_enabled = True
        db.commit()

    # Lock Deal Sniper onto target
    with deal_sniper._lock:
        if target_search.id not in deal_sniper.selected_search_ids:
            deal_sniper.selected_search_ids.append(target_search.id)
        if payload.recommended_max_price and payload.recommended_max_price > 0:
            deal_sniper.item_max_prices[target_search.id] = float(payload.recommended_max_price)

    return {
        "ok": True,
        "search_id": target_search.id,
        "search_text": target_search.search_text,
        "mode": payload.mode,
        "target_models": list(target_names_set) if payload.mode == "model" else "ALL_MODELS",
        "max_price": deal_sniper.item_max_prices.get(target_search.id, deal_sniper.max_price),
        "message": f"Strategy successfully locked onto '{target_search.search_text}' in Deal Sniper!",
    }

