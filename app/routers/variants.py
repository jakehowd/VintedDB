"""Endpoints for AI Variant Deep Research, Local Vision Training, and Variant Management."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ProductVariant, SoldItem, TrackedSearch
from ..services.llm_router import llm_router
from ..services.variant_vision import variant_vision_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["variants"])


# ── Pydantic Payloads ───────────────────────────────────────────────────────

class AIConfigUpdateRequest(BaseModel):
    routing_strategy: Optional[str] = None
    gemini_api_keys: Optional[Any] = None
    gemini_model: Optional[str] = None
    enable_google_search_grounding: Optional[bool] = None
    groq_api_keys: Optional[Any] = None
    groq_model: Optional[str] = None
    openrouter_api_keys: Optional[Any] = None
    openrouter_model: Optional[str] = None
    mistral_api_keys: Optional[Any] = None
    mistral_model: Optional[str] = None
    custom_openai_base_url: Optional[str] = None
    custom_openai_api_key: Optional[str] = None
    custom_openai_model: Optional[str] = None
    ollama_base_url: Optional[str] = None


class ResearchVariantsRequest(BaseModel):
    fetch_live_samples: bool = True
    classify_existing: bool = True
    replace_existing: bool = False


class ClassifyExistingRequest(BaseModel):
    only_unclassified: bool = False
    max_vision_items: int = Field(80, ge=0, le=500)


class VariantCreateUpdateRequest(BaseModel):
    variant_name: Optional[str] = None
    short_description: Optional[str] = None
    resale_tier: Optional[str] = None
    est_market_value_gbp: Optional[float] = None
    visual_cues: Optional[List[str]] = None
    ocr_signatures: Optional[List[str]] = None
    text_keywords: Optional[List[str]] = None
    negative_keywords: Optional[List[str]] = None
    target_max_price: Optional[float] = None
    clear_target_max_price: Optional[bool] = False
    is_sniping_enabled: Optional[bool] = None


class BulkVariantSnipeSelectRequest(BaseModel):
    mode: str = Field(..., description="'all', 'none', or 'only'")
    variant_ids: Optional[List[int]] = None


class AssignItemVariantRequest(BaseModel):
    variant_id: Optional[int] = None


# ── Helper: Enrich Variant Serialization with Market Metrics ─────────────────

def serialize_search_variants(db: Session, search: TrackedSearch) -> Dict[str, Any]:
    variants = (
        db.query(ProductVariant)
        .filter(ProductVariant.tracked_search_id == search.id)
        .order_by(ProductVariant.id)
        .all()
    )

    clean_filter = [
        SoldItem.tracked_search_id == search.id,
        SoldItem.is_replica.isnot(True),
    ]
    if getattr(search, "exclude_child_sizes", False):
        clean_filter.append(SoldItem.is_child_size.isnot(True))

    total_clean = db.query(func.count(SoldItem.id)).filter(*clean_filter).scalar() or 0
    unclassified_count = (
        db.query(func.count(SoldItem.id))
        .filter(*clean_filter, SoldItem.variant_id.is_(None))
        .scalar()
        or 0
    )

    out_variants: List[Dict[str, Any]] = []
    for v in variants:
        v_all = (
            db.query(func.count(SoldItem.id), func.avg(SoldItem.price))
            .filter(*clean_filter, SoldItem.variant_id == v.id)
            .first()
        )
        v_sold = (
            db.query(func.count(SoldItem.id), func.avg(SoldItem.price))
            .filter(*clean_filter, SoldItem.variant_id == v.id, SoldItem.is_sold == True)
            .first()
        )
        v_active = (
            db.query(func.count(SoldItem.id), func.avg(SoldItem.price))
            .filter(*clean_filter, SoldItem.variant_id == v.id, SoldItem.is_sold == False)
            .first()
        )

        item_cnt = int(v_all[0] or 0) if v_all else 0
        all_avg = float(v_all[1] or 0.0) if v_all else 0.0
        sold_cnt = int(v_sold[0] or 0) if v_sold else 0
        sold_avg = float(v_sold[1] or 0.0) if v_sold else 0.0
        active_cnt = int(v_active[0] or 0) if v_active else 0
        active_avg = float(v_active[1] or 0.0) if v_active else 0.0

        effective_market = (
            sold_avg
            if sold_cnt > 0
            else (all_avg if item_cnt > 0 else float(v.est_market_value_gbp or 0.0))
        )
        rec_snipe = max(5.0, round(effective_market * 0.65, 0)) if effective_market > 0 else 35.0

        out_variants.append({
            "id": v.id,
            "tracked_search_id": v.tracked_search_id,
            "variant_slug": v.variant_slug,
            "variant_name": v.variant_name,
            "short_description": v.short_description or "",
            "resale_tier": v.resale_tier or "STANDARD",
            "est_market_value_gbp": round(float(v.est_market_value_gbp or 0.0), 2) if v.est_market_value_gbp else None,
            "effective_market_value_gbp": round(effective_market, 2),
            "recommended_snipe_price": rec_snipe,
            "target_max_price": round(float(v.target_max_price), 2) if v.target_max_price is not None else None,
            "is_sniping_enabled": bool(v.is_sniping_enabled),
            "training_sample_count": int(v.training_sample_count or 0),
            "has_trained_prototype": bool(v.prototype_embedding),
            "visual_cues": v.parsed_list("visual_cues"),
            "ocr_signatures": v.parsed_list("ocr_signatures"),
            "text_keywords": v.parsed_list("text_keywords"),
            "negative_keywords": v.parsed_list("negative_keywords"),
            "total_items": item_cnt,
            "sold_count": sold_cnt,
            "active_count": active_cnt,
            "sold_avg_price": round(sold_avg, 2),
            "active_avg_price": round(active_avg, 2),
        })

    catalogue_meta = {}
    if search.catalogue_summary:
        try:
            catalogue_meta = json.loads(search.catalogue_summary)
        except Exception:
            catalogue_meta = {}

    return {
        "search_id": search.id,
        "search_text": search.search_text,
        "variants_enabled": bool(getattr(search, "variants_enabled", False)),
        "catalogue_summary": catalogue_meta,
        "total_clean_items": total_clean,
        "unclassified_count": unclassified_count,
        "classified_count": max(0, total_clean - unclassified_count),
        "variants": out_variants,
    }


def run_research_and_train_for_search(
    db: Session,
    search: TrackedSearch,
    fetch_live_samples: bool = True,
    classify_existing: bool = True,
    replace_existing: bool = False,
) -> Dict[str, Any]:
    """Execute end-to-end Deep Research -> Catalogue Upsert -> Local Vision Training -> Backfill Classification."""
    # 1. Gather sample listings (DB first, then optional live Vinted page 1)
    db_samples = (
        db.query(SoldItem)
        .filter(SoldItem.tracked_search_id == search.id, SoldItem.is_replica.isnot(True))
        .order_by(SoldItem.captured_at.desc())
        .limit(45)
        .all()
    )
    sample_dicts: List[Dict[str, Any]] = [
        {"title": it.title, "price": it.price, "size": it.size, "photo_url": it.photo_url}
        for it in db_samples
    ]

    if len(sample_dicts) < 12 and fetch_live_samples:
        try:
            from ..scraper import _extract_item, _search_vinted
            raw_items = _search_vinted(search.search_text, page=1, per_page=36)
            for raw in (raw_items or [])[:36]:
                ext = _extract_item(raw)
                if ext and ext.get("title"):
                    sample_dicts.append(ext)
        except Exception as exc:
            logger.debug("Live Vinted sample fetch during research skipped: %s", exc)

    # 2. Run Cloud LLM Router (or Specialist + Corpus Fallback)
    catalogue = llm_router.deep_research_product_variants(
        search_text=search.search_text,
        brand=search.brand,
        sample_listings=sample_dicts,
    )

    if replace_existing:
        db.query(SoldItem).filter(SoldItem.tracked_search_id == search.id).update(
            {
                SoldItem.variant_id: None,
                SoldItem.variant_name: None,
                SoldItem.variant_confidence: 0.0,
                SoldItem.variant_method: None,
                SoldItem.variant_evidence: None,
            },
            synchronize_session=False,
        )
        db.query(ProductVariant).filter(ProductVariant.tracked_search_id == search.id).delete(synchronize_session=False)
        db.flush()

    existing_by_slug = {
        v.variant_slug: v
        for v in db.query(ProductVariant).filter(ProductVariant.tracked_search_id == search.id).all()
    }

    for v_spec in catalogue.get("variants", []):
        slug = v_spec["variant_slug"]
        if slug in existing_by_slug:
            v_obj = existing_by_slug[slug]
            v_obj.variant_name = v_spec["variant_name"]
            v_obj.short_description = v_spec.get("short_description")
            v_obj.resale_tier = v_spec.get("resale_tier", "STANDARD")
            v_obj.est_market_value_gbp = v_spec.get("est_market_value_gbp")
            v_obj.visual_cues = json.dumps(v_spec.get("visual_cues", []))
            v_obj.ocr_signatures = json.dumps(v_spec.get("ocr_signatures", []))
            v_obj.text_keywords = json.dumps(v_spec.get("text_keywords", []))
            v_obj.negative_keywords = json.dumps(v_spec.get("negative_keywords", []))
            v_obj.updated_at = datetime.now(timezone.utc)
        else:
            est_mkt = v_spec.get("est_market_value_gbp")
            default_snipe_max = max(5.0, round(float(est_mkt) * 0.65, 0)) if est_mkt else None
            v_obj = ProductVariant(
                tracked_search_id=search.id,
                variant_slug=slug,
                variant_name=v_spec["variant_name"],
                short_description=v_spec.get("short_description"),
                resale_tier=v_spec.get("resale_tier", "STANDARD"),
                est_market_value_gbp=est_mkt,
                visual_cues=json.dumps(v_spec.get("visual_cues", [])),
                ocr_signatures=json.dumps(v_spec.get("ocr_signatures", [])),
                text_keywords=json.dumps(v_spec.get("text_keywords", [])),
                negative_keywords=json.dumps(v_spec.get("negative_keywords", [])),
                target_max_price=default_snipe_max,
                is_sniping_enabled=True,
            )
            db.add(v_obj)

    search.variants_enabled = True
    search.catalogue_summary = json.dumps({
        "product_family": catalogue.get("product_family", search.search_text),
        "domain": catalogue.get("domain", "other"),
        "research_notes": catalogue.get("research_notes", ""),
        "provider_used": catalogue.get("provider_used", "Local Corpus"),
        "researched_at": catalogue.get("researched_at"),
    })
    db.commit()
    db.refresh(search)

    # 3. Train Local CLIP + Neural OCR Prototypes
    train_report = variant_vision_service.train_search_variants(
        db=db,
        tracked_search=search,
        fetch_live_samples=fetch_live_samples,
    )

    # 4. Classify existing DB items
    classified_count = 0
    if classify_existing:
        classified_count = _batch_classify_stored_items(
            db=db,
            search=search,
            only_unclassified=False,
            max_vision_items=60,
        )

    out = serialize_search_variants(db, search)
    out["training_report"] = train_report
    out["classified_items_updated"] = classified_count
    return out


def _batch_classify_stored_items(
    db: Session,
    search: TrackedSearch,
    only_unclassified: bool = False,
    max_vision_items: int = 80,
) -> int:
    variants = (
        db.query(ProductVariant)
        .filter(ProductVariant.tracked_search_id == search.id)
        .order_by(ProductVariant.id)
        .all()
    )
    if not variants:
        return 0

    q = db.query(SoldItem).filter(SoldItem.tracked_search_id == search.id)
    if only_unclassified:
        q = q.filter(SoldItem.variant_id.is_(None))
    items = q.order_by(SoldItem.captured_at.desc()).all()

    updated = 0
    vision_used = 0

    for item in items:
        if item.variant_method == "manual" and item.variant_id is not None:
            continue

        desc = ""
        if item.raw_data:
            try:
                rd = json.loads(item.raw_data)
                desc = str(rd.get("description") or "")
            except Exception:
                pass

        # Use full image vision+OCR when text title alone is ambiguous (up to max_vision_items per batch)
        text_scores = [
            variant_vision_service._score_text_only_strict(item.title or "", desc, v)
            for v in variants
        ]
        sorted_ts = sorted(text_scores, reverse=True)
        needs_vision = bool(
            item.photo_url
            and (sorted_ts[0] < 2.5 or (len(sorted_ts) > 1 and sorted_ts[0] - sorted_ts[1] < 1.5))
        )
        use_vision = needs_vision and (vision_used < max_vision_items)
        if use_vision:
            vision_used += 1

        res = variant_vision_service.classify_item_variant(
            db=db,
            tracked_search=search,
            item_data={
                "title": item.title,
                "description": desc,
                "photo_url": item.photo_url,
            },
            variants=variants,
            allow_vision=use_vision,
        )
        if res:
            item.variant_id = res["variant_id"]
            item.variant_name = res["variant_name"]
            item.variant_confidence = res["confidence"]
            item.variant_method = res["method"]
            item.variant_evidence = res["evidence"]
            updated += 1

    if updated:
        db.commit()
    return updated


# ── AI Configuration & Engine Health Endpoints ──────────────────────────────

@router.get("/ai/status")
def get_ai_status():
    """Return multi-LLM provider pool status, cooldowns, and local Vision/OCR engine health."""
    return llm_router.get_public_status()


@router.post("/ai/config")
def update_ai_config(data: AIConfigUpdateRequest):
    """Save LLM API keys, load-balancing strategy, and local Ollama settings."""
    payload = {k: v for k, v in data.model_dump().items() if v is not None}
    return llm_router.save_config(payload)


@router.post("/ai/test")
def test_ai_pipeline():
    """Verify Cloud LLM Router connectivity (if keys configured) + Local Vision CLIP & Apple Neural OCR."""
    clip_ok = variant_vision_service._ensure_clip_loaded()
    vision_status = variant_vision_service.get_engine_status()

    parsed, provider_badge = llm_router.generate_json(
        system_prompt="Respond strictly with JSON: {\"status\": \"ok\", \"message\": \"Cloud LLM connected\"}",
        user_prompt="Ping test.",
        allow_grounding=False,
    )
    return {
        "ok": True,
        "cloud_llm_connected": bool(parsed),
        "cloud_provider_used": provider_badge or "None configured (Using Zero-Key Local Corpus + Specialist Mode)",
        "local_clip_ready": clip_ok,
        "local_vision": vision_status,
    }


# ── Variant Catalogue, Training & Classification Endpoints ──────────────────

@router.get("/searches/{search_id}/variants")
def list_search_variants(search_id: int, db: Session = Depends(get_db)):
    """Return all product variants and live market breakdowns for a tracked search."""
    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Tracked search not found")
    return serialize_search_variants(db, search)


@router.post("/searches/{search_id}/variants/research")
def research_search_variants(
    search_id: int,
    data: ResearchVariantsRequest = ResearchVariantsRequest(),
    db: Session = Depends(get_db),
):
    """Run Deep Research on broad product, build variant catalogue, train local vision model, and classify DB items."""
    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Tracked search not found")

    return run_research_and_train_for_search(
        db=db,
        search=search,
        fetch_live_samples=data.fetch_live_samples,
        classify_existing=data.classify_existing,
        replace_existing=data.replace_existing,
    )


@router.post("/searches/{search_id}/variants/train")
def train_local_vision_for_search(search_id: int, db: Session = Depends(get_db)):
    """Re-train and calibrate local CLIP + Apple Neural OCR visual prototypes for all variants of this search."""
    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Tracked search not found")

    report = variant_vision_service.train_search_variants(db=db, tracked_search=search, fetch_live_samples=True)
    out = serialize_search_variants(db, search)
    out["training_report"] = report
    return out


@router.post("/searches/{search_id}/variants/classify-existing")
def classify_existing_items_for_search(
    search_id: int,
    data: ClassifyExistingRequest = ClassifyExistingRequest(),
    db: Session = Depends(get_db),
):
    """Run local multimodal classifier across stored items for this search."""
    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Tracked search not found")

    search.variants_enabled = True
    db.commit()

    updated = _batch_classify_stored_items(
        db=db,
        search=search,
        only_unclassified=data.only_unclassified,
        max_vision_items=data.max_vision_items,
    )
    out = serialize_search_variants(db, search)
    out["classified_items_updated"] = updated
    return out


@router.post("/searches/{search_id}/variants/select-sniping")
def bulk_select_variant_sniping(
    search_id: int,
    data: BulkVariantSnipeSelectRequest,
    db: Session = Depends(get_db),
):
    """Select 'all', 'none', or 'only' specific variants for Deal Sniper on a tracked search."""
    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Tracked search not found")

    variants = db.query(ProductVariant).filter(ProductVariant.tracked_search_id == search_id).all()
    target_set = set(data.variant_ids or [])

    for v in variants:
        if data.mode == "all":
            v.is_sniping_enabled = True
        elif data.mode == "none":
            v.is_sniping_enabled = False
        elif data.mode == "only":
            v.is_sniping_enabled = v.id in target_set

    db.commit()
    try:
        from ..services.sniper import deal_sniper
        deal_sniper.revalidate_deals(search_id=search_id)
    except Exception:
        pass

    return serialize_search_variants(db, search)


@router.post("/searches/{search_id}/variants")
def create_custom_variant(
    search_id: int,
    data: VariantCreateUpdateRequest,
    db: Session = Depends(get_db),
):
    """Add a custom variant definition to a tracked search and calibrate its visual prototype."""
    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Tracked search not found")

    name = (data.variant_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Variant name is required")

    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "custom_variant"
    v = ProductVariant(
        tracked_search_id=search.id,
        variant_slug=f"{slug}_{int(datetime.now(timezone.utc).timestamp()) % 10000}",
        variant_name=name,
        short_description=data.short_description or "",
        resale_tier=(data.resale_tier or "STANDARD").upper(),
        est_market_value_gbp=data.est_market_value_gbp,
        visual_cues=json.dumps(data.visual_cues or [f"{search.search_text} {name}"]),
        ocr_signatures=json.dumps(data.ocr_signatures or []),
        text_keywords=json.dumps(data.text_keywords or [name.lower()]),
        negative_keywords=json.dumps(data.negative_keywords or []),
        target_max_price=data.target_max_price,
        is_sniping_enabled=True if data.is_sniping_enabled is None else bool(data.is_sniping_enabled),
    )
    search.variants_enabled = True
    db.add(v)
    db.commit()

    variant_vision_service.train_search_variants(db=db, tracked_search=search, fetch_live_samples=False)
    return serialize_search_variants(db, search)


@router.put("/variants/{variant_id}")
def update_variant(
    variant_id: int,
    data: VariantCreateUpdateRequest,
    db: Session = Depends(get_db),
):
    """Update a variant's sniping status, custom target max buy price, or detection keywords."""
    v = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Variant not found")

    if data.variant_name is not None:
        v.variant_name = data.variant_name.strip()
    if data.short_description is not None:
        v.short_description = data.short_description.strip()
    if data.resale_tier is not None:
        v.resale_tier = data.resale_tier.upper()
    if data.est_market_value_gbp is not None:
        v.est_market_value_gbp = data.est_market_value_gbp
    if data.visual_cues is not None:
        v.visual_cues = json.dumps(data.visual_cues)
    if data.ocr_signatures is not None:
        v.ocr_signatures = json.dumps(data.ocr_signatures)
    if data.text_keywords is not None:
        v.text_keywords = json.dumps(data.text_keywords)
    if data.negative_keywords is not None:
        v.negative_keywords = json.dumps(data.negative_keywords)
    if data.clear_target_max_price:
        v.target_max_price = None
    elif data.target_max_price is not None:
        v.target_max_price = float(data.target_max_price) if data.target_max_price > 0 else None
    if data.is_sniping_enabled is not None:
        v.is_sniping_enabled = bool(data.is_sniping_enabled)

    v.updated_at = datetime.now(timezone.utc)
    db.commit()

    try:
        from ..services.sniper import deal_sniper
        deal_sniper.revalidate_deals(search_id=v.tracked_search_id)
    except Exception:
        pass

    search = db.query(TrackedSearch).filter(TrackedSearch.id == v.tracked_search_id).first()
    return serialize_search_variants(db, search)


@router.delete("/variants/{variant_id}")
def delete_variant(variant_id: int, db: Session = Depends(get_db)):
    """Remove a variant definition and clear its reference from items."""
    v = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Variant not found")

    search_id = v.tracked_search_id
    db.query(SoldItem).filter(SoldItem.variant_id == variant_id).update(
        {
            SoldItem.variant_id: None,
            SoldItem.variant_name: None,
            SoldItem.variant_confidence: 0.0,
        },
        synchronize_session=False,
    )
    db.delete(v)
    db.commit()

    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    return serialize_search_variants(db, search)


@router.post("/items/{item_id}/variant")
def assign_item_variant(
    item_id: int,
    data: AssignItemVariantRequest,
    db: Session = Depends(get_db),
):
    """Manually verify or override an item's variant and update local CLIP prototype via online learning."""
    item = db.query(SoldItem).filter(SoldItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    target_v: Optional[ProductVariant] = None
    if data.variant_id is not None:
        target_v = (
            db.query(ProductVariant)
            .filter(
                ProductVariant.id == data.variant_id,
                ProductVariant.tracked_search_id == item.tracked_search_id,
            )
            .first()
        )
        if not target_v:
            raise HTTPException(status_code=404, detail="Target variant does not belong to this item's search")

    variant_vision_service.record_manual_feedback(db=db, item=item, target_variant=target_v)
    return {
        "ok": True,
        "item_id": item.id,
        "variant_id": item.variant_id,
        "variant_name": item.variant_name,
        "variant_confidence": item.variant_confidence,
        "variant_method": item.variant_method,
        "variant_evidence": item.variant_evidence,
    }

