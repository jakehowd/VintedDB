"""Anti-Replica (Anti-Rep) detection service and filters.

Implements rules targeting common replica seller traits on Vinted:
1. "New with tags" (BNWT) listings — checks Vinted condition tags AND deep scans
   titles and descriptions for hidden tag mentions (e.g., BNWT, sealed with tags, tags attached).
2. Sellers with zero or low reviews — checks seller profile feedback count via Vinted API Gateway.
3. Sellers listing multiple quantities of the same/similar item with tags.
4. Duplicate / stock photos — detects reused image assets via fingerprint database across all scrapes.
5. Dormant / acquired accounts — flags sellers whose only reviews were a set time limit ago.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..config import VINTED_BASE_URL
from ..models import DEFAULT_ANTIREP_CONFIG, ScrapedPhotoRecord, SoldItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for detecting "New with tags" / BNWT in titles & descriptions
# ---------------------------------------------------------------------------

BNWT_CONDITION_STRINGS = [
    "new with tags",
    "brand new with tags",
    "bnwt",
    "nwt",
    "neuf avec étiquette",
    "neuf avec etiquette",
    "nuevo con etiquetas",
    "nuovo con cartellino",
    "nieuw met prijskaartje",
    "nowy z metką",
    "nowy z metka",
]

TAG_DESCRIPTION_PATTERNS = [
    r"\b(?:bnwt|nwt)\b",
    r"\bbrand\s+new\s+with\s+(?:all\s+|the\s+|original\s+|its\s+|store\s+|price\s+)?tags?\b",
    r"\bnew\s+with\s+(?:all\s+|the\s+|original\s+|its\s+|store\s+|price\s+)?tags?\b",
    r"\b(?<!without\s)(?<!no\s)(?<!missing\s)(?<!removed\s)with\s+(?:all\s+|the\s+|its\s+|original\s+|store\s+|price\s+)?tags?\b",
    r"\btags?\s+(?:are\s+|still\s+)?attached\b",
    r"\btags?\s+still\s+on\b",
    r"\bcomes\s+with\s+(?:all\s+|the\s+|original\s+)?tags?\b",
    r"\bdeadstock\s+with\s+tags?\b",
    r"\bsealed\s+with\s+tags?\b",
    r"\bhas\s+(?:all\s+|the\s+|original\s+)?tags?\b",
    r"\bwith\s+original\s+box\s+and\s+tags?\b",
    r"\bwith\s+receipt\s+and\s+tags?\b",
    r"\bwith\s+tags?\s+and\s+receipt\b",
    r"\b(?:store\s+tags?|barcode\s+tags?|swing\s+tags?|boutique\s+tags?)\b",
]

# Compile regexes once
COMPILED_TAG_PATTERNS = [re.compile(p, re.IGNORECASE) for p in TAG_DESCRIPTION_PATTERNS]


def is_new_with_tags(
    condition: Optional[str],
    title: str,
    description: Optional[str] = None,
) -> Tuple[bool, str]:
    """Check whether a listing represents a 'New with Tags' item.

    Examines:
    1. Vinted condition string
    2. Item title
    3. Item description (if available)
    """
    # 1. Condition check
    if condition:
        cond_lower = condition.strip().lower()
        for marker in BNWT_CONDITION_STRINGS:
            if marker in cond_lower:
                return True, f"Condition marked as '{condition}'"

    # 2. Title check
    t_lower = (title or "").lower()
    for pattern in COMPILED_TAG_PATTERNS:
        m = pattern.search(t_lower)
        if m:
            return True, f"Title contains tag keyword: '{m.group(0)}'"

    # 3. Description check
    if description:
        d_lower = description.lower()
        for pattern in COMPILED_TAG_PATTERNS:
            m = pattern.search(d_lower)
            if m:
                return True, f"Description contains tag keyword: '{m.group(0)}'"

    return False, "Not new with tags"


# ---------------------------------------------------------------------------
# Duplicate / Stock Photo Fingerprint Tracking
# ---------------------------------------------------------------------------

def extract_photo_key(photo_obj: Any = None, photo_url: Optional[str] = None) -> Optional[str]:
    """Extract a unique Vinted photo identifier / CDN asset key.

    Vinted CDN asset URLs follow patterns like:
    https://images1.vinted.net/tc/01_00855_1JSEn58QWsrTopcc85RvmQyi/cf83ecd2.webp
    where '01_00855_1JSEn58QWsrTopcc85RvmQyi' is the unique photo identifier.
    """
    # 1. Direct object inspection
    if isinstance(photo_obj, dict):
        high_res = photo_obj.get("high_resolution")
        if isinstance(high_res, dict) and high_res.get("id"):
            return str(high_res["id"]).strip()
        url = photo_obj.get("full_size_url") or photo_obj.get("url")
        if url:
            photo_url = url
    elif photo_obj is not None:
        high_res = getattr(photo_obj, "high_resolution", None)
        if high_res and hasattr(high_res, "id") and getattr(high_res, "id"):
            return str(getattr(high_res, "id")).strip()
        url = getattr(photo_obj, "full_size_url", None) or getattr(photo_obj, "url", None)
        if url:
            photo_url = url

    # 2. URL parsing fallback
    if photo_url:
        m = re.search(r"([0-9]{2}_[0-9A-Za-z_]+)", photo_url)
        if m:
            return m.group(1).strip()
        # Fallback to stripped base URL
        clean = photo_url.split("?")[0].rstrip("/")
        parts = clean.split("/")
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
        return parts[-1]

    return None


def check_duplicate_photo(
    db: Session,
    photo_key: str,
    current_item_id: str,
) -> Optional[ScrapedPhotoRecord]:
    """Check if photo_key has been previously recorded from another item."""
    if not photo_key:
        return None
    rec = (
        db.query(ScrapedPhotoRecord)
        .filter(
            ScrapedPhotoRecord.photo_key == photo_key,
            ScrapedPhotoRecord.vinted_item_id != current_item_id,
        )
        .first()
    )
    if rec:
        return rec

    # Also check SoldItem.photo_url directly in case an older item wasn't in scraped_photos
    existing_sold = (
        db.query(SoldItem)
        .filter(
            SoldItem.vinted_item_id != current_item_id,
            SoldItem.photo_url.ilike(f"%{photo_key}%"),
        )
        .first()
    )
    if existing_sold:
        return record_photo_fingerprint(
            db=db,
            photo_key=photo_key,
            photo_url=existing_sold.photo_url,
            vinted_item_id=existing_sold.vinted_item_id,
            seller_id=existing_sold.seller_id,
            tracked_search_id=existing_sold.tracked_search_id,
        )
    return None


def record_photo_fingerprint(
    db: Session,
    photo_key: str,
    photo_url: Optional[str],
    vinted_item_id: str,
    seller_id: Optional[str] = None,
    tracked_search_id: Optional[int] = None,
) -> Optional[ScrapedPhotoRecord]:
    """Store photo fingerprint into scraped_photos database table."""
    if not photo_key:
        return None

    existing = (
        db.query(ScrapedPhotoRecord)
        .filter(
            ScrapedPhotoRecord.photo_key == photo_key,
            ScrapedPhotoRecord.vinted_item_id == vinted_item_id,
        )
        .first()
    )
    if existing:
        return existing

    record = ScrapedPhotoRecord(
        photo_key=photo_key,
        photo_url=photo_url,
        vinted_item_id=vinted_item_id,
        seller_id=seller_id,
        tracked_search_id=tracked_search_id,
    )
    db.add(record)
    return record


# ---------------------------------------------------------------------------
# Seller Profile & Review / Dormancy Analysis (via Chrome TLS & API Gateway)
# ---------------------------------------------------------------------------

_SELLER_PROFILE_CACHE: Dict[str, dict] = {}
_SELLER_FEEDBACK_CACHE: Dict[str, list] = {}
_ITEM_UPLOAD_TS_CACHE: Dict[str, datetime] = {}
_ITEM_DESCRIPTION_CACHE: Dict[str, str] = {}
_CACHE_LOCK = threading.Lock()


def parse_vinted_upload_age_minutes(raw_value: str) -> Optional[int]:
    """Parse Vinted's relative 'Uploaded' string (e.g. '2 min ago', 'an hour ago', '3 days ago') into minutes."""
    if not raw_value:
        return None

    val = str(raw_value).strip().lower()
    if not val:
        return None

    if val in ("just now", "now", "moments ago", "a few seconds ago", "seconds ago") or "second" in val:
        return 0

    if val in ("a minute ago", "1 minute ago", "1 min ago", "a min ago"):
        return 1
    m_min = re.search(r"(\d+)\s*(?:min|mins|minute|minutes)\b", val)
    if m_min:
        return int(m_min.group(1))

    if val in ("an hour ago", "1 hour ago", "about an hour ago"):
        return 60
    m_hr = re.search(r"(\d+)\s*(?:hour|hours|hr|hrs)\b", val)
    if m_hr:
        return int(m_hr.group(1)) * 60

    if val in ("a day ago", "1 day ago", "yesterday"):
        return 1440
    m_day = re.search(r"(\d+)\s*(?:day|days)\b", val)
    if m_day:
        return int(m_day.group(1)) * 1440

    if val in ("a week ago", "1 week ago"):
        return 10080
    m_wk = re.search(r"(\d+)\s*(?:week|weeks|wk|wks)\b", val)
    if m_wk:
        return int(m_wk.group(1)) * 10080

    if val in ("a month ago", "1 month ago"):
        return 43200
    m_mo = re.search(r"(\d+)\s*(?:month|months|mo|mos)\b", val)
    if m_mo:
        return int(m_mo.group(1)) * 43200

    if val in ("a year ago", "1 year ago"):
        return 525600
    m_yr = re.search(r"(\d+)\s*(?:year|years|yr|yrs)\b", val)
    if m_yr:
        return int(m_yr.group(1)) * 525600

    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
            diff_mins = max(0, int((datetime.now(timezone.utc) - dt).total_seconds() // 60))
            return diff_mins
        except Exception:
            pass

    return None


def _extract_upload_and_seller_from_item_html(vid_str: str, html_text: str) -> Optional[datetime]:
    """Extract upload timestamp, seller profile, and listing description from a Vinted item page HTML/RSC payload."""
    unesc = html_text.replace('\\"', '"')

    # 1. Opportunistically extract and cache seller profile from user_info_header
    m_uh = re.search(
        r'"feedback_count":(\d+).*?"name":"([^"]*)".*?"user_id":"?(\d+)"?[^\n]*?"name":"user_info_header"',
        unesc,
    )
    if m_uh:
        fc = int(m_uh.group(1))
        uname = m_uh.group(2)
        sid = m_uh.group(3)
        prof = {
            "id": sid,
            "login": uname,
            "feedback_count": fc,
        }
        with _CACHE_LOCK:
            if sid not in _SELLER_PROFILE_CACHE:
                _SELLER_PROFILE_CACHE[sid] = prof

    # 2. Extract and cache listing description from OpenGraph meta or itemprop
    m_og_desc = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']', html_text)
    if not m_og_desc:
        m_og_desc = re.search(r'itemprop=["\']description["\'][^>]*>([^<]+)<', html_text)
    if not m_og_desc:
        m_og_desc = re.search(r'"description"\s*:\s*"([^"]+)"', unesc)
    if m_og_desc:
        desc_val = m_og_desc.group(1).strip()
        with _CACHE_LOCK:
            _ITEM_DESCRIPTION_CACHE[vid_str] = desc_val

    # 3. Extract Vinted's "Uploaded" string from RSC JSON or HTML itemProp="upload_date"
    upload_str = None
    m_rsc = re.search(r'"title"\s*:\s*"Uploaded"\s*,\s*"value"\s*:\s*"([^"]+)"', unesc)
    if m_rsc:
        upload_str = m_rsc.group(1)
    else:
        m_prop = re.search(r'itemProp="upload_date"[^>]*>.*?<span[^>]*>([^<]+)</span>', unesc)
        if m_prop:
            upload_str = m_prop.group(1)

    if upload_str:
        age_mins = parse_vinted_upload_age_minutes(upload_str)
        if age_mins is not None:
            uploaded_at = datetime.now(timezone.utc) - timedelta(minutes=age_mins)
            with _CACHE_LOCK:
                _ITEM_UPLOAD_TS_CACHE[vid_str] = uploaded_at
            return uploaded_at

    return None


def get_item_description(vinted_item_id: str, html_text: Optional[str] = None) -> Optional[str]:
    """Return the description of a Vinted item, using cache or fetching live HTML."""
    vid_str = str(vinted_item_id or "").strip()
    if not vid_str:
        return None

    with _CACHE_LOCK:
        if vid_str in _ITEM_DESCRIPTION_CACHE:
            return _ITEM_DESCRIPTION_CACHE[vid_str]

    if html_text:
        _extract_upload_and_seller_from_item_html(vid_str, html_text)
        with _CACHE_LOCK:
            return _ITEM_DESCRIPTION_CACHE.get(vid_str)

    from .status_checker import _gateway_get
    from .http_client import antiban_client

    base = VINTED_BASE_URL.rstrip("/")
    try:
        html_hdrs = antiban_client._build_headers()
        if antiban_client._raw_cookie_str:
            html_hdrs["Cookie"] = antiban_client._raw_cookie_str
        ri = _gateway_get(f"{base}/items/{vid_str}", headers=html_hdrs, timeout=8.0)
        if ri.status_code == 200 and ri.text:
            _extract_upload_and_seller_from_item_html(vid_str, ri.text)
            with _CACHE_LOCK:
                return _ITEM_DESCRIPTION_CACHE.get(vid_str)
    except Exception as exc:
        logger.debug("Could not fetch description for item %s: %s", vid_str, exc)

    return None


def get_item_uploaded_at(vinted_item_id: str) -> Optional[datetime]:
    """Return the UTC datetime when a Vinted item was actually uploaded to Vinted."""
    vid_str = str(vinted_item_id or "").strip()
    if not vid_str:
        return None

    with _CACHE_LOCK:
        if vid_str in _ITEM_UPLOAD_TS_CACHE:
            return _ITEM_UPLOAD_TS_CACHE[vid_str]

    from .status_checker import _gateway_get
    from .http_client import antiban_client

    base = VINTED_BASE_URL.rstrip("/")
    try:
        html_hdrs = antiban_client._build_headers()
        if antiban_client._raw_cookie_str:
            html_hdrs["Cookie"] = antiban_client._raw_cookie_str
        ri = _gateway_get(f"{base}/items/{vid_str}", headers=html_hdrs, timeout=8.0)
        if ri.status_code == 200 and ri.text:
            return _extract_upload_and_seller_from_item_html(vid_str, ri.text)
    except Exception as exc:
        logger.debug("Could not fetch upload time for item %s: %s", vid_str, exc)

    return None


def get_item_upload_age_minutes(vinted_item_id: str) -> Optional[int]:
    """Return how many minutes ago a Vinted item was actually uploaded to Vinted."""
    uploaded_at = get_item_uploaded_at(vinted_item_id)
    if not uploaded_at:
        return None
    now_utc = datetime.now(timezone.utc)
    if uploaded_at.tzinfo is None:
        uploaded_at = uploaded_at.replace(tzinfo=timezone.utc)
    return max(0, int((now_utc - uploaded_at).total_seconds() // 60))


def get_seller_profile(
    scraper: Any = None,
    seller_id: Optional[str] = None,
    vinted_item_id: Optional[str] = None,
) -> Optional[dict]:
    """Fetch seller profile & feedback summary JSON with thread-safe in-memory caching.

    Uses Vinted's `/api/v2/user_feedbacks/summary?user_id={sid}` and `/api/v2/users/{sid}`
    over Chrome TLS impersonation, and can also resolve `seller_id` + `feedback_count`
    from `vinted_item_id` when `seller_id` was not present on the item record.
    """
    from .status_checker import API_GATEWAY_BASE, _build_api_gateway_headers, _gateway_get
    from .http_client import antiban_client

    sid = str(seller_id or "").strip()
    if sid:
        with _CACHE_LOCK:
            if sid in _SELLER_PROFILE_CACHE:
                return _SELLER_PROFILE_CACHE[sid]

    hdrs = _build_api_gateway_headers()
    base = VINTED_BASE_URL.rstrip("/")

    # If seller_id is missing, try to resolve it (and feedback_count if on item page) via vinted_item_id
    if not sid and vinted_item_id:
        vid_str = str(vinted_item_id).strip()
        try:
            more_url = f"{API_GATEWAY_BASE}/item-details/more-items/{vid_str}?content_source=other_user_items&screen=item"
            rm = _gateway_get(more_url, headers=hdrs, timeout=8.0)
            if rm.status_code == 200:
                more_items = rm.json().get("items") or []
                if more_items:
                    sid = str(more_items[0].get("user_id") or "").strip()
        except Exception as exc:
            logger.debug("Could not resolve seller_id via more-items for %s: %s", vid_str, exc)

        if sid:
            with _CACHE_LOCK:
                if sid in _SELLER_PROFILE_CACHE:
                    return _SELLER_PROFILE_CACHE[sid]

        # If more-items was empty (e.g. 1-item seller), inspect the item page RSC `user_info_header`
        if not sid:
            try:
                html_hdrs = antiban_client._build_headers()
                if antiban_client._raw_cookie_str:
                    html_hdrs["Cookie"] = antiban_client._raw_cookie_str
                ri = _gateway_get(f"{base}/items/{vid_str}", headers=html_hdrs, timeout=8.0)
                if ri.status_code == 200:
                    _extract_upload_and_seller_from_item_html(vid_str, ri.text)
                    unesc = ri.text.replace('\\"', '"')
                    m_uh = re.search(
                        r'"feedback_count":(\d+).*?"name":"([^"]*)".*?"user_id":"?(\d+)"?[^\n]*?"name":"user_info_header"',
                        unesc,
                    )
                    if m_uh:
                        sid = m_uh.group(3)
                        with _CACHE_LOCK:
                            if sid in _SELLER_PROFILE_CACHE:
                                return _SELLER_PROFILE_CACHE[sid]
            except Exception as exc:
                logger.debug("Item page user_info_header fallback failed for %s: %s", vid_str, exc)

    if not sid:
        return None

    # 1. Fast lightweight endpoint: /api/v2/user_feedbacks/summary?user_id={sid}
    try:
        sum_url = f"{base}/api/v2/user_feedbacks/summary?user_id={sid}"
        r_sum = _gateway_get(sum_url, headers=hdrs, timeout=8.0)
        if r_sum.status_code == 200:
            data = r_sum.json()
            summary = data.get("user_feedback_summary")
            if isinstance(summary, dict):
                fc = int(summary.get("feedback_count") or 0)
                prof = {
                    "id": sid,
                    "feedback_count": fc,
                    "user_feedback_count": int(summary.get("user_feedback_count") or 0),
                    "system_feedback_count": int(summary.get("system_feedback_count") or 0),
                    "feedback_rating": summary.get("feedback_rating"),
                }
                with _CACHE_LOCK:
                    _SELLER_PROFILE_CACHE[sid] = prof
                return prof
    except Exception as exc:
        logger.debug("Failed to fetch seller %s feedback summary: %s", sid, exc)

    # 2. Secondary fallback: /api/v2/users/{sid}
    try:
        u_url = f"{base}/api/v2/users/{sid}"
        r_user = _gateway_get(u_url, headers=hdrs, timeout=8.0)
        if r_user.status_code == 200:
            data = r_user.json()
            if isinstance(data, dict) and isinstance(data.get("user"), dict):
                user_data = data["user"]
                user_data["id"] = str(user_data.get("id") or sid)
                user_data["feedback_count"] = int(user_data.get("feedback_count") or 0)
                with _CACHE_LOCK:
                    _SELLER_PROFILE_CACHE[sid] = user_data
                return user_data
    except Exception as exc:
        logger.debug("Failed to fetch seller %s profile: %s", sid, exc)

    # 3. Legacy scraper fallback if provided
    if scraper and hasattr(scraper, "curl"):
        try:
            res = scraper.curl(f"/api/v2/users/{sid}", api_endpoint=False)
            data = res.json_data if hasattr(res, "json_data") else res
            if isinstance(data, dict) and "user" in data:
                user_data = data["user"]
                with _CACHE_LOCK:
                    _SELLER_PROFILE_CACHE[sid] = user_data
                return user_data
        except Exception:
            pass

    return None


def get_seller_feedbacks(scraper: Any = None, seller_id: str = "") -> list[dict]:
    """Fetch seller feedbacks JSON list with thread-safe in-memory caching."""
    if not seller_id:
        return []

    sid = str(seller_id).strip()
    with _CACHE_LOCK:
        if sid in _SELLER_FEEDBACK_CACHE:
            return _SELLER_FEEDBACK_CACHE[sid]

    from .status_checker import _build_api_gateway_headers, _gateway_get

    hdrs = _build_api_gateway_headers()
    base = VINTED_BASE_URL.rstrip("/")
    try:
        r = _gateway_get(f"{base}/api/v2/user_feedbacks?user_id={sid}&page=1&per_page=10", headers=hdrs, timeout=8.0)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict) and "user_feedbacks" in data:
                fbs = data["user_feedbacks"] or []
                with _CACHE_LOCK:
                    _SELLER_FEEDBACK_CACHE[sid] = fbs
                return fbs
    except Exception as exc:
        logger.debug("Failed to fetch seller %s feedbacks: %s", sid, exc)

    return []


def is_seller_no_reviews(profile: Optional[dict], min_reviews: int = 1) -> Tuple[bool, str]:
    """Check whether a seller has zero or insufficient feedback reviews."""
    if not profile:
        return False, "Seller profile unavailable"

    feedback_count = int(profile.get("feedback_count", 0) or 0)
    if feedback_count < min_reviews:
        if feedback_count == 0:
            return True, f"Seller has 0 feedback reviews (minimum required: {min_reviews})"
        return True, f"Seller has {feedback_count} reviews (minimum required: {min_reviews})"

    return False, f"Seller has {feedback_count} reviews"


def is_seller_dormant(feedbacks: list[dict], max_dormancy_days: int = 90) -> Tuple[bool, str]:
    """Check whether seller's only reviews are older than max_dormancy_days (acquired account)."""
    if not feedbacks:
        return False, "No reviews to assess dormancy"

    latest = feedbacks[0]
    ts_str = latest.get("created_at_ts")
    if not ts_str:
        return False, "Review timestamp unavailable"

    try:
        latest_dt = datetime.fromisoformat(ts_str)
        now = datetime.now(latest_dt.tzinfo or timezone.utc)
        days_ago = (now - latest_dt).days
        if days_ago > max_dormancy_days:
            return True, f"Latest review was {days_ago} days ago (exceeds {max_dormancy_days}d dormancy limit — acquired account)"
        return False, f"Recent review activity ({days_ago} days ago)"
    except Exception as exc:
        logger.debug("Error parsing review timestamp '%s': %s", ts_str, exc)
        return False, "Timestamp parsing failed"


# ---------------------------------------------------------------------------
# Composite Anti-Rep Evaluator (Executed on Every Scrape & Sniper Poll)
# ---------------------------------------------------------------------------

def evaluate_antirep_rules(
    db: Session,
    tracked_search_id: int,
    item_data: dict,
    antirep_config: dict,
    scraper: Any = None,
    seller_batch_counts: Optional[Dict[str, int]] = None,
    deep_inspection: bool = False,
) -> Tuple[bool, str]:
    """Evaluate an individual scraped item against all enabled anti-rep rules.

    Returns:
        (is_allowed: bool, reason: str)
    """
    if not antirep_config.get("antirep_enabled", True):
        return True, "Anti-rep protection disabled"

    vinted_id = str(item_data.get("id") or "")
    title = str(item_data.get("title") or "")
    condition = item_data.get("condition")
    seller_id = str(item_data.get("seller_id") or "").strip()
    photo_key = extract_photo_key(item_data.get("raw_photo"), item_data.get("photo_url"))

    # Always ensure photo is saved/fingerprinted into database
    if photo_key:
        record_photo_fingerprint(
            db=db,
            photo_key=photo_key,
            photo_url=item_data.get("photo_url"),
            vinted_item_id=vinted_id,
            seller_id=seller_id,
            tracked_search_id=tracked_search_id,
        )

    # 1. Stock / Duplicate Photo Filter
    if antirep_config.get("block_duplicate_photos", True) and photo_key:
        duplicate = check_duplicate_photo(db, photo_key, vinted_id)
        if duplicate:
            # Also ensure any existing SoldItem sharing this photo_key is flagged in DB
            prior_items = (
                db.query(SoldItem)
                .filter(
                    SoldItem.vinted_item_id != vinted_id,
                    SoldItem.photo_url.ilike(f"%{photo_key}%"),
                    SoldItem.is_replica == False,
                )
                .all()
            )
            for p_it in prior_items:
                p_it.is_replica = True
                p_it.replica_reason = f"Duplicate / stock photo detected (also seen in item #{vinted_id})"
            return False, f"Duplicate / stock photo detected (previously seen in item #{duplicate.vinted_item_id})"

    # 2. Max Similar Listings per Seller Filter
    max_listings = int(antirep_config.get("max_seller_listings", 2))
    if antirep_config.get("block_multiple_seller_listings", True) and seller_id:
        batch_count = (seller_batch_counts or {}).get(seller_id, 0)
        db_count = (
            db.query(SoldItem.id)
            .filter(
                SoldItem.tracked_search_id == tracked_search_id,
                SoldItem.seller_id == seller_id,
                SoldItem.vinted_item_id != vinted_id,
            )
            .count()
        )
        total_seller_listings = batch_count + db_count + 1  # Including current item
        if total_seller_listings > max_listings:
            return False, f"Seller is listing multiple similar items ({total_seller_listings} listings, limit is {max_listings})"

    # 3. New With Tags (BNWT) Filter
    if antirep_config.get("block_new_with_tags", True):
        description = item_data.get("description")
        is_bnwt, bnwt_reason = is_new_with_tags(condition, title, description)

        if not is_bnwt and not description and deep_inspection and scraper and hasattr(scraper, "item"):
            try:
                page_meta = scraper.item(vinted_id)
                fetched_desc = getattr(page_meta, "description", None)
                if fetched_desc:
                    item_data["description"] = fetched_desc
                    is_bnwt, bnwt_reason = is_new_with_tags(condition, title, fetched_desc)
            except Exception as exc:
                logger.debug("Failed to fetch item description for %s: %s", vinted_id, exc)

        if is_bnwt:
            return False, f"New with tags item blocked: {bnwt_reason}"

    # 4. Zero Reviews / Low Reviews Seller Filter & 5. Dormant Account Filter
    need_profile = bool(antirep_config.get("block_no_reviews", True))
    need_dormancy = bool(antirep_config.get("block_dormant_accounts", False))

    if need_profile or need_dormancy:
        profile = get_seller_profile(scraper, seller_id=seller_id, vinted_item_id=vinted_id)
        if profile:
            if not seller_id and profile.get("id"):
                seller_id = str(profile["id"])
                item_data["seller_id"] = seller_id
            if not item_data.get("seller_username") and profile.get("login"):
                item_data["seller_username"] = str(profile["login"])

            if need_profile:
                min_reviews = max(1, int(antirep_config.get("min_seller_reviews", 1) or 1))
                is_zero, zero_reason = is_seller_no_reviews(profile, min_reviews=min_reviews)
                if is_zero:
                    return False, f"Seller review check failed: {zero_reason}"

            if need_dormancy and int(profile.get("feedback_count", 0) or 0) > 0 and seller_id:
                dormancy_days = int(antirep_config.get("dormant_account_days", 90) or 90)
                feedbacks = get_seller_feedbacks(scraper, seller_id)
                if feedbacks:
                    is_dorm, dorm_reason = is_seller_dormant(feedbacks, max_dormancy_days=dormancy_days)
                    if is_dorm:
                        return False, f"Dormant account check failed: {dorm_reason}"

    return True, "Passed anti-rep filters"


def is_existing_item_compliant(
    item: SoldItem,
    antirep_config: dict,
    db: Session,
    seller_item_counts: Optional[Dict[str, int]] = None,
    check_live_seller_reviews: bool = True,
) -> Tuple[bool, str]:
    """Verify whether an existing database item conforms to the updated anti-rep configuration."""
    if not antirep_config.get("antirep_enabled", True):
        return True, "Anti-rep protection disabled"

    # 1. Duplicate / Stock Photo Check
    if antirep_config.get("block_duplicate_photos", True) and item.photo_url:
        pk = extract_photo_key(None, item.photo_url)
        if pk:
            dup = check_duplicate_photo(db, pk, str(item.vinted_item_id))
            if dup:
                return False, f"Duplicate / stock photo detected (previously seen in item #{dup.vinted_item_id})"
            other_sold = (
                db.query(SoldItem)
                .filter(
                    SoldItem.vinted_item_id != str(item.vinted_item_id),
                    SoldItem.photo_url.ilike(f"%{pk}%"),
                )
                .first()
            )
            if other_sold:
                return False, f"Duplicate / stock photo detected (previously seen in item #{other_sold.vinted_item_id})"

    # 2. BNWT Check
    if antirep_config.get("block_new_with_tags", True):
        raw_desc = None
        raw_cond = item.condition
        if item.raw_data:
            try:
                raw_blob = json.loads(item.raw_data)
                raw_desc = raw_blob.get("description")
                if not raw_cond:
                    raw_cond = raw_blob.get("condition")
            except Exception:
                pass

        is_bnwt, bnwt_reason = is_new_with_tags(raw_cond, item.title or "", raw_desc)
        if is_bnwt:
            return False, f"New with tags item blocked: {bnwt_reason}"

    # 3. Seller multiple listings check
    if antirep_config.get("block_multiple_seller_listings", True) and item.seller_id and seller_item_counts:
        max_listings = int(antirep_config.get("max_seller_listings", 2))
        seller_cnt = seller_item_counts.get(item.seller_id, 0)
        if seller_cnt > max_listings:
            return False, f"Seller is listing multiple similar items ({seller_cnt} listings, limit is {max_listings})"

    # 4. Seller Reviews & Dormancy Check
    need_profile = bool(antirep_config.get("block_no_reviews", True))
    need_dormancy = bool(antirep_config.get("block_dormant_accounts", False))
    if check_live_seller_reviews and (need_profile or need_dormancy):
        profile = get_seller_profile(None, seller_id=item.seller_id, vinted_item_id=str(item.vinted_item_id))
        if profile:
            if not item.seller_id and profile.get("id"):
                item.seller_id = str(profile["id"])
            if not item.seller_username and profile.get("login"):
                item.seller_username = str(profile["login"])

            if need_profile:
                min_reviews = max(1, int(antirep_config.get("min_seller_reviews", 1) or 1))
                is_zero, zero_reason = is_seller_no_reviews(profile, min_reviews=min_reviews)
                if is_zero:
                    return False, f"Seller review check failed: {zero_reason}"

            if need_dormancy and int(profile.get("feedback_count", 0) or 0) > 0 and item.seller_id:
                dormancy_days = int(antirep_config.get("dormant_account_days", 90) or 90)
                feedbacks = get_seller_feedbacks(None, item.seller_id)
                if feedbacks:
                    is_dorm, dorm_reason = is_seller_dormant(feedbacks, max_dormancy_days=dormancy_days)
                    if is_dorm:
                        return False, f"Dormant account check failed: {dorm_reason}"

    return True, "Item is compliant"


# ---------------------------------------------------------------------------
# High-Yield Cross-Market Arbitrage Authenticity Validator
# ---------------------------------------------------------------------------

REWORK_AND_FAKE_BLACKLIST_PATTERNS = [
    # Rework & Franken-garment markers (very common scam with Carhartt and Ralph Lauren)
    r"\b(?:rework|reworked|re-work|re-worked|upcycled|up-cycled|remade|re-made)\b",
    r"\b(?:customized?|custom\s+made|custom\s+piece|diy|frankenstein)\b",
    
    # Replica / Fake / Bootleg markers
    r"\b(?:replica|rep|reps|1:1|aaa\+?|ua\b|unauthorized\s+authentic)\b",
    r"\b(?:bootleg|fantasy\s+piece|homage\s+piece|reprint|screenprint)\b",
    r"\b(?:dhgate|pandabuy|cssbuy|sugargoo|hago|taobao|weidian)\b",
    r"\b(?:mirror\s+quality|top\s+batch|pk\s+batch|god\s+batch|og\s+batch)\b",
    r"\b(?:print\s+on\s+demand|gildan\s+heavy|printed\s+on\s+gildan)\b",
    
    # "Style" / "lookalike" evasions (sellers admitting it's not the real brand/model)
    r"\b(?:carhartt\s+style|detroit\s+style|nuptse\s+style|bape\s+style)\b",
    r"\b(?:arcteryx\s+style|stussy\s+style|corteiz\s+style)\b",
    r"\b(?:inspired\s+by|dupe|dupes|lookalike|look-alike)\b",
]

COMPILED_FAKE_BLACKLIST = [re.compile(p, re.IGNORECASE) for p in REWORK_AND_FAKE_BLACKLIST_PATTERNS]

HYPE_HIGH_FAKE_RISK_BRANDS = [
    "arc'teryx", "arcteryx", "bape", "a bathing ape", "corteiz", "crtz",
    "sp5der", "stone island", "supreme", "trapstar", "gallery dept",
]


def verify_arbitrage_item_authenticity(
    item_data: Dict[str, Any],
    db: Optional[Session] = None,
) -> Tuple[bool, str]:
    """Strict anti-counterfeit & authenticity check for cross-market arbitrage candidates.
    
    Ensures:
    1. Items are NOT 'reworked' / upcycled thrift scraps sold as authentic grails.
    2. Items are NOT replicas, 1:1 fakes, bootlegs, or DHgate/Taobao reps.
    3. High-risk hype brands listed as BNWT at suspiciously cheap prices are screened.
    4. Stock photos / duplicate stolen photos are detected.
    5. Accessory/mismatched listings using grail keyword spam are excluded.
    """
    title = str(item_data.get("title") or "")
    desc = str(item_data.get("description") or "")
    brand = str(item_data.get("brand") or "").lower()
    condition = str(item_data.get("condition") or "")
    price = float(item_data.get("price") or item_data.get("vinted_asking_price") or 0.0)
    raw_text = f"{title} {desc}".lower()

    # 1. Rework / Replica / Bootleg Blacklist
    for pat in COMPILED_FAKE_BLACKLIST:
        m = pat.search(raw_text)
        if m:
            return False, f"Blacklisted counterfeit / rework token: '{m.group(0)}'"

    # 2. Hype Brand Counterfeit Trap Detection
    # If a hype brand is claimed as 'New with tags' and priced under £45, it is 99.9% a rep
    is_hype = any(hb in brand or hb in raw_text for hb in HYPE_HIGH_FAKE_RISK_BRANDS)
    is_bnwt, _ = is_new_with_tags(condition, title, desc)
    if is_hype and is_bnwt and price > 0 and price < 45.0:
        return False, f"High counterfeit risk: {brand.title()} claimed as Brand New with Tags for £{price:.0f} (typical DHgate/Pandabuy replica)"

    # 3. Duplicate / Stolen Stock Photo Check (if db provided)
    if db:
        photo_key = extract_photo_key(item_data.get("raw_photo"), item_data.get("photo_url"))
        vinted_id = str(item_data.get("id") or item_data.get("vinted_item_id") or "")
        if photo_key and vinted_id:
            dup = check_duplicate_photo(db, photo_key, vinted_id)
            if dup:
                return False, f"Duplicate / stock photo detected (reused image #{dup.vinted_item_id})"

    # 4. Model Category Sanity Check
    # Avoid mismatched items claiming to be grails (e.g. Carhartt beanie or socks claiming to be Detroit)
    if "detroit" in raw_text:
        if any(w in raw_text for w in ["beanie", "hat", "cap", "t-shirt", "tee", "socks", "keyring", "trouser", "pants", "vest", "gilet"]):
            return False, "Not an outerwear jacket (mismatched accessory/apparel)"
    if "beta" in raw_text and ("arcteryx" in raw_text or "arc'teryx" in raw_text):
        if any(w in raw_text for w in ["beanie", "hat", "cap", "t-shirt", "tee", "glove", "gloves", "bag", "waistpack"]):
            return False, "Not a technical shell (mismatched accessory)"

    return True, "Authenticity verified"
