"""Service for verifying live Vinted listing availability, sold status, and exact listed/sold prices."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import math
import re
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from sqlalchemy.orm import Session

from ..config import VINTED_BASE_URL
from ..models import SoldItem, TrackedSearch
from .http_client import antiban_client

logger = logging.getLogger(__name__)

API_GATEWAY_BASE = "https://api.vinted.co.uk"

_gateway_lock = threading.Lock()
_direct_fallback_session: Optional[cffi_requests.Session] = None
_use_direct_fallback: bool = False


def _build_api_gateway_headers() -> Dict[str, str]:
    """Build headers required for https://api.vinted.co.uk endpoints."""
    if not antiban_client._session:
        antiban_client._reinit_session()
    headers = {
        "User-Agent": antiban_client._user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-GB,en;q=0.5",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Origin": VINTED_BASE_URL,
        "Referer": VINTED_BASE_URL,
        "Locale": "en-GB",
        "Platform": "web",
        "X-Next-App": "marketplace-web",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }
    if antiban_client._raw_cookie_str:
        headers["Cookie"] = antiban_client._raw_cookie_str
    anon_id = antiban_client._cookie_dict.get("anon_id")
    if anon_id:
        headers["X-Anon-Id"] = anon_id
    return headers


def _gateway_get(url: str, headers: Dict[str, str], timeout: float = 10.0):
    """Perform a GET request to Vinted, transparently falling back to direct Chrome TLS if proxy is CF-challenged."""
    global _direct_fallback_session, _use_direct_fallback
    if not _use_direct_fallback:
        if not antiban_client._session:
            antiban_client._reinit_session()
        try:
            r = antiban_client._session.get(url, headers=headers, timeout=timeout)
            if r.status_code not in (403, 502, 503):
                return r
        except Exception:
            pass

    with _gateway_lock:
        if _direct_fallback_session is None:
            _direct_fallback_session = cffi_requests.Session(impersonate="chrome124")
        _use_direct_fallback = True
        sess = _direct_fallback_session

    return sess.get(url, headers=headers, timeout=timeout)


def fetch_live_item_price(
    vinted_id: str,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[float], int]:
    """Fetch the exact current listed price or final sold asking price from Vinted's pricing service.

    Returns `(price_gbp, http_status_code)`.
    Works for active listings (HTTP 200), sold listings (HTTP 200), and archived/deleted listings (HTTP 404).
    """
    hdrs = headers or _build_api_gateway_headers()
    url = f"{API_GATEWAY_BASE}/pricing/public/api/v1/items/{vinted_id}/services"
    try:
        r = _gateway_get(url, headers=hdrs, timeout=10.0)
        if r.status_code == 200:
            data = r.json()
            asking = (
                data.get("discounted_asking_amount")
                or data.get("asking_amount")
                or data.get("seller_asking_amount")
                or data.get("original_asking_amount")
                or data.get("seller_original_asking_amount")
                or {}
            )
            amt = asking.get("amount")
            if amt is not None:
                val = float(amt)
                if val > 0:
                    return round(val, 2), 200
        return None, r.status_code
    except Exception as exc:
        logger.debug("Pricing API lookup failed for %s: %s", vinted_id, exc)
        return None, 0


def _clean_search_title(title: str) -> str:
    """Normalize an item title for Vinted catalog search."""
    if not title:
        return ""
    # Strip apostrophe suffixes ('s, ’s) so both "Men’s" ("Men") and "90’s" ("90") match Vinted's tokenizer
    q = re.sub(r"[\x27\u2019`]\w*", " ", title)
    q = re.sub(r"[^\w\s]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q


def _check_html_item_page(
    vinted_id: str,
    url: Optional[str] = None,
    fallback_price: Optional[float] = None,
) -> Dict[str, Any]:
    """Inspect the public Vinted item page HTML & Next.js RSC payload directly."""
    target_url = url if (url and url.startswith("http")) else f"{VINTED_BASE_URL.rstrip('/')}/items/{vinted_id}"
    try:
        html_headers = antiban_client._build_headers()
        if antiban_client._raw_cookie_str:
            html_headers["Cookie"] = antiban_client._raw_cookie_str
        response = _gateway_get(target_url, headers=html_headers, timeout=10.0)

        if response.status_code == 404:
            # Query pricing service gateway which often retains the final sale price even when HTML is 404
            pricing_p, _ = fetch_live_item_price(vinted_id)
            effective_p = pricing_p if pricing_p is not None else fallback_price
            return {
                "is_sold": True,
                "status": "sold",
                "live_price": effective_p,
                "verified": True,
                "reason": (
                    f"Sold / completed on Vinted (HTTP 404, final price £{effective_p:.2f})"
                    if effective_p
                    else "Sold / completed on Vinted (HTTP 404)"
                ),
            }

        if response.status_code == 200:
            unescaped = response.text.replace('\\"', '"')
            soup = BeautifulSoup(response.text, "html.parser")

            # 1. Extract exact item price from DOM [data-testid="item-price"] or SidebarItemProvider
            page_price = None
            price_el = soup.find(attrs={"data-testid": "item-price"})
            if price_el:
                # Remove strikethrough / original prices so only the markdown sale price is parsed
                for old_tag in price_el.find_all(["s", "del", "strike"]):
                    old_tag.decompose()
                for old_tag in price_el.find_all(class_=re.compile(r"strike|original|previous|before", re.I)):
                    old_tag.decompose()

                m_dom_p = re.search(r"([\d\.]+)", price_el.get_text(strip=True).replace(",", ""))
                if m_dom_p:
                    try:
                        page_price = round(float(m_dom_p.group(1)), 2)
                    except ValueError:
                        pass

            if page_price is None:
                m_sidebar = re.search(
                    r'SidebarItemProvider"\][^\n]*?"price":\{"amount":"([\d\.]+)"',
                    unescaped,
                )
                if m_sidebar:
                    try:
                        page_price = round(float(m_sidebar.group(1)), 2)
                    except ValueError:
                        pass

            if page_price is None:
                page_price = fallback_price

            # 2. Extract seller_id if present in SidebarItemProvider
            seller_id = None
            m_seller = re.search(r'"id":"' + re.escape(str(vinted_id)) + r'","seller_id":"(\d+)"', unescaped)
            if m_seller:
                seller_id = m_seller.group(1)

            # 3. Check RSC buyer_item_status plugin ("Sold" / "Reserved")
            m_badge = re.search(
                r'"title":"([^"]+)"\},"exposures":\[\],"name":"buyer_item_status"',
                unescaped,
            )
            badge_title = m_badge.group(1).strip().lower() if m_badge else ""

            has_buy_btn = bool(soup.find(attrs={"data-testid": "item-buy-button"})) or (
                '"name":"buy","section":"sidebar","type":"buy"' in unescaped
                or '"can_buy":true' in unescaped
            )

            has_sold_cell = any(
                cell.get_text(strip=True).lower() == "sold"
                for cell in soup.select("div.web_ui__Cell__body")
            )

            if badge_title == "sold" or has_sold_cell:
                return {
                    "is_sold": True,
                    "status": "sold",
                    "live_price": page_price,
                    "seller_id": seller_id,
                    "verified": True,
                    "reason": (
                        f"Marked as Sold on Vinted at £{page_price:.2f}"
                        if page_price
                        else "Marked as Sold on Vinted"
                    ),
                }

            if badge_title == "reserved" or '"is_reserved":true' in unescaped:
                return {
                    "is_sold": False,
                    "status": "reserved",
                    "live_price": page_price,
                    "seller_id": seller_id,
                    "verified": True,
                    "reason": (
                        f"Currently reserved on Vinted at £{page_price:.2f}"
                        if page_price
                        else "Currently reserved on Vinted"
                    ),
                }

            if has_buy_btn:
                return {
                    "is_sold": False,
                    "status": "active",
                    "live_price": page_price,
                    "seller_id": seller_id,
                    "verified": True,
                    "reason": (
                        f"Live active listing on Vinted at £{page_price:.2f}"
                        if page_price
                        else "Live active listing on Vinted"
                    ),
                }

            # Fallback if page loaded with 200 OK on /items/{id} without a Buy Now button
            if f"/items/{vinted_id}" in str(response.url) and price_el is not None:
                return {
                    "is_sold": True,
                    "status": "sold",
                    "live_price": page_price,
                    "seller_id": seller_id,
                    "verified": True,
                    "reason": (
                        f"Listing closed/sold on Vinted at £{page_price:.2f}"
                        if page_price
                        else "Listing closed/sold on Vinted"
                    ),
                }

        return {
            "is_sold": None,
            "status": "unknown",
            "live_price": fallback_price,
            "verified": False,
            "reason": f"HTTP {response.status_code} from Vinted item page",
        }
    except Exception as exc:
        logger.debug("HTML page check failed for %s: %s", vinted_id, exc)
        return {
            "is_sold": None,
            "status": "unknown",
            "live_price": fallback_price,
            "verified": False,
            "reason": f"Connection error: {exc}",
        }


def verify_vinted_item_status(
    vinted_id: str,
    url: Optional[str] = None,
    title: Optional[str] = None,
    current_price: Optional[float] = None,
    seller_id: Optional[str] = None,
    wardrobe_cache: Optional[Dict[str, Tuple[Set[str], bool]]] = None,
    wardrobe_lock: Optional[threading.Lock] = None,
    use_html_page: bool = False,
) -> Dict[str, Any]:
    """Verify a listing's live status and exact listed/sold price on Vinted UK.

    Combines Vinted's high-speed API Gateway (`https://api.vinted.co.uk`) endpoints:
      1. `/pricing/public/api/v1/items/{id}/services` -> exact current listed or final sold price
      2. `/svc-catalogue/items` -> targeted title + price active catalog search
      3. `/item-details/more-items/{id}` -> peer `other_user_items` wardrobe verification on `api.vinted.co.uk`
      4. `/items/{id}` HTML/RSC page check (when `use_html_page=True` or when 1-item seller needs disambiguation)
    """
    hdrs = _build_api_gateway_headers()
    vid_str = str(vinted_id).strip()

    # Step 1: Fetch the exact live or final asking price from Vinted's pricing service
    live_price, pricing_status = fetch_live_item_price(vid_str, headers=hdrs)
    effective_price = live_price if live_price is not None else current_price

    # If caller explicitly wants full HTML page inspection (e.g. single-item verify button)
    if use_html_page or not title:
        html_res = _check_html_item_page(vid_str, url=url, fallback_price=effective_price)
        if html_res.get("verified"):
            return html_res

    # Step 2: Fast check if seller's active wardrobe is already cached in memory
    resolved_seller_id = str(seller_id).strip() if seller_id else ""
    if resolved_seller_id and wardrobe_cache is not None:
        cached_entry = None
        if wardrobe_lock:
            with wardrobe_lock:
                cached_entry = wardrobe_cache.get(resolved_seller_id)
        else:
            cached_entry = wardrobe_cache.get(resolved_seller_id)
        if cached_entry is not None:
            active_ids, is_complete = cached_entry
            if vid_str in active_ids:
                return {
                    "is_sold": False,
                    "status": "active",
                    "live_price": effective_price,
                    "seller_id": resolved_seller_id,
                    "verified": True,
                    "reason": (
                        f"Confirmed active in seller wardrobe at £{effective_price:.2f}"
                        if effective_price
                        else "Confirmed active in seller wardrobe"
                    ),
                }
            elif is_complete:
                return {
                    "is_sold": True,
                    "status": "sold",
                    "live_price": effective_price,
                    "seller_id": resolved_seller_id,
                    "verified": True,
                    "reason": (
                        f"Confirmed sold on Vinted at £{effective_price:.2f}"
                        if effective_price
                        else "Confirmed sold on Vinted"
                    ),
                }

    # Step 3: Targeted lookup in Vinted's active catalog API (`api.vinted.co.uk/svc-catalogue/items`)
    if title:
        q = _clean_search_title(title)
        if q:
            # First search newest_first without price filter (handles USD/EUR converted prices cleanly)
            cat_urls = [
                f"{API_GATEWAY_BASE}/svc-catalogue/items?search_text={quote_plus(q)}&order=newest_first&per_page=96&page=1"
            ]
            if effective_price is not None and effective_price > 0:
                p_lo = max(0, int(math.floor(effective_price)) - 1)
                p_hi = int(math.ceil(effective_price)) + 1
                cat_urls.append(
                    f"{API_GATEWAY_BASE}/svc-catalogue/items?search_text={quote_plus(q)}&price_from={p_lo}&price_to={p_hi}&per_page=96&page=1"
                )

            for idx, cat_url in enumerate(cat_urls):
                try:
                    rc = _gateway_get(cat_url, headers=hdrs, timeout=10.0)
                    if rc.status_code == 200:
                        cat_items = rc.json().get("items") or []
                        for it in cat_items:
                            if str(it.get("id")) == vid_str:
                                it_price = effective_price
                                if isinstance(it.get("price"), dict) and it["price"].get("amount"):
                                    try:
                                        it_price = round(float(it["price"]["amount"]), 2)
                                    except ValueError:
                                        pass
                                user_obj = it.get("user") or {}
                                found_seller_id = str(user_obj.get("id") or resolved_seller_id or "") or None
                                found_seller_login = str(user_obj.get("login") or "") or None
                                return {
                                    "is_sold": False,
                                    "status": "active",
                                    "live_price": it_price,
                                    "seller_id": found_seller_id,
                                    "seller_username": found_seller_login,
                                    "verified": True,
                                    "reason": f"Confirmed active in Vinted catalog at £{it_price:.2f}",
                                }
                        # If first query returned < 96 items, we already saw every matching item in the catalog!
                        if idx == 0 and len(cat_items) < 96:
                            break
                except Exception as exc:
                    logger.debug("Targeted catalog check failed for %s: %s", vid_str, exc)

    # Step 4: Peer wardrobe check via `api.vinted.co.uk/item-details/more-items/{vid}` (Zero rate limit!)
    try:
        more_url = f"{API_GATEWAY_BASE}/item-details/more-items/{vid_str}?content_source=other_user_items&screen=item"
        rm = _gateway_get(more_url, headers=hdrs, timeout=10.0)
        if rm.status_code == 200:
            more_items = rm.json().get("items") or []
            if more_items:
                peer_vid = str(more_items[0].get("id") or "").strip()
                peer_seller_id = str(more_items[0].get("user_id") or resolved_seller_id or "").strip()
                if peer_seller_id:
                    resolved_seller_id = peer_seller_id

                # Check if we already cached this seller's wardrobe
                cached_entry = None
                if resolved_seller_id and wardrobe_cache is not None:
                    if wardrobe_lock:
                        with wardrobe_lock:
                            cached_entry = wardrobe_cache.get(resolved_seller_id)
                    else:
                        cached_entry = wardrobe_cache.get(resolved_seller_id)

                if cached_entry is None and peer_vid:
                    # Build seller's active wardrobe set using peer_vid's other_user_items on api.vinted.co.uk
                    active_ids: Set[str] = {str(x.get("id")) for x in more_items if x.get("id")}
                    active_ids.add(peer_vid)
                    is_complete = False
                    token = None
                    for _page in range(1, 21):
                        u_peer = f"{API_GATEWAY_BASE}/item-details/more-items/{peer_vid}?content_source=other_user_items&screen=item"
                        if token:
                            u_peer += f"&next_page_token={token}"
                        rp = _gateway_get(u_peer, headers=hdrs, timeout=10.0)
                        if rp.status_code != 200:
                            break
                        dp = rp.json()
                        p_items = dp.get("items") or []
                        for x in p_items:
                            xid = str(x.get("id") or "")
                            if xid:
                                active_ids.add(xid)
                        if vid_str in active_ids:
                            break
                        token = (dp.get("pagination") or {}).get("next_page_token") or dp.get("next_page_token")
                        if not token or not p_items:
                            is_complete = True
                            break

                    cached_entry = (active_ids, is_complete)
                    if resolved_seller_id and wardrobe_cache is not None:
                        if wardrobe_lock:
                            with wardrobe_lock:
                                wardrobe_cache[resolved_seller_id] = cached_entry
                        else:
                            wardrobe_cache[resolved_seller_id] = cached_entry

                if cached_entry is not None:
                    active_ids, is_complete = cached_entry
                    if vid_str in active_ids:
                        return {
                            "is_sold": False,
                            "status": "active",
                            "live_price": effective_price,
                            "seller_id": resolved_seller_id or None,
                            "verified": True,
                            "reason": (
                                f"Confirmed active in seller wardrobe at £{effective_price:.2f}"
                                if effective_price
                                else "Confirmed active in seller wardrobe"
                            ),
                        }
                    if is_complete:
                        return {
                            "is_sold": True,
                            "status": "sold",
                            "live_price": effective_price,
                            "seller_id": resolved_seller_id or None,
                            "verified": True,
                            "reason": (
                                f"Confirmed sold on Vinted at £{effective_price:.2f}"
                                if effective_price
                                else "Confirmed sold on Vinted"
                            ),
                        }
            else:
                # more_items == []: Either item was deleted/closed OR seller has only 1 item in wardrobe.
                # If pricing service returned 404 or item was not in catalog (<96 items), it's sold/closed!
                if pricing_status == 404:
                    return {
                        "is_sold": True,
                        "status": "sold",
                        "live_price": effective_price,
                        "seller_id": resolved_seller_id or None,
                        "verified": True,
                        "reason": (
                            f"Confirmed sold/closed on Vinted at £{effective_price:.2f}"
                            if effective_price
                            else "Confirmed sold/closed on Vinted"
                        ),
                    }
                # If seller_id is known, check seller's wardrobe directly
                if resolved_seller_id:
                    w_url = f"{VINTED_BASE_URL.rstrip('/')}/api/v2/wardrobe/{resolved_seller_id}/items?page=1&per_page=96"
                    rw = _gateway_get(w_url, headers=hdrs, timeout=10.0)
                    if rw.status_code == 200:
                        w_items = rw.json().get("items") or []
                        w_active = {str(wit.get("id")) for wit in w_items if not wit.get("is_closed")}
                        is_sold = vid_str not in w_active
                        return {
                            "is_sold": is_sold,
                            "status": "sold" if is_sold else "active",
                            "live_price": effective_price,
                            "seller_id": resolved_seller_id,
                            "verified": True,
                            "reason": (
                                f"Confirmed {'sold' if is_sold else 'active'} on Vinted at £{effective_price:.2f}"
                                if effective_price
                                else f"Confirmed {'sold' if is_sold else 'active'} on Vinted"
                            ),
                        }
                # Single-item seller whose item didn't match top-96 catalog: if Pricing API is 200 and absent from catalog
                if pricing_status == 200:
                    return {
                        "is_sold": True,
                        "status": "sold",
                        "live_price": effective_price,
                        "seller_id": resolved_seller_id or None,
                        "verified": True,
                        "reason": (
                            f"Confirmed sold on Vinted at £{effective_price:.2f}"
                            if effective_price
                            else "Confirmed sold on Vinted"
                        ),
                    }
    except Exception as exc:
        logger.debug("more-items check failed for %s: %s", vid_str, exc)

    # Final fallback: check HTML page
    return _check_html_item_page(vid_str, url=url, fallback_price=effective_price)


def sweep_active_catalog_for_searches(
    search_queries: List[str],
    max_pages_per_query: int = 8,
) -> Dict[str, Dict[str, Any]]:
    """Sweep Vinted's active catalog API across search queries to bulk-identify active listings and live prices.

    Returns a dict mapping `vinted_item_id -> {"price": float, "seller_id": str, "seller_username": str}`.
    """
    hdrs = _build_api_gateway_headers()
    active_map: Dict[str, Dict[str, Any]] = {}

    for query_text in search_queries:
        if not query_text or not query_text.strip():
            continue
        q_encoded = quote_plus(query_text.strip())
        for page in range(1, max_pages_per_query + 1):
            url = (
                f"{API_GATEWAY_BASE}/svc-catalogue/items"
                f"?search_text={q_encoded}&order=newest_first&per_page=96&page={page}"
            )
            try:
                r = _gateway_get(url, headers=hdrs, timeout=10.0)
                if r.status_code != 200:
                    break
                items = r.json().get("items") or []
                if not items:
                    break
                for it in items:
                    vid = str(it.get("id") or "").strip()
                    if not vid:
                        continue
                    p_val = None
                    if isinstance(it.get("price"), dict) and it["price"].get("amount") is not None:
                        try:
                            p_val = round(float(it["price"]["amount"]), 2)
                        except ValueError:
                            pass
                    user_obj = it.get("user") or {}
                    active_map[vid] = {
                        "price": p_val,
                        "seller_id": str(user_obj.get("id") or "") or None,
                        "seller_username": str(user_obj.get("login") or "") or None,
                    }
                if len(items) < 96:
                    break
                time.sleep(0.08)
            except Exception as exc:
                logger.debug("Catalog sweep error on '%s' page %d: %s", query_text, page, exc)
                break

    return active_map


def audit_and_sync_item_status(db: Session, item_id: int) -> Dict[str, Any]:
    """Verify live status and exact price for a single database item and synchronize its record."""
    item = db.query(SoldItem).filter(SoldItem.id == item_id).first()
    if not item:
        return {"ok": False, "error": "Item not found"}

    old_price = float(item.price or 0.0)
    result = verify_vinted_item_status(
        vinted_id=item.vinted_item_id,
        url=item.url,
        title=item.title,
        current_price=old_price,
        seller_id=item.seller_id,
        use_html_page=True,
    )

    price_updated = False
    if result.get("verified"):
        item.is_sold = bool(result["is_sold"])
        item.status = result["status"]
        live_price = result.get("live_price")
        if live_price is not None and live_price > 0 and abs(live_price - old_price) >= 0.01:
            item.price = live_price
            price_updated = True
        if result.get("seller_id") and not item.seller_id:
            item.seller_id = result["seller_id"]
        if result.get("seller_username") and not item.seller_username:
            item.seller_username = result["seller_username"]
        db.commit()
        db.refresh(item)

    status_label = (item.status or ("sold" if item.is_sold else "active")).upper()
    price_msg = (
        f" (Price updated: £{old_price:.2f} → £{item.price:.2f})"
        if price_updated
        else f" (£{item.price:.2f})"
    )

    return {
        "ok": True,
        "item_id": item.id,
        "vinted_item_id": item.vinted_item_id,
        "title": item.title,
        "is_sold": bool(item.is_sold),
        "status": item.status or ("sold" if item.is_sold else "active"),
        "old_price": old_price,
        "price": float(item.price or 0.0),
        "price_updated": price_updated,
        "verified": result.get("verified", False),
        "reason": result.get("reason", ""),
        "message": f"Item #{item.id} verified as {status_label}{price_msg}: {result.get('reason')}",
    }


def batch_audit_and_sync_statuses(
    db: Session,
    batch_size: Optional[int] = None,
    search_id: Optional[int] = None,
    only_unverified_or_active: bool = False,
    max_workers: int = 8,
) -> Dict[str, Any]:
    """Verify live sold/active statuses and exact prices across a batch or the entire database."""
    query = db.query(SoldItem)
    if search_id is not None:
        query = query.filter(SoldItem.tracked_search_id == search_id)
    if only_unverified_or_active:
        query = query.filter(SoldItem.is_sold == False)

    query = query.order_by(SoldItem.captured_at.asc())
    if batch_size is not None and batch_size > 0:
        items = query.limit(batch_size).all()
    else:
        items = query.all()

    if not items:
        return {
            "ok": True,
            "checked": 0,
            "marked_sold": 0,
            "still_active": 0,
            "prices_updated": 0,
            "message": "No items to verify.",
        }

    # Phase 1: Fast bulk catalog sweep if checking >= 25 items
    active_catalog_map: Dict[str, Dict[str, Any]] = {}
    if len(items) >= 25:
        searches = db.query(TrackedSearch).all()
        search_phrases = [s.search_text for s in searches if s.search_text]
        brands = sorted({it.brand.strip() for it in items if it.brand and it.brand.strip()})
        for b in brands[:6]:
            if b not in search_phrases:
                search_phrases.append(b)
        active_catalog_map = sweep_active_catalog_for_searches(search_phrases, max_pages_per_query=8)

    checked = 0
    now_sold = 0
    still_active = 0
    prices_updated = 0

    wardrobe_cache: Dict[str, Tuple[Set[str], bool]] = {}
    wardrobe_lock = threading.Lock()

    to_verify_individually: List[SoldItem] = []

    for item in items:
        vid = str(item.vinted_item_id).strip()
        if vid in active_catalog_map:
            cat_info = active_catalog_map[vid]
            checked += 1
            still_active += 1
            item.is_sold = False
            item.status = "active"
            live_p = cat_info.get("price")
            if live_p is not None and live_p > 0 and abs(live_p - float(item.price or 0.0)) >= 0.01:
                item.price = live_p
                prices_updated += 1
            if cat_info.get("seller_id") and not item.seller_id:
                item.seller_id = cat_info["seller_id"]
            if cat_info.get("seller_username") and not item.seller_username:
                item.seller_username = cat_info["seller_username"]
        else:
            to_verify_individually.append(item)

    if checked > 0:
        db.commit()

    # Phase 2: Verify remaining items concurrently via API Gateway (Pricing + Catalog + Peer More-Items)
    if to_verify_individually:
        item_map: Dict[int, SoldItem] = {it.id: it for it in to_verify_individually}
        work_args = [
            (
                it.id,
                str(it.vinted_item_id),
                it.url,
                it.title,
                float(it.price or 0.0),
                it.seller_id,
            )
            for it in to_verify_individually
        ]

        def _worker(args: Tuple[int, str, Optional[str], Optional[str], float, Optional[str]]):
            db_id, vid, url, title, cur_price, s_id = args
            res = verify_vinted_item_status(
                vinted_id=vid,
                url=url,
                title=title,
                current_price=cur_price,
                seller_id=s_id,
                wardrobe_cache=wardrobe_cache,
                wardrobe_lock=wardrobe_lock,
                use_html_page=False,
            )
            return db_id, res

        completed_since_commit = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_worker, a) for a in work_args]
            for fut in as_completed(futures):
                try:
                    db_id, res = fut.result()
                except Exception as exc:
                    logger.debug("Worker error during status audit: %s", exc)
                    continue

                if not res.get("verified"):
                    continue

                item = item_map.get(db_id)
                if not item:
                    continue

                checked += 1
                old_p = float(item.price or 0.0)
                live_p = res.get("live_price")
                if live_p is not None and live_p > 0 and abs(live_p - old_p) >= 0.01:
                    item.price = live_p
                    prices_updated += 1

                if res.get("seller_id") and not item.seller_id:
                    item.seller_id = res["seller_id"]
                if res.get("seller_username") and not item.seller_username:
                    item.seller_username = res["seller_username"]

                if res["is_sold"]:
                    item.is_sold = True
                    item.status = "sold"
                    now_sold += 1
                else:
                    item.is_sold = False
                    item.status = res.get("status") or "active"
                    still_active += 1

                completed_since_commit += 1
                if completed_since_commit >= 50:
                    db.commit()
                    completed_since_commit = 0

        if completed_since_commit > 0:
            db.commit()

    total_sold_db = db.query(SoldItem).filter(SoldItem.is_sold == True).count()
    total_active_db = db.query(SoldItem).filter(SoldItem.is_sold == False).count()

    return {
        "ok": True,
        "checked": checked,
        "marked_sold": now_sold,
        "still_active": still_active,
        "prices_updated": prices_updated,
        "total_sold_in_db": total_sold_db,
        "total_active_in_db": total_active_db,
        "message": (
            f"Audited {checked} items: {now_sold} confirmed SOLD, {still_active} confirmed ACTIVE, "
            f"{prices_updated} exact prices updated."
        ),
    }
