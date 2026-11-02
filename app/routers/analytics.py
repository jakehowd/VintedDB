"""Analytics / aggregation endpoints for charting and summaries."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import SoldItem, TrackedSearch, User
from .variants import build_variant_filter_clauses

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# strftime format strings for each supported time period
_PERIOD_FMT = {
    "day": "%Y-%m-%d",
    "week": "%Y-W%W",
    "month": "%Y-%m",
    "year": "%Y",
}


def _apply_variant_filter(db: Session, filters: list, variant_id: Optional[str]) -> None:
    clauses = build_variant_filter_clauses(db, variant_id)
    if clauses:
        filters.extend(clauses)


@router.get("/{search_id}/sales-over-time")
def sales_over_time(
    search_id: int,
    period: str = Query("week", pattern="^(day|week|month|year)$"),
    variant_id: Optional[str] = Query(None, description="Optional variant ID or 'unclassified'"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aggregate item count + price stats per time bucket."""
    search = (
        db.query(TrackedSearch)
        .filter(TrackedSearch.id == search_id, TrackedSearch.user_id == current_user.id)
        .first()
    )
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")

    fmt = _PERIOD_FMT.get(period, _PERIOD_FMT["week"])
    period_label = func.strftime(fmt, SoldItem.captured_at)

    filters = [SoldItem.tracked_search_id == search_id, SoldItem.is_replica.isnot(True)]
    if getattr(search, "exclude_child_sizes", False):
        filters.append(SoldItem.is_child_size.isnot(True))
    _apply_variant_filter(db, filters, variant_id)

    rows = (
        db.query(
            period_label.label("period_label"),
            func.count(SoldItem.id).label("count"),
            func.round(func.avg(SoldItem.price), 2).label("avg_price"),
            func.min(SoldItem.price).label("min_price"),
            func.max(SoldItem.price).label("max_price"),
            func.round(func.sum(SoldItem.price), 2).label("total_revenue"),
        )
        .filter(*filters)
        .group_by("period_label")
        .order_by("period_label")
        .all()
    )

    return {
        "search_id": search_id,
        "search_text": search.search_text,
        "period": period,
        "data": [
            {
                "period_label": r.period_label or "",
                "count": r.count,
                "avg_price": r.avg_price or 0,
                "min_price": r.min_price or 0,
                "max_price": r.max_price or 0,
                "total_revenue": r.total_revenue or 0,
            }
            for r in rows
        ],
    }


@router.get("/{search_id}/summary")
def get_summary(
    search_id: int,
    variant_id: Optional[str] = Query(None, description="Optional variant ID or 'unclassified'"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Overall statistics for a tracked search."""
    search = (
        db.query(TrackedSearch)
        .filter(TrackedSearch.id == search_id, TrackedSearch.user_id == current_user.id)
        .first()
    )
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")

    filters = [SoldItem.tracked_search_id == search_id, SoldItem.is_replica.isnot(True)]
    if getattr(search, "exclude_child_sizes", False):
        filters.append(SoldItem.is_child_size.isnot(True))
    _apply_variant_filter(db, filters, variant_id)

    stats = (
        db.query(
            func.count(SoldItem.id),
            func.round(func.avg(SoldItem.price), 2),
            func.min(SoldItem.price),
            func.max(SoldItem.price),
            func.min(SoldItem.captured_at),
            func.max(SoldItem.captured_at),
        )
        .filter(*filters)
        .first()
    )

    sold_stats = (
        db.query(
            func.count(SoldItem.id),
            func.round(func.avg(SoldItem.price), 2),
            func.min(SoldItem.price),
            func.max(SoldItem.price),
            func.round(func.sum(SoldItem.price), 2),
        )
        .filter(*filters, SoldItem.is_sold == True)
        .first()
    )

    active_stats = (
        db.query(
            func.count(SoldItem.id),
            func.round(func.avg(SoldItem.price), 2),
        )
        .filter(*filters, SoldItem.is_sold == False)
        .first()
    )

    sold_count = sold_stats[0] or 0
    sold_avg = float(sold_stats[1] or 0.0)
    sold_min = float(sold_stats[2] or 0.0)
    sold_max = float(sold_stats[3] or 0.0)
    sold_gross = float(sold_stats[4] or 0.0)

    # Average Sold Price is strictly the mean of items that have actually sold.
    # If no sold items exist yet, avg_price reports 0.0 to avoid inaccurate unsold listing distortion.
    effective_avg_sold = sold_avg if sold_count > 0 else 0.0

    return {
        "search_id": search_id,
        "search_text": search.search_text,
        "total_items": stats[0] or 0,
        "sold_count": sold_count,
        "active_count": active_stats[0] or 0,
        "avg_price": effective_avg_sold,
        "sold_avg_price": sold_avg,
        "active_avg_price": float(active_stats[1] or 0.0),
        "min_price": sold_min if sold_count > 0 else (float(stats[2] or 0.0)),
        "max_price": sold_max if sold_count > 0 else (float(stats[3] or 0.0)),
        "sold_gross_value": sold_gross,
        "total_gross_value": round((stats[0] or 0) * (float(stats[1] or 0.0)), 2),
        "first_seen": stats[4].isoformat() if stats[4] else None,
        "last_seen": stats[5].isoformat() if stats[5] else None,
    }


@router.get("/{search_id}/recent-sales")
def recent_sales(
    search_id: int,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    variant_id: Optional[str] = Query(None, description="Optional variant ID or 'unclassified'"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Paginated list of recent individual items."""
    from ..services.antirep import extract_photo_key

    search = (
        db.query(TrackedSearch)
        .filter(TrackedSearch.id == search_id, TrackedSearch.user_id == current_user.id)
        .first()
    )
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")

    filters = [SoldItem.tracked_search_id == search_id]
    if getattr(search, "exclude_child_sizes", False):
        filters.append(SoldItem.is_child_size.isnot(True))
    _apply_variant_filter(db, filters, variant_id)

    items = (
        db.query(SoldItem)
        .filter(*filters)
        .order_by(SoldItem.captured_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    total = (
        db.query(func.count(SoldItem.id))
        .filter(*filters)
        .scalar()
    )

    all_photo_rows = db.query(SoldItem.photo_url).filter(SoldItem.photo_url != None).all()
    db_photo_counts: dict[str, int] = {}
    for (p_url,) in all_photo_rows:
        pk_val = extract_photo_key(None, p_url)
        if pk_val:
            db_photo_counts[pk_val] = db_photo_counts.get(pk_val, 0) + 1

    out_items = []
    for item in items:
        pk = extract_photo_key(None, item.photo_url)
        dup_count = db_photo_counts.get(pk, 1) if pk else 0
        reason_lower = (item.replica_reason or "").lower()
        flagged_for_photo = bool(
            item.is_replica
            and ("photo" in reason_lower or "image" in reason_lower or "stock" in reason_lower)
        )
        is_dup_photo = bool(pk and (dup_count > 1 or flagged_for_photo))

        out_items.append(
            {
                "id": item.id,
                "vinted_item_id": item.vinted_item_id,
                "title": item.title,
                "price": item.price,
                "currency": item.currency,
                "brand": item.brand,
                "size": item.size,
                "photo_url": item.photo_url,
                "photo_key": pk,
                "is_duplicate_photo": is_dup_photo,
                "duplicate_photo_count": dup_count,
                "url": item.url,
                "is_sold": bool(item.is_sold),
                "status": item.status or ("sold" if item.is_sold else "active"),
                "is_replica": bool(item.is_replica),
                "replica_reason": item.replica_reason,
                "is_child_size": bool(getattr(item, "is_child_size", False)),
                "child_size_reason": getattr(item, "child_size_reason", None),
                "variant_id": getattr(item, "variant_id", None),
                "variant_ids": item.parsed_variant_ids(),
                "variant_name": getattr(item, "variant_name", None),
                "variant_names": item.parsed_variant_names(),
                "variant_confidence": round(float(getattr(item, "variant_confidence", 0.0) or 0.0), 3),
                "variant_method": getattr(item, "variant_method", None),
                "variant_evidence": getattr(item, "variant_evidence", None),
                "captured_at": (
                    item.captured_at.isoformat() if item.captured_at else None
                ),
            }
        )

    return {
        "search_id": search_id,
        "total": total or 0,
        "items": out_items,
    }



@router.get("/overview")
def get_overview(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dashboard overview — summary stats for every active tracked search belonging to the user."""
    searches = (
        db.query(TrackedSearch)
        .filter(TrackedSearch.is_active.is_(True), TrackedSearch.user_id == current_user.id)
        .all()
    )

    overview = []
    for search in searches:
        stats = (
            db.query(
                func.count(SoldItem.id),
                func.round(func.avg(SoldItem.price), 2),
                func.min(SoldItem.price),
                func.max(SoldItem.price),
            )
            .filter(SoldItem.tracked_search_id == search.id, SoldItem.is_replica.isnot(True))
            .first()
        )
        overview.append(
            {
                "search_id": search.id,
                "search_text": search.search_text,
                "total_items": stats[0] or 0,
                "avg_price": stats[1] or 0,
                "min_price": stats[2] or 0,
                "max_price": stats[3] or 0,
            }
        )

    return overview
