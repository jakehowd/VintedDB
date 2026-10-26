"""Endpoints for AI Variant Deep Research, Local Vision Training, and Multi-Category Facets."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ProductVariant, SoldItem, TrackedSearch
from ..services.llm_router import llm_router
from ..services.variant_vision import _build_stop_tokens, variant_vision_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["variants"])


# ── Shared SQL & Sniper Multi-Category Facet Helpers ────────────────────────

def item_has_variant_clause(vid: int):
    """Exact SQLite membership check for `vid` inside compact `variant_ids_json` (e.g. '[6,8]') or legacy `variant_id`."""
    return or_(
        SoldItem.variant_id == vid,
        SoldItem.variant_ids_json == f"[{vid}]",
        SoldItem.variant_ids_json.like(f"[{vid},%"),
        SoldItem.variant_ids_json.like(f"%,{vid},%"),
        SoldItem.variant_ids_json.like(f"%,{vid}]"),
    )


def build_variant_filter_clauses(db: Session, variant_filter: Optional[str]) -> List[Any]:
    """Parse `?variant_id=...` into SQLAlchemy filter clauses.

    Supports:
    - None / '' / 'all' -> []
    - 'unclassified' -> [SoldItem.variant_id.is_(None)]
    - Single ID '6' -> [item_has_variant_clause(6)]
    - Multi-ID '6,8' -> OR within the same `dimension`, AND across different `dimension`s
      (e.g. Style='Cable-Knit' AND Material='Wool / Merino').
    """
    if not variant_filter:
        return []
    raw = str(variant_filter).strip().lower()
    if raw in ("", "all"):
        return []
    if raw == "unclassified":
        return [SoldItem.variant_id.is_(None)]

    vids = []
    for part in raw.split(","):
        p = part.strip()
        if p.isdigit():
            val = int(p)
            if val not in vids:
                vids.append(val)

    if not vids:
        return []
    if len(vids) == 1:
        return [item_has_variant_clause(vids[0])]

    rows = db.query(ProductVariant.id, ProductVariant.dimension).filter(ProductVariant.id.in_(vids)).all()
    dim_by_id = {r[0]: ((r[1] or "Style").strip() or "Style") for r in rows}

    grouped: Dict[str, List[int]] = {}
    for vid in vids:
        dim = dim_by_id.get(vid, f"dim_{vid}")
        grouped.setdefault(dim, []).append(vid)

    clauses: List[Any] = []
    for dim_vids in grouped.values():
        if len(dim_vids) == 1:
            clauses.append(item_has_variant_clause(dim_vids[0]))
        else:
            clauses.append(or_(*(item_has_variant_clause(vid) for vid in dim_vids)))
    return clauses


def evaluate_sniper_variant_match(
    matched_ids: List[int],
    all_variants: List[ProductVariant],
) -> Tuple[bool, Optional[float]]:
    """Evaluate faceted Deal Sniper selection (`OR` within dimension, `AND` across active dimensions).

    Returns `(allowed_for_snipe, variant_price_override)`.
    """
    if not all_variants:
        return True, None

    enabled = [v for v in all_variants if v.is_sniping_enabled]
    if not enabled:
        return False, None

    matched_set = set(matched_ids or [])
    v_map = {v.id: v for v in all_variants}
    matched_objs = [v_map[vid] for vid in matched_ids if vid in v_map]

    # Price cap: if matched variants define custom max prices, use the highest matched tier cap
    # unless multiple facets restrict it (when specific facets are filtered, respect the tightest enabled cap).
    all_enabled = len(enabled) == len(all_variants)

    if all_enabled:
        caps = [float(v.target_max_price) for v in matched_objs if v.target_max_price and float(v.target_max_price) > 0]
        return True, (max(caps) if caps else None)

    if not matched_set:
        return False, None

    enabled_by_dim: Dict[str, set[int]] = {}
    for v in enabled:
        dim_key = (v.dimension or "Style").strip() or "Style"
        enabled_by_dim.setdefault(dim_key, set()).add(v.id)

    # Every dimension where the user enabled >= 1 chip MUST be satisfied!
    for dim_key, allowed_vids in enabled_by_dim.items():
        if not (matched_set & allowed_vids):
            return False, None

    active_matched = [v for v in matched_objs if v.id in {ev.id for ev in enabled}]
    caps = [float(v.target_max_price) for v in active_matched if v.target_max_price and float(v.target_max_price) > 0]
    return True, (min(caps) if caps else None)


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
    replace_existing: bool = True


class ClassifyExistingRequest(BaseModel):
    only_unclassified: bool = False
    max_vision_items: int = Field(300, ge=0, le=3000)


class VariantCreateUpdateRequest(BaseModel):
    variant_name: Optional[str] = None
    dimension: Optional[str] = None
    modality: Optional[str] = None
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
    variant_ids: Optional[List[int]] = None


# ── Helper: Enrich Variant Serialization with Multi-Category Metrics ────────

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

    dimensions_order: List[str] = []
    out_variants: List[Dict[str, Any]] = []

    for v in variants:
        dim_name = (v.dimension or "Style").strip() or "Style"
        if dim_name not in dimensions_order:
            dimensions_order.append(dim_name)
        has_clause = item_has_variant_clause(v.id)

        v_all = (
            db.query(func.count(SoldItem.id), func.avg(SoldItem.price))
            .filter(*clean_filter, has_clause)
            .first()
        )
        v_sold = (
            db.query(func.count(SoldItem.id), func.avg(SoldItem.price))
            .filter(*clean_filter, has_clause, SoldItem.is_sold == True)
            .first()
        )
        v_active = (
            db.query(func.count(SoldItem.id), func.avg(SoldItem.price))
            .filter(*clean_filter, has_clause, SoldItem.is_sold == False)
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
            "dimension": dim_name,
            "modality": (v.modality or "visual").strip().lower(),
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
        "dimensions": dimensions_order,
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
    replace_existing: bool = True,
) -> Dict[str, Any]:
    """Execute end-to-end Deep Research -> Faceted Catalogue Sync -> Per-Dimension Vision Training -> Backfill."""
    db_samples = (
        db.query(SoldItem)
        .filter(SoldItem.tracked_search_id == search.id, SoldItem.is_replica.isnot(True))
        .order_by(SoldItem.captured_at.desc())
        .limit(50)
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

    catalogue = llm_router.deep_research_product_variants(
        search_text=search.search_text,
        brand=search.brand,
        sample_listings=sample_dicts,
    )

    if replace_existing:
        db.query(SoldItem).filter(SoldItem.tracked_search_id == search.id).update(
            {
                SoldItem.variant_id: None,
                SoldItem.variant_ids_json: None,
                SoldItem.variant_name: None,
                SoldItem.variant_names_json: None,
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
        dim = v_spec.get("dimension") or "Style"
        mod = v_spec.get("modality") or "visual"
        if slug in existing_by_slug:
            v_obj = existing_by_slug[slug]
            v_obj.variant_name = v_spec["variant_name"]
            v_obj.dimension = dim
            v_obj.modality = mod
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
                dimension=dim,
                modality=mod,
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

    train_report = variant_vision_service.train_search_variants(
        db=db,
        tracked_search=search,
        fetch_live_samples=fetch_live_samples,
    )

    classified_count = 0
    if classify_existing:
        classified_count = _batch_classify_stored_items(
            db=db,
            search=search,
            only_unclassified=False,
            max_vision_items=180,
        )

    out = serialize_search_variants(db, search)
    out["training_report"] = train_report
    out["classified_items_updated"] = classified_count
    return out


def _batch_classify_stored_items(
    db: Session,
    search: TrackedSearch,
    only_unclassified: bool = False,
    max_vision_items: int = 180,
) -> int:
    variants = (
        db.query(ProductVariant)
        .filter(ProductVariant.tracked_search_id == search.id)
        .order_by(ProductVariant.id)
        .all()
    )
    if not variants:
        return 0

    stop_tokens = _build_stop_tokens(search)
    visual_variants = [v for v in variants if (v.modality or "visual") == "visual" and bool(v.prototype_embedding)]
    has_ocr_sigs = any(bool(v.parsed_list("ocr_signatures")) for v in variants)

    q = db.query(SoldItem).filter(SoldItem.tracked_search_id == search.id)
    if only_unclassified:
        q = q.filter(SoldItem.variant_id.is_(None))
    items = q.order_by(SoldItem.captured_at.desc()).all()

    eval_items: List[Tuple[SoldItem, str]] = []
    uncached_to_download: List[str] = []

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
        eval_items.append((item, desc))

        if item.photo_url and len(uncached_to_download) < max_vision_items:
            if not variant_vision_service.get_cached_image_path(item.photo_url):
                # Download when visual dimension is unresolved by title alone
                vis_sc = max(
                    (variant_vision_service._score_seed_match(f"{item.title or ''} {desc}", v, stop_tokens) for v in visual_variants),
                    default=0.0,
                )
                if vis_sc < 2.2:
                    uncached_to_download.append(item.photo_url)

    # Parallel image pre-caching for crisp throughput
    if uncached_to_download and max_vision_items > 0:
        unique_urls = list(dict.fromkeys(uncached_to_download))[:max_vision_items]
        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(variant_vision_service._download_image_cached, unique_urls))

    # Batch-embed all items that have a local cached photo on disk
    path_by_item_id: Dict[int, str] = {}
    embed_cap = max(320, max_vision_items * 2) if max_vision_items > 0 else 0
    # Prioritize unclassified items first so incremental Sync Tags progressively embeds the whole archive
    prioritized_for_embed = sorted(eval_items, key=lambda pair: (pair[0].variant_id is not None,))
    for item, _ in prioritized_for_embed:
        if len(path_by_item_id) >= embed_cap:
            break
        if item.photo_url:
            cp = variant_vision_service.get_cached_image_path(item.photo_url)
            if cp:
                path_by_item_id[item.id] = cp

    vec_by_path: Dict[str, np.ndarray] = {}
    unique_paths = list(dict.fromkeys(path_by_item_id.values()))
    if unique_paths and visual_variants and variant_vision_service._ensure_clip_loaded():
        for i in range(0, len(unique_paths), 64):
            chunk = unique_paths[i : i + 64]
            mat = variant_vision_service.embed_images(chunk)
            if mat is not None and mat.shape[0] == len(chunk):
                for p, row_vec in zip(chunk, mat):
                    vec_by_path[p] = row_vec

    ocr_by_path: Dict[str, str] = {}
    if unique_paths and has_ocr_sigs and max_vision_items > 0:
        ocr_paths = unique_paths[: min(len(unique_paths), 80)]
        raw_ocr = variant_vision_service.run_batch_ocr(ocr_paths)
        for p, lines in raw_ocr.items():
            ocr_by_path[p] = " | ".join(lines)

    valid_vid_set = {v.id for v in variants}
    classified_count = 0
    changed = False

    for item, desc in eval_items:
        c_path = path_by_item_id.get(item.id)
        pre_vec = vec_by_path.get(c_path) if c_path else None
        pre_ocr = ocr_by_path.get(c_path, "") if c_path else ""

        res = variant_vision_service.classify_item_variant(
            db=db,
            tracked_search=search,
            item_data={
                "title": item.title,
                "description": desc,
                "photo_url": item.photo_url,
            },
            variants=variants,
            allow_vision=bool(c_path),
            precomputed_img_vec=pre_vec,
            precomputed_ocr_text=pre_ocr,
        )
        # Preserve earlier photo/OCR tags if this item's image wasn't re-evaluated in this cap window
        if res is None and not c_path and item.variant_id in valid_vid_set:
            existing_ids = [vid for vid in item.parsed_variant_ids() if vid in valid_vid_set]
            if existing_ids:
                classified_count += 1
                continue

        item.apply_variant_result(res)
        changed = True
        if res:
            classified_count += 1

    if changed:
        db.commit()
    return classified_count


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
        "cloud_provider_used": provider_badge or "Built-in Specialist + Live Vinted Miner (No API key needed)",
        "local_clip_ready": clip_ok,
        "local_vision": vision_status,
    }


# ── Variant Catalogue, Training & Classification Endpoints ──────────────────

@router.get("/searches/{search_id}/variants")
def list_search_variants(search_id: int, db: Session = Depends(get_db)):
    """Return all product variants, facet dimensions, and live market breakdowns for a tracked search."""
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
    """Run Deep Research on broad product, build multi-category catalogue, train prototypes, and classify DB items."""
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
    """Re-train and calibrate local per-dimension prototypes and re-classify items."""
    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Tracked search not found")

    report = variant_vision_service.train_search_variants(db=db, tracked_search=search, fetch_live_samples=True)
    updated = _batch_classify_stored_items(db=db, search=search, only_unclassified=False, max_vision_items=180)
    out = serialize_search_variants(db, search)
    out["training_report"] = report
    out["classified_items_updated"] = updated
    return out


@router.post("/searches/{search_id}/variants/classify-existing")
def classify_existing_items_for_search(
    search_id: int,
    data: ClassifyExistingRequest = ClassifyExistingRequest(),
    db: Session = Depends(get_db),
):
    """Run local multi-category classifier across stored items for this search."""
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
    """Select 'all', 'none', or 'only' specific variants (or multi-category combinations) for Deal Sniper."""
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
    """Add a custom category/variant tag to a tracked search."""
    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Tracked search not found")

    name = (data.variant_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Variant name is required")

    dim = (data.dimension or "Style").strip().title() or "Style"
    mod = (data.modality or ("label" if dim.lower() in ("material", "fabric", "kit", "bundle") else "visual")).strip().lower()
    if mod not in ("visual", "label"):
        mod = "visual"

    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "custom_variant"
    v = ProductVariant(
        tracked_search_id=search.id,
        variant_slug=f"{slug}_{int(datetime.now(timezone.utc).timestamp()) % 10000}",
        variant_name=name,
        dimension=dim,
        modality=mod,
        short_description=data.short_description or "",
        resale_tier=(data.resale_tier or "STANDARD").upper(),
        est_market_value_gbp=data.est_market_value_gbp,
        visual_cues=json.dumps([] if mod == "label" else (data.visual_cues or [f"{search.search_text} {name}"])),
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
    _batch_classify_stored_items(db=db, search=search, only_unclassified=False, max_vision_items=40)
    return serialize_search_variants(db, search)


@router.put("/variants/{variant_id}")
def update_variant(
    variant_id: int,
    data: VariantCreateUpdateRequest,
    db: Session = Depends(get_db),
):
    """Update a variant's sniping status, dimension, custom target max buy price, or detection keywords."""
    v = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Variant not found")

    if data.variant_name is not None:
        v.variant_name = data.variant_name.strip()
    if data.dimension is not None and data.dimension.strip():
        v.dimension = data.dimension.strip().title()
    if data.modality is not None and data.modality.strip().lower() in ("visual", "label"):
        v.modality = data.modality.strip().lower()
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
    """Remove a variant definition and clean its ID from any multi-tagged items."""
    v = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Variant not found")

    search_id = v.tracked_search_id
    affected_items = db.query(SoldItem).filter(item_has_variant_clause(variant_id)).all()
    remaining_map = {
        rv.id: rv.variant_name
        for rv in db.query(ProductVariant)
        .filter(ProductVariant.tracked_search_id == search_id, ProductVariant.id != variant_id)
        .all()
    }
    for it in affected_items:
        kept_ids = [vid for vid in it.parsed_variant_ids() if vid != variant_id and vid in remaining_map]
        if kept_ids:
            kept_names = [remaining_map[vid] for vid in kept_ids]
            it.variant_id = kept_ids[0]
            it.variant_ids_json = json.dumps(kept_ids, separators=(",", ":"))
            it.variant_name = " · ".join(kept_names)
            it.variant_names_json = json.dumps(kept_names)
        else:
            it.apply_variant_result(None)

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
    """Manually assign 0, 1, or multiple category variants to an item and update local prototypes."""
    item = db.query(SoldItem).filter(SoldItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    requested_ids: List[int] = []
    if data.variant_ids is not None:
        for vid in data.variant_ids:
            if vid is not None and int(vid) not in requested_ids:
                requested_ids.append(int(vid))
    elif data.variant_id is not None:
        requested_ids = [int(data.variant_id)]

    target_variants: List[ProductVariant] = []
    if requested_ids:
        found = (
            db.query(ProductVariant)
            .filter(
                ProductVariant.id.in_(requested_ids),
                ProductVariant.tracked_search_id == item.tracked_search_id,
            )
            .order_by(ProductVariant.id)
            .all()
        )
        if len(found) != len(requested_ids):
            raise HTTPException(status_code=404, detail="One or more target variants do not belong to this item's search")
        target_variants = found

    variant_vision_service.record_manual_feedback(db=db, item=item, target_variants=target_variants)
    return {
        "ok": True,
        "item_id": item.id,
        "variant_id": item.variant_id,
        "variant_ids": item.parsed_variant_ids(),
        "variant_name": item.variant_name,
        "variant_names": item.parsed_variant_names(),
        "variant_confidence": item.variant_confidence,
        "variant_method": item.variant_method,
        "variant_evidence": item.variant_evidence,
    }
