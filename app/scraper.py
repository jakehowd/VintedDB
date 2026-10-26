"""Vinted scraper — fetches items and stores new ones in the database."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from .config import VINTED_BASE_URL
from .models import ProductVariant, SoldItem, TrackedSearch

from .services.item_classifier import is_child_size, is_item_relevant, normalize_categories

logger = logging.getLogger(__name__)

import os

_shared_scraper = None


def _get_vinted_scraper():
    """Get or initialize a shared VintedScraper instance."""
    global _shared_scraper
    if _shared_scraper is not None:
        return _shared_scraper

    from vinted_scraper import VintedScraper
    from vinted_scraper.models import VintedSession

    vinted_cookie = os.environ.get("VINTED_COOKIE") or os.environ.get("VINTED_ACCESS_TOKEN")
    if vinted_cookie:
        cookies = {}
        if "=" in vinted_cookie:
            for part in vinted_cookie.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    cookies[k] = v
        else:
            cookies["access_token_web"] = vinted_cookie.strip()
        session = VintedSession(cookies=cookies)
        _shared_scraper = VintedScraper(VINTED_BASE_URL, session=session)
    else:
        _shared_scraper = VintedScraper(VINTED_BASE_URL)
    return _shared_scraper


def _search_vinted(search_text: str, page: int = 1, per_page: int = 96) -> list:
    """Call Vinted search using Anti-Ban Chrome TLS client with proxy and cookie support."""
    params: dict[str, Any] = {
        "search_text": search_text,
        "order": "newest_first",
        "page": str(page),
        "per_page": str(per_page),
    }

    last_exc = None

    # Priority 1: Anti-Ban Chrome TLS Client (curl_cffi) with proxy pooling & cookie injection
    try:
        from .services.http_client import antiban_client, VintedBlockedException, VintedRateLimitException
        items = antiban_client.search_catalog(
            search_text=search_text,
            page=page,
            per_page=per_page,
            order="newest_first",
        )
        if items is not None:
            return list(items)
    except Exception as exc:
        last_exc = exc
        logger.debug("AntiBanClient search failed, checking fallbacks: %s", exc)
        from .services.http_client import VintedBlockedException, VintedRateLimitException
        # If circuit breaker is active or blocked, don't spam fallbacks with raw httpx
        if isinstance(exc, (VintedBlockedException, VintedRateLimitException)):
            raise exc

    global _shared_scraper

    # Priority 2: Cached / configured VintedScraper instance
    try:
        scraper = _get_vinted_scraper()
        items = scraper.search(params)
        if items is not None:
            return list(items)
    except Exception as exc:
        last_exc = exc
        logger.debug("VintedScraper search failed: %s", exc)
        _shared_scraper = None

    # Priority 3: Top-level search() if available
    try:
        import vinted_scraper  # type: ignore

        if hasattr(vinted_scraper, "search"):
            items = vinted_scraper.search(f"{VINTED_BASE_URL}/catalog", params)
            if items is not None:
                return list(items)
    except Exception as exc:
        logger.debug("Approach 3 (vinted_scraper.search) failed: %s", exc)

    if last_exc:
        raise last_exc

    return []


# ---------------------------------------------------------------------------
# Item data extraction — handles both dict-like and object-like results
# ---------------------------------------------------------------------------

def _parse_price(raw: Any) -> float:
    """Coerce various price representations into a float."""
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        cleaned = raw.replace("£", "").replace("€", "").replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    if isinstance(raw, dict):
        return float(raw.get("amount", 0))
    return 0.0


def _get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Try multiple attribute / key names and return the first truthy value."""
    for key in keys:
        # dict-like
        if isinstance(obj, dict):
            val = obj.get(key)
        else:
            val = getattr(obj, key, None)
        if val is not None:
            return val
    return default


def _extract_photo(obj: Any) -> str | None:
    """Pull a thumbnail URL from whichever field the scraper provides."""
    # Direct URL fields
    for field in ("photo", "image_url", "thumbnail_url", "img"):
        val = _get(obj, field)
        if val and isinstance(val, str):
            return val

    # Nested photo object
    photo_obj = _get(obj, "photo", "photos")
    if isinstance(photo_obj, dict):
        return photo_obj.get("url") or photo_obj.get("full_size_url")
    if isinstance(photo_obj, list) and photo_obj:
        first = photo_obj[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get("url") or first.get("full_size_url")

    return None


def _extract_brand(raw_item: Any) -> str:
    """Extract brand name, supporting both legacy and new API formats."""
    brand = _get(raw_item, "brand_title", "brand")
    if not brand:
        item_box = _get(raw_item, "item_box")
        if isinstance(item_box, dict):
            brand = item_box.get("first_line")
    return str(brand or "").strip()


def _extract_size(raw_item: Any) -> str:
    """Extract size, supporting both legacy and new API formats."""
    size = _get(raw_item, "size_title", "size")
    if not size:
        item_box = _get(raw_item, "item_box")
        if isinstance(item_box, dict):
            second_line = item_box.get("second_line") or ""
            if second_line:
                # e.g. "L / UK 16-18 · Very good" -> "L / UK 16-18" or "M" -> "M"
                size = second_line.split("·")[0].strip()
    return str(size or "").strip()


def _extract_condition(raw_item: Any) -> str:
    """Extract item condition string (e.g. 'Very good', 'New with tags')."""
    item_box = _get(raw_item, "item_box")
    if isinstance(item_box, dict):
        second_line = item_box.get("second_line") or ""
        if "·" in second_line:
            return second_line.split("·")[-1].strip()
        label = item_box.get("accessibility_label") or ""
        if "Condition:" in label:
            try:
                part = label.split("Condition:")[1].split(",")[0].strip()
                if part:
                    return part
            except Exception:
                pass
        if second_line:
            return second_line.strip()
    status = _get(raw_item, "status")
    return str(status or "").strip()


def _extract_seller(raw_item: Any) -> tuple[str, str]:
    """Extract seller user_id and username."""
    user = _get(raw_item, "user")
    if isinstance(user, dict):
        sid = str(user.get("id") or "").strip()
        sname = str(user.get("login") or "").strip()
        return sid, sname
    if user is not None:
        sid = str(getattr(user, "id", "") or "").strip()
        sname = str(getattr(user, "login", "") or "").strip()
        return sid, sname
    return "", ""


def _extract_item(raw_item: Any) -> dict | None:
    """Normalise a single item from vinted-scraper into a flat dict."""
    item_id = _get(raw_item, "id", "item_id")
    if item_id is None:
        return None

    # Handle currency if nested in price dict
    currency = _get(raw_item, "currency", "currency_code")
    if not currency:
        price_obj = _get(raw_item, "price", "total_item_price")
        if isinstance(price_obj, dict):
            currency = price_obj.get("currency_code")

    seller_id, seller_username = _extract_seller(raw_item)
    condition = _extract_condition(raw_item)

    desc = str(_get(raw_item, "description", "body", "summary", default="") or "").strip()
    if not desc:
        box = _get(raw_item, "item_box")
        if isinstance(box, dict):
            desc = str(box.get("accessibility_label", "") or "").strip()

    return {
        "id": str(item_id),
        "title": str(_get(raw_item, "title", default="") or ""),
        "description": desc,
        "price": _parse_price(_get(raw_item, "price", "total_item_price", default=0)),
        "currency": str(currency or "GBP"),
        "brand": _extract_brand(raw_item),
        "size": _extract_size(raw_item),
        "condition": condition,
        "seller_id": seller_id,
        "seller_username": seller_username,
        "photo_url": _extract_photo(raw_item),
        "raw_photo": _get(raw_item, "photo"),
        "promoted": bool(_get(raw_item, "promoted", default=False)),
        "url": (
            str(_get(raw_item, 'url', 'path', default='') or '').strip()
            if str(_get(raw_item, 'url', 'path', default='') or '').strip().startswith(('http://', 'https://'))
            else (
                f"{VINTED_BASE_URL.rstrip('/')}/{str(_get(raw_item, 'url', 'path', default='') or '').strip().lstrip('/')}"
                if str(_get(raw_item, 'url', 'path', default='') or '').strip()
                else f"{VINTED_BASE_URL.rstrip('/')}/items/{item_id}"
            )
        ),
        "raw_item": raw_item,
    }



# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_items(search_text: str, max_pages: int = 3) -> list[dict]:
    """Fetch items from Vinted for a given search term.

    Returns a list of normalised item dicts.
    """
    all_items: list[dict] = []
    first_page_err = None

    for page in range(1, max_pages + 1):
        try:
            raw_items = _search_vinted(search_text, page=page)
            if not raw_items:
                break

            for raw in raw_items:
                item = _extract_item(raw)
                if item:
                    all_items.append(item)

            logger.info(
                "Page %d for '%s': got %d items", page, search_text, len(raw_items)
            )
            # Rate-limit between pages
            time.sleep(2)

        except Exception as exc:
            logger.exception("Error fetching page %d for '%s'", page, search_text)
            if page == 1:
                first_page_err = exc
            break

    if not all_items and first_page_err:
        raise first_page_err

    return all_items


def scrape_and_store(
    db: Session,
    tracked_search: TrackedSearch,
    return_details: bool = False,
) -> int | dict:
    """Scrape Vinted for *tracked_search* and persist any new items.

    Enforces all configured brand, category, exclusion, and anti-rep filters.
    Returns new_count (int), or a detailed dictionary if return_details=True.
    """
    from .services.scraper_logger import scraper_logger

    scraper_logger.log_scan_start(tracked_search.search_text, source="scraper")
    items = fetch_items(tracked_search.search_text)
    new_count = 0
    filtered_spam_count = 0
    filtered_antirep_count = 0
    seen_ids: set[str] = set()

    target_categories = normalize_categories(tracked_search.category)
    antirep_config = tracked_search.antirep_config
    scraper = _get_vinted_scraper()
    seller_batch_counts: dict[str, int] = {}

    active_variants: list[ProductVariant] = []
    if getattr(tracked_search, "variants_enabled", False):
        active_variants = (
            db.query(ProductVariant)
            .filter(ProductVariant.tracked_search_id == tracked_search.id)
            .order_by(ProductVariant.id)
            .all()
        )
    from .services.variant_vision import variant_vision_service

    updated_existing = False
    for item_data in items:
        vinted_id = item_data.get("id")
        if not vinted_id or vinted_id in seen_ids:
            continue
        seen_ids.add(vinted_id)

        # If item already exists in DB, keep its current price, seller metadata, and active status synced
        existing_item = (
            db.query(SoldItem)
            .filter(SoldItem.vinted_item_id == vinted_id)
            .first()
        )
        if existing_item:
            live_price = item_data.get("price", 0.0)
            if live_price and live_price > 0 and abs(float(existing_item.price or 0.0) - float(live_price)) >= 0.01:
                old_p = float(existing_item.price or 0.0)
                existing_item.price = round(float(live_price), 2)
                updated_existing = True
                scraper_logger.log_price_updated(
                    title=existing_item.title or "Item",
                    old_price=old_p,
                    new_price=float(live_price),
                    search_text=tracked_search.search_text,
                    item_id=str(existing_item.vinted_item_id or ""),
                    url=existing_item.url or "",
                )
            if existing_item.is_sold or existing_item.status != "active":
                existing_item.is_sold = False
                existing_item.status = "active"
                updated_existing = True
            if not existing_item.seller_id and item_data.get("seller_id"):
                existing_item.seller_id = str(item_data.get("seller_id"))
                updated_existing = True
            if not existing_item.seller_username and item_data.get("seller_username"):
                existing_item.seller_username = str(item_data.get("seller_username"))
                updated_existing = True
            if active_variants and existing_item.variant_id is None:
                v_match = variant_vision_service.classify_item_variant(
                    db=db,
                    tracked_search=tracked_search,
                    item_data=item_data,
                    variants=active_variants,
                    allow_vision=True,
                )
                if v_match:
                    existing_item.variant_id = v_match["variant_id"]
                    existing_item.variant_name = v_match["variant_name"]
                    existing_item.variant_confidence = v_match["confidence"]
                    existing_item.variant_method = v_match["method"]
                    existing_item.variant_evidence = v_match["evidence"]
                    updated_existing = True
            continue

        item_title = str(item_data.get("title", "") or "")
        item_brand = item_data.get("brand")
        item_size = item_data.get("size")
        item_desc = str(item_data.get("description", "") or "").strip()
        item_url = str(item_data.get("url", "") or "").strip()

        # If categories are constrained and description is missing on a generic title, fetch live description
        if target_categories and not item_desc and len(item_title.split()) <= 4:
            from .services.antirep import get_item_description
            live_desc = get_item_description(vinted_id)
            if live_desc:
                item_desc = live_desc
                item_data["description"] = live_desc

        # 1. Intelligent brand, category coherence, and anti-junk filter
        is_relevant, filter_reason = is_item_relevant(
            target_brand=tracked_search.brand,
            target_categories=target_categories,
            must_exclude=tracked_search.must_exclude,
            item_brand=item_brand,
            item_title=item_title,
            exclude_child_sizes=bool(getattr(tracked_search, "exclude_child_sizes", False)),
            item_size=item_size,
            item_description=item_desc,
            item_url=item_url,
        )
        if not is_relevant:
            filtered_spam_count += 1
            logger.info(
                "Blocked spam/irrelevant item '%s' (Brand: %s): %s",
                item_title,
                item_brand,
                filter_reason,
            )
            continue

        # Evaluate child size classification for database record
        is_child, child_reason = is_child_size(item_title, item_size)

        # 2. Anti-Replica (Anti-Rep) filter evaluation (in-memory + local SQLite to avoid rate limits)
        from .services.antirep import evaluate_antirep_rules

        is_rep_clean, rep_reason = evaluate_antirep_rules(
            db=db,
            tracked_search_id=tracked_search.id,
            item_data=item_data,
            antirep_config=antirep_config,
            scraper=scraper,
            seller_batch_counts=seller_batch_counts,
            deep_inspection=False,
        )
        if not is_rep_clean:
            filtered_antirep_count += 1
            logger.info(
                "Flagged replica listing '%s' (ID: %s): %s",
                item_title,
                vinted_id,
                rep_reason,
            )

        # Track count for this seller in the current scrape batch
        seller_id = item_data.get("seller_id")
        if seller_id and is_rep_clean:
            seller_batch_counts[seller_id] = seller_batch_counts.get(seller_id, 0) + 1

        # Construct absolute URL if we only got a path
        url = item_data.get("url", "")
        if url and not url.startswith("http"):
            url = f"{VINTED_BASE_URL}{url}"

        # Clean raw_data to avoid non-serializable objects
        serializable_data = dict(item_data)
        serializable_data.pop("raw_item", None)
        serializable_data.pop("raw_photo", None)

        # 3. Local AI Variant Classification (CLIP Vision + Apple Neural OCR, $0 cloud cost)
        variant_match = None
        if active_variants:
            variant_match = variant_vision_service.classify_item_variant(
                db=db,
                tracked_search=tracked_search,
                item_data=item_data,
                variants=active_variants,
                allow_vision=True,
            )

        sold_item = SoldItem(
            tracked_search_id=tracked_search.id,
            vinted_item_id=vinted_id,
            title=item_data.get("title", ""),
            price=item_data.get("price", 0.0),
            currency=item_data.get("currency", "GBP"),
            brand=item_data.get("brand") or None,
            size=item_size or None,
            condition=item_data.get("condition") or None,
            seller_id=seller_id or None,
            seller_username=item_data.get("seller_username") or None,
            photo_url=item_data.get("photo_url"),
            url=url or None,
            status=item_data.get("status") or "active",
            is_sold=False,
            is_replica=not is_rep_clean,
            replica_reason=rep_reason if not is_rep_clean else None,
            is_child_size=is_child,
            child_size_reason=child_reason if is_child else None,
            variant_id=variant_match["variant_id"] if variant_match else None,
            variant_name=variant_match["variant_name"] if variant_match else None,
            variant_confidence=variant_match["confidence"] if variant_match else 0.0,
            variant_method=variant_match["method"] if variant_match else None,
            variant_evidence=variant_match["evidence"] if variant_match else None,
            raw_data=json.dumps(serializable_data),
        )
        db.add(sold_item)
        new_count += 1
        scraper_logger.log_item_added(
            item_data={
                "id": vinted_id,
                "title": item_data.get("title", ""),
                "price": item_data.get("price", 0.0),
                "currency": item_data.get("currency", "GBP"),
                "brand": item_brand or tracked_search.brand or "",
                "size": item_size or "",
                "condition": item_data.get("condition") or "",
                "seller_username": item_data.get("seller_username") or "",
                "photo_url": item_data.get("photo_url"),
                "url": url,
                "variant_name": variant_match["variant_name"] if variant_match else "",
            },
            search_text=tracked_search.search_text,
            source="scraper",
        )

    if new_count or updated_existing:
        db.commit()

    scraper_logger.log_scan_summary(
        search_text=tracked_search.search_text,
        total_found=len(items),
        new_count=new_count,
        spam_blocked=filtered_spam_count,
        rep_blocked=filtered_antirep_count,
        source="scraper",
    )

    logger.info(
        "Scrape complete for '%s': %d new items stored (blocked: %d spam, %d replica, total found %d)",
        tracked_search.search_text,
        new_count,
        filtered_spam_count,
        filtered_antirep_count,
        len(items),
    )

    if return_details:
        return {
            "new_count": new_count,
            "filtered_spam_count": filtered_spam_count,
            "filtered_antirep_count": filtered_antirep_count,
            "total_found": len(items),
        }

    return new_count

