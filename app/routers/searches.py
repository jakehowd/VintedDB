"""CRUD endpoints for tracked search terms."""

import json
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ProductVariant, SoldItem, TrackedSearch
from ..schemas import (
    SearchCreate,
    SearchUpdate,
    SuggestClassificationRequest,
    SuggestClassificationResponse,
)
from ..scraper import scrape_and_store
from ..services.antirep import is_existing_item_compliant
from ..services.item_classifier import (
    VINTED_CATEGORIES,
    is_child_size,
    is_item_relevant,
    normalize_categories,
    suggest_brand_and_category,
)

router = APIRouter(prefix="/api", tags=["searches"])


@router.get("/searches/categories")
def get_categories():
    """Return available Vinted category taxonomy for hierarchical multi-selection."""
    return [
        {
            "id": cat_id,
            "name": info["name"],
            "short_name": info.get("short_name", info["name"]),
            "parent": info["parent"],
            "group_id": info.get("group_id", info["parent"].lower().replace(" ", "_")),
            "department": info.get("department", "Clothing"),
            "vinted_path": info.get("vinted_path", f"{info.get('department', 'Clothing')} › {info['parent']} › {info['name']}"),
            "vinted_catalog_ids": info.get("vinted_catalog_ids", []),
            "is_group_all": bool(info.get("is_group_all", False)),
        }
        for cat_id, info in VINTED_CATEGORIES.items()
    ]


@router.post("/searches/suggest-classification", response_model=SuggestClassificationResponse)
def suggest_classification(data: SuggestClassificationRequest):
    """Auto-suggest brand and Vinted categories from search query using local classifier."""
    return suggest_brand_and_category(data.query)


@router.get("/searches")
def list_searches(db: Session = Depends(get_db)):
    """Return all tracked searches with their item counts and classification metadata."""
    searches = db.query(TrackedSearch).order_by(TrackedSearch.id).all()
    result = []
    for s in searches:
        count = (
            db.query(func.count(SoldItem.id))
            .filter(SoldItem.tracked_search_id == s.id)
            .scalar()
        )
        v_count = (
            db.query(func.count(ProductVariant.id))
            .filter(ProductVariant.tracked_search_id == s.id)
            .scalar()
        ) or 0
        cat_summary = {}
        if getattr(s, "catalogue_summary", None):
            try:
                cat_summary = json.loads(s.catalogue_summary)
            except Exception:
                cat_summary = {}
        cats = normalize_categories(s.category)
        result.append(
            {
                "id": s.id,
                "search_text": s.search_text,
                "brand": s.brand or "",
                "category": s.category or "",
                "categories": cats,
                "must_exclude": s.must_exclude or "",
                "exclude_child_sizes": bool(getattr(s, "exclude_child_sizes", False)),
                "variants_enabled": bool(getattr(s, "variants_enabled", False)),
                "variants_count": v_count,
                "catalogue_summary": cat_summary,
                "antirep_settings": s.antirep_config,
                "antirep_config": s.antirep_config,
                "is_active": s.is_active,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "item_count": count or 0,
            }
        )
    return result


@router.post("/searches")
def create_search(data: SearchCreate, db: Session = Depends(get_db)):
    """Add a new search term to track with brand, category, and anti-rep filtering."""
    text = data.search_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Search text cannot be empty")

    existing = (
        db.query(TrackedSearch)
        .filter(TrackedSearch.search_text == text)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Search already exists")

    # Resolve brand and categories
    brand = (data.brand or "").strip()
    categories = data.categories or []
    if not categories and data.category:
        categories = normalize_categories(data.category)

    # If neither provided, run auto-suggestion
    if not brand and not categories:
        suggestion = suggest_brand_and_category(text)
        brand = suggestion.get("suggested_brand") or ""
        categories = suggestion.get("suggested_categories") or []

    cat_json = json.dumps(categories) if categories else None
    antirep_json = json.dumps(data.antirep_settings) if data.antirep_settings else None

    search = TrackedSearch(
        search_text=text,
        brand=brand or None,
        category=cat_json,
        must_exclude=data.must_exclude.strip() if data.must_exclude else None,
        exclude_child_sizes=bool(data.exclude_child_sizes),
        variants_enabled=bool(data.variants_enabled or data.auto_research_variants),
        antirep_settings=antirep_json,
    )
    db.add(search)
    db.commit()
    db.refresh(search)

    researched_variants: List[Dict[str, Any]] = []
    if data.auto_research_variants:
        try:
            from .variants import run_research_and_train_for_search
            research_out = run_research_and_train_for_search(
                db=db,
                search=search,
                fetch_live_samples=True,
                classify_existing=False,
            )
            researched_variants = research_out.get("variants", [])
        except Exception:
            pass

    return {
        "id": search.id,
        "search_text": search.search_text,
        "brand": search.brand or "",
        "category": search.category or "",
        "categories": normalize_categories(search.category),
        "must_exclude": search.must_exclude or "",
        "exclude_child_sizes": bool(search.exclude_child_sizes),
        "variants_enabled": bool(getattr(search, "variants_enabled", False)),
        "variants_count": len(researched_variants),
        "variants": researched_variants,
        "antirep_settings": search.antirep_config,
        "antirep_config": search.antirep_config,
        "is_active": search.is_active,
        "item_count": 0,
    }


@router.put("/searches/{search_id}")
def update_search(search_id: int, data: SearchUpdate, db: Session = Depends(get_db)):
    """Update brand, categories, and anti-rep filters on an existing search, pushing rules immediately."""
    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")

    if data.search_text is not None:
        search.search_text = data.search_text.strip()
    if data.brand is not None:
        search.brand = data.brand.strip() or None
    if data.categories is not None:
        search.category = json.dumps(data.categories) if data.categories else None
    elif data.category is not None:
        cats = normalize_categories(data.category)
        search.category = json.dumps(cats) if cats else None
    if data.must_exclude is not None:
        search.must_exclude = data.must_exclude.strip() or None
    if data.exclude_child_sizes is not None:
        search.exclude_child_sizes = bool(data.exclude_child_sizes)
    if data.antirep_settings is not None:
        search.antirep_settings = json.dumps(data.antirep_settings)
    if data.variants_enabled is not None:
        search.variants_enabled = bool(data.variants_enabled)
    if data.is_active is not None:
        search.is_active = data.is_active

    db.commit()
    db.refresh(search)

    # Immediately enforce the updated filters on all existing items in the database
    categories = normalize_categories(search.category)
    antirep_config = search.antirep_config
    existing_items = db.query(SoldItem).filter(SoldItem.tracked_search_id == search.id).all()

    seller_counts: Dict[str, int] = {}
    for it in existing_items:
        if it.seller_id:
            seller_counts[it.seller_id] = seller_counts.get(it.seller_id, 0) + 1

    purged = 0
    flagged = 0
    reasons: Dict[str, int] = {}

    for item in existing_items:
        # 1. Child size check
        if search.exclude_child_sizes:
            is_child = getattr(item, "is_child_size", False) or is_child_size(item.title, item.size)[0]
            if is_child:
                purged += 1
                reasons["Child size excluded"] = reasons.get("Child size excluded", 0) + 1
                db.delete(item)
                continue

        # 2. Spam & brand/category coherence check
        relevant, spam_reason = is_item_relevant(
            target_brand=search.brand,
            target_categories=categories,
            must_exclude=search.must_exclude,
            item_brand=item.brand,
            item_title=item.title,
            exclude_child_sizes=bool(search.exclude_child_sizes),
            item_size=item.size,
        )
        if not relevant:
            purged += 1
            reasons[spam_reason] = reasons.get(spam_reason, 0) + 1
            db.delete(item)
            continue

        # 3. Anti-Rep compliance check (including seller reviews & duplicate photos)
        clean, rep_reason = is_existing_item_compliant(
            item=item,
            antirep_config=antirep_config,
            db=db,
            seller_item_counts=seller_counts,
        )
        if not clean:
            if not item.is_replica or item.replica_reason != rep_reason:
                flagged += 1
            item.is_replica = True
            item.replica_reason = rep_reason
            reasons[rep_reason] = reasons.get(rep_reason, 0) + 1
        elif item.is_replica:
            item.is_replica = False
            item.replica_reason = None

    db.commit()

    # Revalidate live Deal Sniper stream against updated search rules
    try:
        from ..routers.sniper import deal_sniper
        deal_sniper.revalidate_deals(search_id=search.id)
    except Exception:
        pass

    remaining = (
        db.query(func.count(SoldItem.id))
        .filter(SoldItem.tracked_search_id == search.id, SoldItem.is_replica.isnot(True))
        .scalar()
    )

    return {
        "id": search.id,
        "search_text": search.search_text,
        "brand": search.brand or "",
        "category": search.category or "",
        "categories": categories,
        "must_exclude": search.must_exclude or "",
        "exclude_child_sizes": bool(search.exclude_child_sizes),
        "antirep_settings": antirep_config,
        "antirep_config": antirep_config,
        "is_active": search.is_active,
        "item_count": remaining or 0,
        "purged_count": purged + flagged,
        "remaining_count": remaining or 0,
        "reasons": reasons,
    }


@router.post("/searches/{search_id}/toggle-child-filter")
def toggle_search_child_filter(search_id: int, db: Session = Depends(get_db)):
    """1-click toggle to exclude or allow child sizes on a tracked search."""
    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")

    search.exclude_child_sizes = not bool(search.exclude_child_sizes)
    purged_count = 0

    if search.exclude_child_sizes:
        # Immediately purge existing child sizes from this search
        items = db.query(SoldItem).filter(SoldItem.tracked_search_id == search.id).all()
        for item in items:
            is_child, _ = is_child_size(item.title, item.size)
            if is_child or getattr(item, "is_child_size", False):
                db.delete(item)
                purged_count += 1
        if purged_count:
            db.commit()

    db.commit()
    db.refresh(search)

    try:
        from ..routers.sniper import deal_sniper
        deal_sniper.revalidate_deals(search_id=search.id)
    except Exception:
        pass

    msg = (
        f"Child sizes excluded for '{search.search_text}' ({purged_count} child items purged)."
        if search.exclude_child_sizes
        else f"Child sizes now allowed for '{search.search_text}'."
    )

    return {
        "ok": True,
        "search_id": search.id,
        "exclude_child_sizes": bool(search.exclude_child_sizes),
        "purged_count": purged_count,
        "message": msg,
    }


@router.post("/searches/{search_id}/purge-child-sizes")
def purge_search_child_sizes(search_id: int, db: Session = Depends(get_db)):
    """Permanently delete all child and youth listings captured for this search."""
    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")

    items = db.query(SoldItem).filter(SoldItem.tracked_search_id == search.id).all()
    purged_count = 0
    for item in items:
        is_child, _ = is_child_size(item.title, item.size)
        if is_child or getattr(item, "is_child_size", False):
            db.delete(item)
            purged_count += 1

    if purged_count:
        db.commit()

    return {
        "ok": True,
        "search_id": search.id,
        "purged_count": purged_count,
        "message": f"Purged {purged_count} child-sized listings from '{search.search_text}'.",
    }


@router.post("/searches/{search_id}/cleanup")
def cleanup_search_items(search_id: int, db: Session = Depends(get_db)):
    """Purge existing items that contradict brand, category, or anti-rep filters."""
    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")

    categories = normalize_categories(search.category)
    antirep_config = search.antirep_config
    items = db.query(SoldItem).filter(SoldItem.tracked_search_id == search_id).all()

    seller_counts: Dict[str, int] = {}
    for it in items:
        if it.seller_id:
            seller_counts[it.seller_id] = seller_counts.get(it.seller_id, 0) + 1

    purged = 0
    reasons_summary: Dict[str, int] = {}

    for item in items:
        relevant, reason = is_item_relevant(
            target_brand=search.brand,
            target_categories=categories,
            must_exclude=search.must_exclude,
            item_brand=item.brand,
            item_title=item.title,
        )
        if not relevant:
            purged += 1
            reasons_summary[reason] = reasons_summary.get(reason, 0) + 1
            db.delete(item)
            continue

        clean, rep_reason = is_existing_item_compliant(
            item=item,
            antirep_config=antirep_config,
            db=db,
            seller_item_counts=seller_counts,
        )
        if not clean:
            purged += 1
            reasons_summary[rep_reason] = reasons_summary.get(rep_reason, 0) + 1
            db.delete(item)

    if purged:
        db.commit()

    remaining = (
        db.query(func.count(SoldItem.id))
        .filter(SoldItem.tracked_search_id == search_id)
        .scalar()
    )
    return {
        "search_id": search_id,
        "search_text": search.search_text,
        "purged_count": purged,
        "remaining_count": remaining or 0,
        "reasons": reasons_summary,
    }


@router.delete("/searches/{search_id}")
def delete_search(search_id: int, db: Session = Depends(get_db)):
    """Remove a tracked search and all its associated items."""
    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")

    db.delete(search)
    db.commit()
    return {"ok": True}


@router.post("/searches/{search_id}/refresh")
def refresh_search(search_id: int, db: Session = Depends(get_db)):
    """Manually trigger a scrape for a specific search, enforcing spam & anti-rep filters."""
    search = db.query(TrackedSearch).filter(TrackedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")

    try:
        details = scrape_and_store(db, search, return_details=True)
        new_items = details.get("new_count", 0) if isinstance(details, dict) else details
        filtered_spam = details.get("filtered_spam_count", 0) if isinstance(details, dict) else 0
        filtered_antirep = details.get("filtered_antirep_count", 0) if isinstance(details, dict) else 0

        return {
            "new_items": new_items,
            "filtered_spam": filtered_spam,
            "filtered_antirep": filtered_antirep,
            "total_blocked": filtered_spam + filtered_antirep,
            "search_text": search.search_text,
        }
    except Exception as exc:
        err_msg = str(exc)
        if "403" in err_msg or "session" in err_msg.lower():
            detail = "Vinted temporary rate limit / Cloudflare check active on your network. Please wait a few moments before trying again."
        else:
            detail = f"Scrape failed: {err_msg}"
        raise HTTPException(status_code=502, detail=detail)




