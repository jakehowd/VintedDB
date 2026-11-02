"""Endpoints for Hierarchical AI Model & Exclusive Style Research, Local Vision Training, and Analytics."""

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
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import ProductVariant, SoldItem, TrackedSearch, User
from ..services.llm_router import get_llm_router, llm_router
from ..services.variant_vision import _build_stop_tokens, variant_vision_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["variants"])


# ── Shared SQL & Sniper Hierarchical Model -> Exclusive Style Helpers ───────

def item_has_variant_clause(vid: int):
    """Exact SQLite membership check for `vid` inside compact `variant_ids_json` (e.g. '[6,8]') or `variant_id`.

    Because hierarchical items store `[model_id]` or `[model_id, style_id]`:
    - `item_has_variant_clause(model_id)` matches ALL items in that Model (including all its exclusive sub-styles).
    - `item_has_variant_clause(style_id)` matches STRICTLY items in that exclusive sub-style.
    """
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
    - Single Model ID or Sub-Style ID '6' -> [item_has_variant_clause(6)]
    - Multi-ID '6,8' -> If both a parent Model and one of its own Sub-Styles are passed, narrows to the Sub-Style(s);
      if multiple sibling Models or sibling Sub-Styles are passed, matches any of them (`OR`).
    """
    if not variant_filter:
        return []
    raw = str(variant_filter).strip().lower()
    if raw in ("", "all"):
        return []
    if raw == "unclassified":
        return [SoldItem.variant_id.is_(None)]

    vids: List[int] = []
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

    rows = db.query(ProductVariant.id, ProductVariant.parent_variant_id).filter(ProductVariant.id.in_(vids)).all()
    parents_with_selected_child = {r[1] for r in rows if r[1] is not None and r[1] in vids}
    effective_vids = [vid for vid in vids if vid not in parents_with_selected_child] or vids

    if len(effective_vids) == 1:
        return [item_has_variant_clause(effective_vids[0])]
    return [or_(*(item_has_variant_clause(vid) for vid in effective_vids))]


def evaluate_sniper_variant_match(
    matched_ids: List[int],
    all_variants: List[ProductVariant],
) -> Tuple[bool, Optional[float]]:
    """Evaluate hierarchical `Model -> Exclusive Sub-Styles` Deal Sniper selection.

    Rules:
    - All variants enabled -> Snipes all listings (applying most specific matched price cap if present).
    - Specific Model(s) enabled with ALL (or 0) of its sub-styles enabled -> Snipes any listing in that Model.
    - Specific Sub-Style(s) enabled under a Model (proper subset of that Model's sub-styles) -> Snipes ONLY listings
      matching one of those enabled exclusive Sub-Styles!
    Returns `(allowed_for_snipe, variant_price_override)`.
    """
    if not all_variants:
        return True, None

    enabled = [v for v in all_variants if v.is_sniping_enabled]
    if not enabled:
        return False, None

    v_map = {v.id: v for v in all_variants}
    matched_objs = [v_map[vid] for vid in (matched_ids or []) if vid in v_map]

    def _resolve_cap(objs: List[ProductVariant]) -> Optional[float]:
        # Prefer sub-style cap over parent model cap when available
        styles_with_cap = [
            float(v.target_max_price)
            for v in objs
            if v.parent_variant_id is not None and v.target_max_price and float(v.target_max_price) > 0
        ]
        if styles_with_cap:
            return min(styles_with_cap)
        models_with_cap = [
            float(v.target_max_price)
            for v in objs
            if v.parent_variant_id is None and v.target_max_price and float(v.target_max_price) > 0
        ]
        if models_with_cap:
            return min(models_with_cap)
        return None

    if len(enabled) == len(all_variants):
        return True, _resolve_cap(matched_objs)

    if not matched_objs:
        return False, None

    matched_model = next((v for v in matched_objs if v.parent_variant_id is None), None)
    matched_style = next((v for v in matched_objs if v.parent_variant_id is not None), None)
    if matched_model is None and matched_style is not None:
        matched_model = v_map.get(matched_style.parent_variant_id)

    if matched_model is None:
        return False, None

    m_children = [v for v in all_variants if v.parent_variant_id == matched_model.id]
    enabled_children = [c for c in m_children if c.is_sniping_enabled]

    if 0 < len(enabled_children) < len(m_children):
        # User narrowed this Model to specific exclusive sub-style(s)
        if matched_style is None or not matched_style.is_sniping_enabled:
            return False, None
        return True, _resolve_cap([matched_model, matched_style])

    if len(enabled_children) == len(m_children) and len(m_children) > 0:
        # All sub-styles of this model are enabled (whole Model targeted)
        active_objs = [matched_model] + ([matched_style] if matched_style else [])
        return True, _resolve_cap(active_objs)

    # Zero sub-styles enabled: allowed only if the Model itself is enabled and has no disabled child restriction,
    # OR if the Model has no sub-styles at all
    if matched_model.is_sniping_enabled and len(m_children) == 0:
        return True, _resolve_cap([matched_model])
    if matched_model.is_sniping_enabled and len(enabled_children) == 0:
        # User toggled Model chip ON directly while sub-styles were off -> allow whole model unless style explicitly off
        return True, _resolve_cap([matched_model])

    return False, None


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
    parent_variant_id: Optional[int] = None
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


# ── Helper: Enrich Hierarchical Model -> Exclusive Styles Metrics ────────────

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

    by_id: Dict[int, ProductVariant] = {v.id: v for v in variants}
    raw_serialized: Dict[int, Dict[str, Any]] = {}

    for v in variants:
        is_sub = v.parent_variant_id is not None and v.parent_variant_id in by_id
        parent_obj = by_id.get(v.parent_variant_id) if is_sub else None
        dim_name = "Style" if is_sub else "Model"
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

        raw_serialized[v.id] = {
            "id": v.id,
            "tracked_search_id": v.tracked_search_id,
            "parent_variant_id": v.parent_variant_id if is_sub else None,
            "parent_variant_name": parent_obj.variant_name if parent_obj else None,
            "is_sub_style": is_sub,
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
        }

    # Build hierarchical `models` tree AND tree-ordered flat `variants` list
    models_tree: List[Dict[str, Any]] = []
    ordered_variants: List[Dict[str, Any]] = []
    top_models = [v for v in variants if v.parent_variant_id is None or v.parent_variant_id not in by_id]

    for m in top_models:
        m_dict = dict(raw_serialized[m.id])
        children = [v for v in variants if v.parent_variant_id == m.id]
        sub_dicts = [dict(raw_serialized[c.id]) for c in children]
        sub_sum = sum(sd["total_items"] for sd in sub_dicts)
        m_dict["sub_styles"] = sub_dicts
        m_dict["sub_styles_count"] = len(sub_dicts)
        m_dict["model_only_items"] = max(0, m_dict["total_items"] - sub_sum)
        models_tree.append(m_dict)

        ordered_variants.append(raw_serialized[m.id])
        for c in children:
            ordered_variants.append(raw_serialized[c.id])

    dimensions_order = ["Model"] + (["Style"] if any(v["is_sub_style"] for v in ordered_variants) else [])

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
        "models": models_tree,
        "variants": ordered_variants,
    }


def run_research_and_train_for_search(
    db: Session,
    search: TrackedSearch,
    fetch_live_samples: bool = True,
    classify_existing: bool = True,
    replace_existing: bool = True,
) -> Dict[str, Any]:
    """Execute end-to-end Deep Research -> Hierarchical Model/Style Sync -> 2-Stage Vision Training -> Backfill."""
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

    # Preserve custom user snipe price caps & snipe toggles across re-research when slugs match
    preserved_prefs: Dict[str, Tuple[Optional[float], bool]] = {
        v.variant_slug: (v.target_max_price, bool(v.is_sniping_enabled))
        for v in db.query(ProductVariant).filter(ProductVariant.tracked_search_id == search.id).all()
    }

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

    def _upsert_variant_node(v_spec: Dict[str, Any], parent_id: Optional[int] = None) -> ProductVariant:
        slug = v_spec["variant_slug"]
        dim = "Style" if parent_id is not None else "Model"
        mod = v_spec.get("modality") or "visual"
        est_mkt = v_spec.get("est_market_value_gbp")
        default_snipe_max = max(5.0, round(float(est_mkt) * 0.65, 0)) if est_mkt else None
        prev_cap, prev_enabled = preserved_prefs.get(slug, (default_snipe_max, True))

        if slug in existing_by_slug:
            v_obj = existing_by_slug[slug]
            v_obj.parent_variant_id = parent_id
            v_obj.variant_name = v_spec["variant_name"]
            v_obj.dimension = dim
            v_obj.modality = mod
            v_obj.short_description = v_spec.get("short_description")
            v_obj.resale_tier = v_spec.get("resale_tier", "STANDARD")
            v_obj.est_market_value_gbp = est_mkt
            v_obj.visual_cues = json.dumps(v_spec.get("visual_cues", []))
            v_obj.ocr_signatures = json.dumps(v_spec.get("ocr_signatures", []))
            v_obj.text_keywords = json.dumps(v_spec.get("text_keywords", []))
            v_obj.negative_keywords = json.dumps(v_spec.get("negative_keywords", []))
            v_obj.updated_at = datetime.now(timezone.utc)
        else:
            v_obj = ProductVariant(
                tracked_search_id=search.id,
                parent_variant_id=parent_id,
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
                target_max_price=prev_cap if prev_cap is not None else default_snipe_max,
                is_sniping_enabled=prev_enabled,
            )
            db.add(v_obj)
            db.flush()
            existing_by_slug[slug] = v_obj
        return v_obj

    for model_spec in catalogue.get("variants", []):
        parent_model = _upsert_variant_node(model_spec, parent_id=None)
        for sub_spec in (model_spec.get("sub_styles") or []):
            if isinstance(sub_spec, dict):
                _upsert_variant_node(sub_spec, parent_id=parent_model.id)

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
    visual_variants = [v for v in variants if (v.modality or "visual") in ("visual", "feature") and bool(v.prototype_embedding)]
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
def get_ai_status(current_user: User = Depends(get_current_user)):
    """Return multi-LLM provider pool status, cooldowns, and local Vision/OCR engine health for current user."""
    return get_llm_router(current_user.id).get_public_status()


@router.post("/ai/config")
def update_ai_config(
    data: AIConfigUpdateRequest,
    current_user: User = Depends(get_current_user),
):
    """Save LLM API keys, load-balancing strategy, and local Ollama settings isolated to current user."""
    payload = {k: v for k, v in data.model_dump().items() if v is not None}
    return get_llm_router(current_user.id).save_config(payload)


@router.post("/ai/test")
def test_ai_pipeline(current_user: User = Depends(get_current_user)):
    """Verify Cloud LLM Router connectivity (if keys configured) + Local Vision CLIP & Apple Neural OCR."""
    clip_ok = variant_vision_service._ensure_clip_loaded()
    vision_status = variant_vision_service.get_engine_status()

    parsed, provider_badge = get_llm_router(current_user.id).generate_json(
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
def list_search_variants(
    search_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all product variants, facet dimensions, and live market breakdowns for a tracked search."""
    search = db.query(TrackedSearch).filter(
        TrackedSearch.id == search_id,
        TrackedSearch.user_id == current_user.id,
    ).first()
    if not search:
        raise HTTPException(status_code=404, detail="Tracked search not found")
    return serialize_search_variants(db, search)


@router.post("/searches/{search_id}/variants/research")
def research_search_variants(
    search_id: int,
    data: ResearchVariantsRequest = ResearchVariantsRequest(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run Deep Research on broad product, build multi-category catalogue, train prototypes, and classify DB items."""
    search = db.query(TrackedSearch).filter(
        TrackedSearch.id == search_id,
        TrackedSearch.user_id == current_user.id,
    ).first()
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
def train_local_vision_for_search(
    search_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-train and calibrate local per-dimension prototypes and re-classify items."""
    search = db.query(TrackedSearch).filter(
        TrackedSearch.id == search_id,
        TrackedSearch.user_id == current_user.id,
    ).first()
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run local multi-category classifier across stored items for this search."""
    search = db.query(TrackedSearch).filter(
        TrackedSearch.id == search_id,
        TrackedSearch.user_id == current_user.id,
    ).first()
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Select 'all', 'none', or 'only' specific variants (or multi-category combinations) for Deal Sniper."""
    search = db.query(TrackedSearch).filter(
        TrackedSearch.id == search_id,
        TrackedSearch.user_id == current_user.id,
    ).first()
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add a custom Top-Level Model (`parent_variant_id=None`) or Exclusive Sub-Style (`parent_variant_id=<model_id>`)."""
    search = db.query(TrackedSearch).filter(
        TrackedSearch.id == search_id,
        TrackedSearch.user_id == current_user.id,
    ).first()
    if not search:
        raise HTTPException(status_code=404, detail="Tracked search not found")

    name = (data.variant_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Model / Style name is required")

    parent_id: Optional[int] = None
    parent_model: Optional[ProductVariant] = None
    if data.parent_variant_id is not None:
        parent_row = (
            db.query(ProductVariant)
            .filter(
                ProductVariant.id == int(data.parent_variant_id),
                ProductVariant.tracked_search_id == search.id,
            )
            .first()
        )
        if not parent_row:
            raise HTTPException(status_code=404, detail="Parent model not found in this search")
        # Keep tree strictly 2-tier (Model -> Exclusive Style)
        if parent_row.parent_variant_id is not None:
            parent_row = (
                db.query(ProductVariant)
                .filter(ProductVariant.id == parent_row.parent_variant_id)
                .first()
                or parent_row
            )
        parent_model = parent_row
        parent_id = parent_row.id

    dim = "Style" if parent_id is not None else "Model"
    mod = (data.modality or "visual").strip().lower()
    if mod not in ("visual", "label", "feature"):
        mod = "visual"

    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "custom_variant"
    default_vcue_prefix = (
        f"{search.search_text} {parent_model.variant_name} {name}"
        if parent_model is not None
        else f"{search.search_text} {name}"
    )
    v = ProductVariant(
        tracked_search_id=search.id,
        parent_variant_id=parent_id,
        variant_slug=f"{slug}_{int(datetime.now(timezone.utc).timestamp()) % 10000}",
        variant_name=name,
        dimension=dim,
        modality=mod,
        short_description=data.short_description or "",
        resale_tier=(data.resale_tier or "STANDARD").upper(),
        est_market_value_gbp=data.est_market_value_gbp,
        visual_cues=json.dumps([] if mod == "label" else (data.visual_cues or [default_vcue_prefix])),
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a Model or Exclusive Style's sniping status, custom target max buy price, or detection keywords."""
    v = (
        db.query(ProductVariant)
        .join(TrackedSearch, ProductVariant.tracked_search_id == TrackedSearch.id)
        .filter(
            ProductVariant.id == variant_id,
            TrackedSearch.user_id == current_user.id,
        )
        .first()
    )
    if not v:
        raise HTTPException(status_code=404, detail="Variant not found")

    if data.variant_name is not None and data.variant_name.strip():
        v.variant_name = data.variant_name.strip()
    if data.parent_variant_id is not None:
        if data.parent_variant_id <= 0:
            v.parent_variant_id = None
            v.dimension = "Model"
        elif data.parent_variant_id != v.id:
            v.parent_variant_id = int(data.parent_variant_id)
            v.dimension = "Style"
    if data.modality is not None and data.modality.strip().lower() in ("visual", "label", "feature"):
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
def delete_variant(
    variant_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a Model (plus its exclusive sub-styles) or a single Sub-Style and clean item references."""
    v = (
        db.query(ProductVariant)
        .join(TrackedSearch, ProductVariant.tracked_search_id == TrackedSearch.id)
        .filter(
            ProductVariant.id == variant_id,
            TrackedSearch.user_id == current_user.id,
        )
        .first()
    )
    if not v:
        raise HTTPException(status_code=404, detail="Variant not found")

    search_id = v.tracked_search_id
    child_rows = db.query(ProductVariant).filter(ProductVariant.parent_variant_id == variant_id).all()
    removed_ids = {variant_id} | {c.id for c in child_rows}

    clause = or_(*(item_has_variant_clause(rid) for rid in removed_ids))
    affected_items = db.query(SoldItem).filter(clause).all()
    remaining_map = {
        rv.id: rv.variant_name
        for rv in db.query(ProductVariant)
        .filter(ProductVariant.tracked_search_id == search_id, ~ProductVariant.id.in_(removed_ids))
        .all()
    }
    for it in affected_items:
        kept_ids = [vid for vid in it.parsed_variant_ids() if vid not in removed_ids and vid in remaining_map]
        if kept_ids:
            kept_names = [remaining_map[vid] for vid in kept_ids]
            it.variant_id = kept_ids[-1]
            it.variant_ids_json = json.dumps(kept_ids, separators=(",", ":"))
            it.variant_name = " › ".join(kept_names)
            it.variant_names_json = json.dumps(kept_names)
        else:
            it.apply_variant_result(None)

    for c in child_rows:
        db.delete(c)
    db.delete(v)
    db.commit()

    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    return serialize_search_variants(db, search)


@router.post("/items/{item_id}/variant")
def assign_item_variant(
    item_id: int,
    data: AssignItemVariantRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually assign 0, 1, or multiple category variants to an item and update local prototypes."""
    item = (
        db.query(SoldItem)
        .join(TrackedSearch, SoldItem.tracked_search_id == TrackedSearch.id)
        .filter(
            SoldItem.id == item_id,
            TrackedSearch.user_id == current_user.id,
        )
        .first()
    )
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
