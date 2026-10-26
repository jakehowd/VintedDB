"""Endpoints for Database Management, Advanced Explorer, and Historical Data Pruning."""

from datetime import datetime, timezone, timedelta
import os
import re
import time
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_, desc, asc, case

from ..database import get_db
from ..models import ProductVariant, SoldItem, TrackedSearch, ScrapedPhotoRecord
from ..config import DB_PATH
from ..services.antirep import extract_photo_key, is_new_with_tags, record_photo_fingerprint
from ..services.item_classifier import is_child_size

router = APIRouter(prefix="/api/database", tags=["database"])


# ── Pydantic Request Models ─────────────────────────────────────────────────

class DeleteMonthRequest(BaseModel):
    month: str = Field(..., description="Month identifier in YYYY-MM format, e.g. '2026-10'")
    search_id: Optional[int] = Field(None, description="Optional search ID to restrict deletion to")


class DeleteReplicaMonthRequest(BaseModel):
    month: str = Field(..., description="Month identifier in YYYY-MM format, e.g. '2026-10'")
    search_id: Optional[int] = Field(None, description="Optional search ID to restrict deletion to")


class PruneOlderRequest(BaseModel):
    older_than_days: int = Field(..., ge=1, description="Delete records older than this many days")
    search_id: Optional[int] = Field(None, description="Optional search ID to restrict deletion to")


class ClearSearchDataRequest(BaseModel):
    search_id: int = Field(..., description="Search ID whose items should be cleared")


# ── Specialized Database Analytics ──────────────────────────────────────────

@router.get("/analytics")
def get_database_analytics(db: Session = Depends(get_db)):
    """Return specialized high-resolution analytics for all stored items, sales vs replicas, and flag reasons."""
    total_items = db.query(SoldItem).count()
    if total_items == 0:
        return {
            "total_items": 0,
            "clean_items": 0,
            "replica_items": 0,
            "replica_rate_pct": 0.0,
            "sold_count": 0,
            "active_count": 0,
            "clean_avg_price": 0.0,
            "sold_avg_price": 0.0,
            "active_avg_price": 0.0,
            "replica_avg_price": 0.0,
            "total_volume_gbp": 0.0,
            "sold_volume_gbp": 0.0,
            "reasons_breakdown": [],
            "monthly_trend": [],
            "top_brands": [],
            "searches": [],
        }

    # Clean vs Replica counts
    replica_items = db.query(SoldItem).filter(SoldItem.is_replica == True).count()
    clean_items = total_items - replica_items
    replica_rate_pct = round((replica_items / total_items * 100), 1) if total_items else 0.0

    # Child vs Adult size counts
    child_items_count = db.query(SoldItem).filter(SoldItem.is_child_size == True).count()
    adult_items_count = total_items - child_items_count

    # Sold vs Active listings
    sold_count = db.query(SoldItem).filter(SoldItem.is_sold == True).count()
    active_count = db.query(SoldItem).filter(SoldItem.is_sold == False).count()

    # Price and Volume metrics
    clean_avg_price = db.query(func.avg(SoldItem.price)).filter(SoldItem.is_replica.isnot(True)).scalar() or 0.0
    sold_avg_price = db.query(func.avg(SoldItem.price)).filter(SoldItem.is_sold == True, SoldItem.is_replica.isnot(True)).scalar() or 0.0
    active_avg_price = db.query(func.avg(SoldItem.price)).filter(SoldItem.is_sold == False, SoldItem.is_replica.isnot(True)).scalar() or 0.0
    replica_avg_price = db.query(func.avg(SoldItem.price)).filter(SoldItem.is_replica == True).scalar() or 0.0
    total_volume_gbp = db.query(func.sum(SoldItem.price)).scalar() or 0.0
    sold_volume_gbp = db.query(func.sum(SoldItem.price)).filter(SoldItem.is_sold == True).scalar() or 0.0

    # Categorize replica reasons
    replica_reasons_rows = (
        db.query(SoldItem.replica_reason)
        .filter(SoldItem.is_replica == True, SoldItem.replica_reason != None)
        .all()
    )

    reason_counts = {
        "bnwt": {"category": "BNWT / Tag Keywords", "count": 0, "icon": "tag", "color": "amber"},
        "photo": {"category": "Duplicate / Stock Photo", "count": 0, "icon": "image", "color": "rose"},
        "multiple": {"category": "Multiple Seller Listings", "count": 0, "icon": "layers", "color": "purple"},
        "reviews": {"category": "Zero / Low Feedback", "count": 0, "icon": "user-x", "color": "blue"},
        "dormant": {"category": "Dormant / Acquired Account", "count": 0, "icon": "clock", "color": "orange"},
        "other": {"category": "Other Flag Reason", "count": 0, "icon": "alert-triangle", "color": "slate"},
    }

    for (r_text,) in replica_reasons_rows:
        if not r_text:
            continue
        lower = r_text.lower()
        matched_any = False
        if "photo" in lower or "image" in lower or "stock" in lower:
            reason_counts["photo"]["count"] += 1
            matched_any = True
        if "tag" in lower or "bnwt" in lower or "metka" in lower or "etiquette" in lower:
            reason_counts["bnwt"]["count"] += 1
            matched_any = True
        if "multiple" in lower or "limit is" in lower:
            reason_counts["multiple"]["count"] += 1
            matched_any = True
        if "review" in lower or "feedback" in lower:
            reason_counts["reviews"]["count"] += 1
            matched_any = True
        if "dormant" in lower or "acquired" in lower:
            reason_counts["dormant"]["count"] += 1
            matched_any = True
        if not matched_any:
            reason_counts["other"]["count"] += 1

    reasons_breakdown = []
    for key, data in reason_counts.items():
        if data["count"] > 0 or replica_items == 0:
            pct = round((data["count"] / replica_items * 100), 1) if replica_items else 0.0
            reasons_breakdown.append({
                "key": key,
                "label": data["category"],
                "count": data["count"],
                "pct": pct,
                "icon": data["icon"],
                "color": data["color"],
            })
    reasons_breakdown.sort(key=lambda x: x["count"], reverse=True)

    # Monthly breakdown of Clean vs Replica
    monthly_rows = (
        db.query(
            func.strftime("%Y-%m", SoldItem.captured_at).label("month_key"),
            func.count(SoldItem.id).label("total"),
            func.sum(case((SoldItem.is_replica == True, 1), else_=0)).label("replicas"),
            func.sum(case((SoldItem.is_replica.isnot(True), 1), else_=0)).label("clean"),
            func.round(func.avg(SoldItem.price), 2).label("avg_price"),
        )
        .filter(SoldItem.captured_at != None)
        .group_by("month_key")
        .order_by(func.strftime("%Y-%m", SoldItem.captured_at).desc())
        .all()
    )

    monthly_trend = []
    for row in monthly_rows:
        if not row.month_key:
            continue
        try:
            dt = datetime.strptime(row.month_key, "%Y-%m")
            label = dt.strftime("%B %Y")
        except Exception:
            label = row.month_key
        monthly_trend.append({
            "month_key": row.month_key,
            "label": label,
            "total": row.total or 0,
            "replicas": row.replicas or 0,
            "clean": row.clean or 0,
            "avg_price": float(row.avg_price or 0.0),
        })

    # Top brands breakdown
    top_brands_rows = (
        db.query(
            SoldItem.brand,
            func.count(SoldItem.id).label("total"),
            func.sum(case((SoldItem.is_replica == True, 1), else_=0)).label("replicas"),
        )
        .filter(SoldItem.brand != None, SoldItem.brand != "")
        .group_by(SoldItem.brand)
        .order_by(func.count(SoldItem.id).desc())
        .limit(10)
        .all()
    )
    top_brands = [
        {
            "brand": row.brand,
            "total": row.total or 0,
            "replicas": row.replicas or 0,
            "clean": (row.total or 0) - (row.replicas or 0),
        }
        for row in top_brands_rows
    ]

    # Searches breakdown
    from .variants import item_has_variant_clause

    searches = db.query(TrackedSearch).filter(TrackedSearch.is_active == True).all()
    search_stats = []
    for s in searches:
        t_cnt = db.query(SoldItem).filter(SoldItem.tracked_search_id == s.id).count()
        r_cnt = db.query(SoldItem).filter(SoldItem.tracked_search_id == s.id, SoldItem.is_replica == True).count()
        s_vars = (
            db.query(ProductVariant)
            .filter(ProductVariant.tracked_search_id == s.id)
            .order_by(ProductVariant.id)
            .all()
        )
        v_by_id = {v.id: v for v in s_vars}
        top_models = [v for v in s_vars if v.parent_variant_id is None or v.parent_variant_id not in v_by_id]
        ordered_s_vars = []
        for m in top_models:
            ordered_s_vars.append(m)
            for c in s_vars:
                if c.parent_variant_id == m.id:
                    ordered_s_vars.append(c)

        serialized_s_vars = []
        for v in ordered_s_vars:
            is_sub = v.parent_variant_id is not None and v.parent_variant_id in v_by_id
            parent_v = v_by_id.get(v.parent_variant_id) if is_sub else None
            v_cnt = (
                db.query(func.count(SoldItem.id))
                .filter(
                    SoldItem.tracked_search_id == s.id,
                    SoldItem.is_replica.isnot(True),
                    item_has_variant_clause(v.id),
                )
                .scalar()
                or 0
            )
            serialized_s_vars.append({
                "id": v.id,
                "parent_variant_id": v.parent_variant_id if is_sub else None,
                "parent_variant_name": parent_v.variant_name if parent_v else None,
                "is_sub_style": is_sub,
                "variant_name": v.variant_name,
                "dimension": "Style" if is_sub else "Model",
                "resale_tier": v.resale_tier or "STANDARD",
                "count": int(v_cnt),
            })

        search_stats.append({
            "id": s.id,
            "search_text": s.search_text,
            "total": t_cnt,
            "replicas": r_cnt,
            "clean": t_cnt - r_cnt,
            "variants_enabled": bool(getattr(s, "variants_enabled", False)),
            "variants": serialized_s_vars,
        })

    return {
        "total_items": total_items,
        "clean_items": clean_items,
        "replica_items": replica_items,
        "replica_rate_pct": replica_rate_pct,
        "child_items_count": child_items_count,
        "adult_items_count": adult_items_count,
        "sold_count": sold_count,
        "active_count": active_count,
        "clean_avg_price": round(float(clean_avg_price), 2),
        "sold_avg_price": round(float(sold_avg_price), 2),
        "active_avg_price": round(float(active_avg_price), 2),
        "replica_avg_price": round(float(replica_avg_price), 2),
        "total_volume_gbp": round(float(total_volume_gbp), 2),
        "sold_volume_gbp": round(float(sold_volume_gbp), 2),
        "reasons_breakdown": reasons_breakdown,
        "monthly_trend": monthly_trend,
        "top_brands": top_brands,
        "searches": search_stats,
    }


# ── Advanced Database Items Explorer with Specialized Filters ────────────────

@router.get("/items")
def get_database_items(
    q: Optional[str] = Query(None, description="Search keyword in title, brand, seller, variant, or ID"),
    search_id: Optional[int] = Query(None, description="Filter by tracked search ID"),
    variant_id: Optional[str] = Query(None, description="Filter by variant ID or 'unclassified'"),
    status_filter: Optional[str] = Query("all", description="Listing status: 'all', 'sold', 'active'"),
    replica_filter: Optional[str] = Query("all", description="Replica status: 'all', 'clean', 'replica'"),
    replica_reason: Optional[str] = Query(None, description="Filter by replica reason category: 'bnwt', 'photo', 'multiple', 'reviews', 'dormant'"),
    photo_key: Optional[str] = Query(None, description="Filter all items sharing a specific repeating photo fingerprint key"),
    child_filter: Optional[str] = Query("all", description="Size category: 'all', 'adult', 'child'"),
    month: Optional[str] = Query(None, description="Filter by month in YYYY-MM format"),
    min_price: Optional[float] = Query(None, ge=0, description="Minimum price"),
    max_price: Optional[float] = Query(None, ge=0, description="Maximum price"),
    sort_by: Optional[str] = Query("newest", description="'newest', 'oldest', 'price_asc', 'price_desc'"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    db: Session = Depends(get_db),
):
    """Retrieve filtered, paginated database items with complete replica and duplicate image diagnostics."""
    query = (
        db.query(SoldItem, TrackedSearch.search_text)
        .outerjoin(TrackedSearch, SoldItem.tracked_search_id == TrackedSearch.id)
    )

    # 0. Repeating Photo Fingerprint Filter (filters whole database by photo_key)
    normalized_photo_key = None
    if photo_key and photo_key.strip():
        raw_pk = photo_key.strip()
        normalized_photo_key = extract_photo_key(None, raw_pk) or raw_pk
        scraped_vinted_ids = [
            r.vinted_item_id
            for r in db.query(ScrapedPhotoRecord.vinted_item_id)
            .filter(ScrapedPhotoRecord.photo_key == normalized_photo_key)
            .all()
            if r.vinted_item_id
        ]
        if scraped_vinted_ids:
            query = query.filter(
                or_(
                    SoldItem.photo_url.ilike(f"%{normalized_photo_key}%"),
                    SoldItem.vinted_item_id.in_(scraped_vinted_ids),
                )
            )
        else:
            query = query.filter(SoldItem.photo_url.ilike(f"%{normalized_photo_key}%"))

    # 1. Search Query Text Filter
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                SoldItem.title.ilike(term),
                SoldItem.brand.ilike(term),
                SoldItem.seller_username.ilike(term),
                SoldItem.vinted_item_id.ilike(term),
                SoldItem.variant_name.ilike(term),
            )
        )

    # 2. Tracked Search & Multi-Category Variant Filter
    if search_id:
        query = query.filter(SoldItem.tracked_search_id == search_id)
    if variant_id and str(variant_id).strip().lower() not in ("", "all"):
        from .variants import build_variant_filter_clauses
        v_clauses = build_variant_filter_clauses(db, variant_id)
        for vc in v_clauses:
            query = query.filter(vc)

    # 3. Sold Status Filter
    if status_filter == "sold":
        query = query.filter(SoldItem.is_sold == True)
    elif status_filter == "active":
        query = query.filter(SoldItem.is_sold == False)

    # 4. Replica Filter
    if replica_filter == "clean":
        query = query.filter(SoldItem.is_replica.isnot(True))
    elif replica_filter == "replica":
        query = query.filter(SoldItem.is_replica == True)

    # 4b. Child Size Filter
    if child_filter == "adult":
        query = query.filter(SoldItem.is_child_size.isnot(True))
    elif child_filter == "child":
        query = query.filter(SoldItem.is_child_size == True)

    # 5. Replica Reason Category Filter
    if replica_reason and replica_reason.strip():
        r_cat = replica_reason.strip().lower()
        if r_cat == "bnwt":
            query = query.filter(
                SoldItem.is_replica == True,
                or_(
                    SoldItem.replica_reason.ilike("%tag%"),
                    SoldItem.replica_reason.ilike("%bnwt%"),
                    SoldItem.replica_reason.ilike("%metka%"),
                ),
            )
        elif r_cat == "photo":
            query = query.filter(
                SoldItem.is_replica == True,
                or_(
                    SoldItem.replica_reason.ilike("%photo%"),
                    SoldItem.replica_reason.ilike("%image%"),
                    SoldItem.replica_reason.ilike("%stock%"),
                ),
            )
        elif r_cat == "multiple":
            query = query.filter(
                SoldItem.is_replica == True,
                or_(
                    SoldItem.replica_reason.ilike("%multiple%"),
                    SoldItem.replica_reason.ilike("%limit is%"),
                ),
            )
        elif r_cat == "reviews":
            query = query.filter(
                SoldItem.is_replica == True,
                or_(
                    SoldItem.replica_reason.ilike("%review%"),
                    SoldItem.replica_reason.ilike("%feedback%"),
                ),
            )
        elif r_cat == "dormant":
            query = query.filter(
                SoldItem.is_replica == True,
                or_(
                    SoldItem.replica_reason.ilike("%dormant%"),
                    SoldItem.replica_reason.ilike("%acquired%"),
                ),
            )
        else:
            query = query.filter(
                SoldItem.is_replica == True,
                SoldItem.replica_reason.ilike(f"%{r_cat}%"),
            )

    # 6. Month Filter
    if month and month.strip():
        query = query.filter(func.strftime("%Y-%m", SoldItem.captured_at) == month.strip())

    # 7. Price Range Filter
    if min_price is not None:
        query = query.filter(SoldItem.price >= min_price)
    if max_price is not None:
        query = query.filter(SoldItem.price <= max_price)

    # Total Count before pagination
    total_count = query.count()

    # 8. Sorting
    if sort_by == "oldest":
        query = query.order_by(asc(SoldItem.captured_at))
    elif sort_by == "price_asc":
        query = query.order_by(asc(SoldItem.price))
    elif sort_by == "price_desc":
        query = query.order_by(desc(SoldItem.price))
    else:  # default 'newest'
        query = query.order_by(desc(SoldItem.captured_at))

    # 9. Pagination
    offset = (page - 1) * page_size
    rows = query.offset(offset).limit(page_size).all()

    # Pre-count photo keys across the entire database so each item knows if its image repeats
    all_photo_rows = db.query(SoldItem.photo_url).filter(SoldItem.photo_url != None).all()
    db_photo_counts: Dict[str, int] = {}
    for (p_url,) in all_photo_rows:
        pk_val = extract_photo_key(None, p_url)
        if pk_val:
            db_photo_counts[pk_val] = db_photo_counts.get(pk_val, 0) + 1

    items = []
    for item, s_text in rows:
        pk = extract_photo_key(None, item.photo_url)
        dup_count = db_photo_counts.get(pk, 1) if pk else 0
        reason_lower = (item.replica_reason or "").lower()
        flagged_for_photo = bool(
            item.is_replica
            and ("photo" in reason_lower or "image" in reason_lower or "stock" in reason_lower)
        )
        is_dup_photo = bool(pk and (dup_count > 1 or flagged_for_photo))

        items.append({
            "id": item.id,
            "vinted_item_id": item.vinted_item_id,
            "title": item.title,
            "price": item.price,
            "currency": item.currency,
            "brand": item.brand,
            "size": item.size,
            "condition": item.condition,
            "seller_id": item.seller_id,
            "seller_username": item.seller_username,
            "photo_url": item.photo_url,
            "photo_key": pk,
            "is_duplicate_photo": is_dup_photo,
            "duplicate_photo_count": dup_count,
            "url": item.url,
            "status": item.status,
            "is_sold": bool(item.is_sold),
            "is_replica": bool(item.is_replica),
            "replica_reason": item.replica_reason,
            "is_child_size": bool(item.is_child_size),
            "child_size_reason": item.child_size_reason,
            "variant_id": getattr(item, "variant_id", None),
            "variant_ids": item.parsed_variant_ids(),
            "variant_name": getattr(item, "variant_name", None),
            "variant_names": item.parsed_variant_names(),
            "variant_confidence": round(float(getattr(item, "variant_confidence", 0.0) or 0.0), 3),
            "variant_method": getattr(item, "variant_method", None),
            "variant_evidence": getattr(item, "variant_evidence", None),
            "tracked_search_id": item.tracked_search_id,
            "search_text": s_text or f"Search #{item.tracked_search_id}",
            "captured_at": item.captured_at.isoformat() if item.captured_at else None,
        })

    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1

    return {
        "items": items,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "active_photo_key": normalized_photo_key,
    }


# ── Replica Monthly Management & Pruning ────────────────────────────────────

@router.get("/replicas/monthly")
def get_replica_monthly_breakdown(db: Session = Depends(get_db)):
    """Return month-by-month replica counts for the database management cleanup section."""
    rows = (
        db.query(
            func.strftime("%Y-%m", SoldItem.captured_at).label("month_key"),
            func.count(SoldItem.id).label("total_items"),
            func.sum(case((SoldItem.is_replica == True, 1), else_=0)).label("replica_count"),
            func.sum(case((SoldItem.is_replica.isnot(True), 1), else_=0)).label("clean_count"),
            func.round(func.avg(SoldItem.price), 2).label("avg_price"),
        )
        .filter(SoldItem.captured_at != None)
        .group_by("month_key")
        .order_by(func.strftime("%Y-%m", SoldItem.captured_at).desc())
        .all()
    )

    months = []
    for row in rows:
        if not row.month_key:
            continue
        try:
            dt = datetime.strptime(row.month_key, "%Y-%m")
            label = dt.strftime("%B %Y")
        except Exception:
            label = row.month_key

        months.append({
            "month_key": row.month_key,
            "label": label,
            "replica_count": int(row.replica_count or 0),
            "clean_count": int(row.clean_count or 0),
            "total_items": int(row.total_items or 0),
            "avg_price": float(row.avg_price or 0.0),
        })

    return {
        "months": months,
        "total_replicas": db.query(SoldItem).filter(SoldItem.is_replica == True).count(),
    }


@router.post("/replicas/delete-month")
def delete_replica_month_data(data: DeleteReplicaMonthRequest, db: Session = Depends(get_db)):
    """Permanently delete ONLY replica items belonging to a specific month, preserving legitimate sales."""
    query = db.query(SoldItem).filter(
        func.strftime("%Y-%m", SoldItem.captured_at) == data.month,
        SoldItem.is_replica == True,
    )
    if data.search_id:
        query = query.filter(SoldItem.tracked_search_id == data.search_id)

    deleted_count = query.delete(synchronize_session=False)
    db.commit()

    remaining_replicas = db.query(SoldItem).filter(SoldItem.is_replica == True).count()
    remaining_total = db.query(SoldItem).count()

    try:
        dt = datetime.strptime(data.month, "%Y-%m")
        month_label = dt.strftime("%B %Y")
    except Exception:
        month_label = data.month

    return {
        "ok": True,
        "deleted_count": deleted_count,
        "remaining_replicas": remaining_replicas,
        "remaining_total": remaining_total,
        "month": data.month,
        "message": f"Successfully purged {deleted_count} caught replicas from {month_label}. Clean market sales were preserved.",
    }


@router.post("/replicas/purge-all")
def purge_all_replicas(db: Session = Depends(get_db)):
    """Permanently delete all caught replica items across all searches and dates."""
    deleted_count = (
        db.query(SoldItem)
        .filter(SoldItem.is_replica == True)
        .delete(synchronize_session=False)
    )
    db.commit()

    remaining_total = db.query(SoldItem).count()
    return {
        "ok": True,
        "deleted_count": deleted_count,
        "remaining_total": remaining_total,
        "message": f"Purged all {deleted_count} caught replicas from database.",
    }


@router.post("/rescan-replicas")
def rescan_database_replicas(db: Session = Depends(get_db)):
    """Audit all stored items against anti-rep detection heuristics (duplicate photos, BNWT keywords, multiple listings)."""
    items = db.query(SoldItem).all()
    total_inspected = len(items)
    newly_flagged = 0
    now_clean = 0

    searches_map: Dict[int, dict] = {
        s.id: s.antirep_config for s in db.query(TrackedSearch).all()
    }

    # Build map of seller item counts per search and photo fingerprint clusters across entire DB
    seller_counts: Dict[tuple, int] = {}
    photo_clusters: Dict[str, List[str]] = {}
    item_photo_keys: Dict[int, Optional[str]] = {}

    for item in items:
        if item.seller_id:
            key = (item.tracked_search_id, str(item.seller_id))
            seller_counts[key] = seller_counts.get(key, 0) + 1

        pk = extract_photo_key(None, item.photo_url)
        item_photo_keys[item.id] = pk
        if pk:
            vid = str(item.vinted_item_id)
            if pk not in photo_clusters:
                photo_clusters[pk] = []
            if vid not in photo_clusters[pk]:
                photo_clusters[pk].append(vid)
            record_photo_fingerprint(
                db=db,
                photo_key=pk,
                photo_url=item.photo_url,
                vinted_item_id=vid,
                seller_id=item.seller_id,
                tracked_search_id=item.tracked_search_id,
            )

    for item in items:
        was_replica = bool(item.is_replica)
        prev_reason = item.replica_reason or ""
        s_cfg = searches_map.get(item.tracked_search_id, {})

        is_bnwt, bnwt_reason = is_new_with_tags(item.condition, item.title or "")

        # Check duplicate photo across SoldItem and ScrapedPhotoRecord
        pk = item_photo_keys.get(item.id)
        is_dup_photo = False
        dup_reason = ""
        if pk:
            cluster_ids = [vid for vid in photo_clusters.get(pk, []) if vid != str(item.vinted_item_id)]
            if cluster_ids:
                is_dup_photo = True
                total_shared = len(photo_clusters.get(pk, []))
                dup_reason = f"Duplicate / stock photo detected (shared across {total_shared} listings, e.g. #{cluster_ids[0]})"
            else:
                dup_rec = (
                    db.query(ScrapedPhotoRecord)
                    .filter(
                        ScrapedPhotoRecord.photo_key == pk,
                        ScrapedPhotoRecord.vinted_item_id != str(item.vinted_item_id),
                    )
                    .first()
                )
                if dup_rec:
                    is_dup_photo = True
                    dup_reason = f"Duplicate / stock photo detected (seen in #{dup_rec.vinted_item_id})"

        reasons_list: List[str] = []
        if is_dup_photo and s_cfg.get("block_duplicate_photos", True):
            reasons_list.append(dup_reason)
        if is_bnwt and s_cfg.get("block_new_with_tags", True):
            reasons_list.append(f"New with tags item blocked: {bnwt_reason}")

        if item.seller_id and s_cfg.get("block_multiple_seller_listings", True):
            max_l = int(s_cfg.get("max_seller_listings", 2) or 2)
            s_cnt = seller_counts.get((item.tracked_search_id, str(item.seller_id)), 0)
            if s_cnt > max_l:
                reasons_list.append(f"Seller is listing multiple similar items ({s_cnt} listings, limit is {max_l})")

        # Preserve prior seller review or dormant flags if the search still has those rules enabled
        if not reasons_list and was_replica and prev_reason:
            low_prev = prev_reason.lower()
            if ("review" in low_prev or "feedback" in low_prev) and s_cfg.get("block_no_reviews", True):
                reasons_list.append(prev_reason)
            elif ("dormant" in low_prev or "acquired" in low_prev) and s_cfg.get("block_dormant_accounts", False):
                reasons_list.append(prev_reason)

        if reasons_list:
            item.is_replica = True
            item.replica_reason = " • ".join(reasons_list)
            if not was_replica:
                newly_flagged += 1
        else:
            if was_replica:
                item.is_replica = False
                item.replica_reason = None
                now_clean += 1

        # Also keep child size classification synchronized
        is_child, child_reason = is_child_size(item.title, item.size)
        item.is_child_size = is_child
        item.child_size_reason = child_reason

    db.commit()
    total_replicas = db.query(SoldItem).filter(SoldItem.is_replica == True).count()

    return {
        "ok": True,
        "total_inspected": total_inspected,
        "newly_flagged": newly_flagged,
        "cleared": now_clean,
        "total_replicas": total_replicas,
        "message": f"Rescanned {total_inspected} items. Found {total_replicas} total replicas ({newly_flagged} newly flagged).",
    }


@router.delete("/items/{item_id}")
def delete_single_item(item_id: int, db: Session = Depends(get_db)):
    """Delete a single individual item entry from the database."""
    item = db.query(SoldItem).filter(SoldItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    title = item.title
    db.delete(item)
    db.commit()

    return {
        "ok": True,
        "deleted_id": item_id,
        "message": f"Successfully deleted item '{title}' (ID: {item_id}).",
    }


@router.post("/items/{item_id}/toggle-sold")
def toggle_item_sold(
    item_id: int,
    sold_price: Optional[float] = Query(None, description="Optional final sale price override"),
    db: Session = Depends(get_db),
):
    """Toggle the is_sold status of an individual item between True and False, ensuring exact final sale price."""
    from ..services.status_checker import fetch_live_item_price

    item = db.query(SoldItem).filter(SoldItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    old_price = float(item.price or 0.0)
    was_sold = bool(item.is_sold)
    item.is_sold = not was_sold
    item.status = "sold" if item.is_sold else "active"

    price_updated = False
    if item.is_sold:
        if sold_price is not None and sold_price > 0:
            item.price = round(float(sold_price), 2)
            price_updated = True
        elif item.vinted_item_id:
            live_p, _ = fetch_live_item_price(item.vinted_item_id)
            if live_p is not None and live_p > 0 and abs(live_p - old_price) >= 0.01:
                item.price = live_p
                price_updated = True

    db.commit()

    price_note = f" (Price updated: £{old_price:.2f} → £{item.price:.2f})" if price_updated else f" (£{item.price:.2f})"
    return {
        "ok": True,
        "item_id": item_id,
        "is_sold": bool(item.is_sold),
        "status": item.status,
        "price": float(item.price or 0.0),
        "old_price": old_price,
        "price_updated": price_updated,
        "message": f"Item #{item_id} marked as {item.status.upper()}{price_note}.",
    }


@router.post("/items/{item_id}/update-price")
def update_item_price(
    item_id: int,
    price: float = Query(..., ge=0.01, description="New price in GBP"),
    db: Session = Depends(get_db),
):
    """Update or mark down an item's exact listed or sold price."""
    item = db.query(SoldItem).filter(SoldItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    old_price = float(item.price or 0.0)
    item.price = round(float(price), 2)
    db.commit()

    return {
        "ok": True,
        "item_id": item_id,
        "old_price": old_price,
        "price": item.price,
        "message": f"Item #{item_id} price updated: £{old_price:.2f} → £{item.price:.2f}.",
    }


@router.post("/items/{item_id}/verify-status")
def verify_item_status_endpoint(item_id: int, db: Session = Depends(get_db)):
    """Perform a live diagnostic check against Vinted to verify if item is actively available or sold."""
    from ..services.status_checker import audit_and_sync_item_status

    res = audit_and_sync_item_status(db, item_id)
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail=res.get("error", "Item not found"))
    return res


@router.post("/verify-sold-statuses")
def batch_verify_sold_statuses(
    batch_size: int = Query(0, ge=0, le=10000, description="Max items to audit (0 = all items)"),
    search_id: Optional[int] = Query(None, description="Optional search ID to restrict audit to"),
    only_active: bool = Query(True, description="Only audit items currently marked active"),
    db: Session = Depends(get_db),
):
    """Verify live sold/active status and exact listed/sold prices against Vinted UK."""
    from ..services.status_checker import batch_audit_and_sync_statuses

    effective_batch = batch_size if batch_size and batch_size > 0 else None
    return batch_audit_and_sync_statuses(
        db=db,
        batch_size=effective_batch,
        search_id=search_id,
        only_unverified_or_active=only_active,
        max_workers=8,
    )


@router.post("/items/{item_id}/toggle-replica")
def toggle_item_replica(item_id: int, db: Session = Depends(get_db)):
    """Manually toggle an item's replica verification status."""
    item = db.query(SoldItem).filter(SoldItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    item.is_replica = not bool(item.is_replica)
    if item.is_replica:
        item.replica_reason = "Manually flagged by user"
    else:
        item.replica_reason = None
    db.commit()

    return {
        "ok": True,
        "item_id": item_id,
        "is_replica": bool(item.is_replica),
        "replica_reason": item.replica_reason,
        "message": f"Item #{item_id} updated: {'Flagged as replica' if item.is_replica else 'Marked as clean'}.",
    }


# ── General Database Maintenance & Summary ──────────────────────────────────

@router.get("/summary")
def get_database_summary(db: Session = Depends(get_db)):
    """Return comprehensive database health metrics, monthly slices, and file statistics."""
    total_items = db.query(SoldItem).count()
    total_searches = db.query(TrackedSearch).filter(TrackedSearch.is_active == True).count()

    # Size on disk
    db_size_mb = 0.0
    try:
        if os.path.exists(DB_PATH):
            db_size_mb = round(os.path.getsize(DB_PATH) / (1024 * 1024), 2)
    except Exception:
        pass

    # Monthly breakdown using SQLite strftime
    monthly_rows = (
        db.query(
            func.strftime("%Y-%m", SoldItem.captured_at).label("month_key"),
            func.count(SoldItem.id).label("count"),
            func.avg(SoldItem.price).label("avg_price"),
            func.count(func.distinct(SoldItem.tracked_search_id)).label("searches_count"),
        )
        .filter(SoldItem.captured_at != None)
        .group_by("month_key")
        .order_by(func.strftime("%Y-%m", SoldItem.captured_at).desc())
        .all()
    )

    months = []
    for row in monthly_rows:
        if not row.month_key:
            continue
        try:
            dt = datetime.strptime(row.month_key, "%Y-%m")
            label = dt.strftime("%B %Y")
        except Exception:
            label = row.month_key

        months.append({
            "month_key": row.month_key,
            "label": label,
            "count": row.count or 0,
            "avg_price": round(float(row.avg_price or 0.0), 2),
            "searches_count": row.searches_count or 0,
        })

    # Search breakdown
    searches = db.query(TrackedSearch).filter(TrackedSearch.is_active == True).all()
    search_stats = []
    for s in searches:
        count = db.query(SoldItem).filter(SoldItem.tracked_search_id == s.id).count()
        search_stats.append({
            "id": s.id,
            "search_text": s.search_text,
            "count": count,
        })

    return {
        "total_items": total_items,
        "total_searches": total_searches,
        "db_size_mb": db_size_mb,
        "months": months,
        "searches": search_stats,
    }


@router.post("/delete-month")
def delete_month_data(data: DeleteMonthRequest, db: Session = Depends(get_db)):
    """Delete all recorded items belonging to a specific month (e.g. '2026-10')."""
    query = db.query(SoldItem).filter(
        func.strftime("%Y-%m", SoldItem.captured_at) == data.month
    )
    if data.search_id:
        query = query.filter(SoldItem.tracked_search_id == data.search_id)

    deleted_count = query.delete(synchronize_session=False)
    db.commit()

    remaining_total = db.query(SoldItem).count()

    try:
        dt = datetime.strptime(data.month, "%Y-%m")
        month_label = dt.strftime("%B %Y")
    except Exception:
        month_label = data.month

    return {
        "ok": True,
        "deleted_count": deleted_count,
        "remaining_total": remaining_total,
        "month": data.month,
        "message": f"Successfully purged {deleted_count} recorded items from {month_label}.",
    }


@router.post("/prune-older-than")
def prune_older_data(data: PruneOlderRequest, db: Session = Depends(get_db)):
    """Delete all records older than a specified number of days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=data.older_than_days)
    query = db.query(SoldItem).filter(SoldItem.captured_at < cutoff)
    if data.search_id:
        query = query.filter(SoldItem.tracked_search_id == data.search_id)

    deleted_count = query.delete(synchronize_session=False)
    db.commit()

    remaining_total = db.query(SoldItem).count()
    return {
        "ok": True,
        "deleted_count": deleted_count,
        "remaining_total": remaining_total,
        "message": f"Purged {deleted_count} items older than {data.older_than_days} days.",
    }


@router.post("/clear-search-data")
def clear_search_data(data: ClearSearchDataRequest, db: Session = Depends(get_db)):
    """Clear all recorded items for a specific tracked search."""
    deleted_count = (
        db.query(SoldItem)
        .filter(SoldItem.tracked_search_id == data.search_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    remaining_total = db.query(SoldItem).count()
    return {
        "ok": True,
        "deleted_count": deleted_count,
        "remaining_total": remaining_total,
        "message": f"Cleared {deleted_count} records for search ID {data.search_id}.",
    }

