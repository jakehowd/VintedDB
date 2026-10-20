"""Anti-Ban HTTP Client for Vinted UK.

Implements browser-grade TLS fingerprint impersonation (Chrome JA3/JA4) via curl_cffi,
proxy rotation pools, Cloudflare clearance cookie injection, adaptive jitter,
and an automated circuit breaker to prevent IP bans.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from curl_cffi import requests
from ..config import ANTIBAN_CONFIG_PATH, DEFAULT_COOKIE, DEFAULT_PROXIES, VINTED_BASE_URL

logger = logging.getLogger(__name__)

# Realistic rotating User-Agent strings (macOS and Windows Chrome)
CHROME_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]


class VintedBlockedException(Exception):
    """Raised when Vinted or Cloudflare returns a 403 or challenge."""
    def __init__(self, message: str, status_code: int = 403, is_cloudflare: bool = True):
        super().__init__(message)
        self.status_code = status_code
        self.is_cloudflare = is_cloudflare


class VintedRateLimitException(Exception):
    """Raised when Vinted returns 429 Too Many Requests."""
    def __init__(self, message: str, retry_after: Optional[int] = 60):
        super().__init__(message)
        self.status_code = 429
        self.retry_after = retry_after


class AntiBanVintedClient:
    """Manages secure, fingerprint-spoofed communication with Vinted UK."""

    def __init__(self):
        self._lock = threading.RLock()
        self._session: Optional[requests.Session] = None
        self._proxy_list: List[str] = []
        self._proxy_index: int = 0
        self._cookie_dict: Dict[str, str] = {}
        self._raw_cookie_str: str = ""
        self._custom_user_agent: Optional[str] = None
        self._user_agent: str = CHROME_USER_AGENTS[0]

        # Circuit breaker state
        self.in_cooldown: bool = False
        self.cooldown_until: Optional[datetime] = None
        self.cooldown_reason: str = ""
        self.consecutive_errors: int = 0
        self.last_status_code: Optional[int] = None
        self.last_latency_ms: Optional[float] = None
        self.last_tested_at: Optional[datetime] = None

        # Load persisted settings or environment defaults
        self._load_config()
        self._reinit_session()

    def _load_config(self) -> None:
        """Load anti-ban proxy and cookie configuration from storage or env."""
        proxy_str = DEFAULT_PROXIES
        cookie_str = DEFAULT_COOKIE
        saved_ua = None

        if os.path.exists(ANTIBAN_CONFIG_PATH):
            try:
                with open(ANTIBAN_CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    if isinstance(cfg, dict):
                        if "proxies" in cfg or "proxy_url" in cfg:
                            proxy_str = cfg.get("proxies") if "proxies" in cfg else cfg.get("proxy_url", "")
                        if "cookies" in cfg or "cookie_str" in cfg:
                            cookie_str = cfg.get("cookies") if "cookies" in cfg else cfg.get("cookie_str", "")
                        saved_ua = cfg.get("user_agent")
            except Exception as e:
                logger.warning("Could not read antiban_config.json: %s", e)

        if saved_ua:
            self._custom_user_agent = saved_ua
            self._user_agent = saved_ua
        self._set_proxies_internal(proxy_str)
        self._set_cookies_internal(cookie_str)

    def save_config(self, proxy_str: str, cookie_str: str, user_agent: Optional[str] = None) -> None:
        """Persist updated proxy, cookie, and User-Agent configuration."""
        with self._lock:
            if user_agent:
                self._custom_user_agent = user_agent
                self._user_agent = user_agent
            self._set_proxies_internal(proxy_str)
            self._set_cookies_internal(cookie_str)
            self.reset_circuit_breaker()
            self._reinit_session()

            try:
                os.makedirs(os.path.dirname(ANTIBAN_CONFIG_PATH), exist_ok=True)
                with open(ANTIBAN_CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "proxies": proxy_str,
                        "cookies": cookie_str,
                        "user_agent": self._user_agent,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }, f, indent=2)
            except Exception as e:
                logger.error("Failed to save antiban_config.json: %s", e)

    def _set_proxies_internal(self, proxy_input: str) -> None:
        """Parse proxy list from newline or comma separated string, stripping quotes and extra tokens."""
        raw_list = []
        if proxy_input:
            lines = re.split(r"[\n,;]+", proxy_input.strip())
            for line in lines:
                tokens = line.strip().split()
                for token in tokens:
                    cleaned = token.strip().strip("\"'").strip()
                    if not cleaned or "ipv4.webshare.io" in cleaned or "ipify.org" in cleaned:
                        continue
                    if not cleaned.startswith(("http://", "https://", "socks5://", "socks5h://")):
                        cleaned = f"http://{cleaned}"
                    cleaned = cleaned.rstrip("/")
                    raw_list.append(cleaned)
        self._proxy_list = raw_list
        self._proxy_index = 0

    def _set_cookies_internal(self, cookie_input: str) -> None:
        """Parse cookie string (e.g. key=val; key2=val2) or JSON into dict."""
        self._raw_cookie_str = cookie_input.strip() if cookie_input else ""
        c_dict = {}
        if self._raw_cookie_str:
            if self._raw_cookie_str.startswith("{") and self._raw_cookie_str.endswith("}"):
                try:
                    c_dict = json.loads(self._raw_cookie_str)
                except Exception:
                    c_dict = {}
            if not c_dict:
                # Standard cookie header format
                parts = self._raw_cookie_str.split(";")
                for part in parts:
                    if "=" in part:
                        k, v = part.strip().split("=", 1)
                        if k and v:
                            c_dict[k.strip()] = v.strip()
        self._cookie_dict = c_dict

    def _reinit_session(self) -> None:
        """Recreate the curl_cffi Session with Chrome TLS profile and active proxy.

        Preserves the harvested User-Agent when custom cookies/cf_clearance are active
        because Cloudflare cryptographically binds cf_clearance to the exact User-Agent.
        """
        active_proxy = self.get_active_proxy()
        proxies = None
        if active_proxy:
            proxies = {"http": active_proxy, "https": active_proxy}

        if self._custom_user_agent:
            self._user_agent = self._custom_user_agent
        elif not self._cookie_dict:
            self._user_agent = CHROME_USER_AGENTS[0]

        self._session = requests.Session(impersonate="chrome124", proxies=proxies)

        # Inject configured cookies
        if self._cookie_dict:
            for k, v in self._cookie_dict.items():
                self._session.cookies.set(k, v, domain="vinted.co.uk")

    def get_active_proxy(self) -> Optional[str]:
        """Return the currently selected proxy URL, if any."""
        if not self._proxy_list:
            return None
        return self._proxy_list[self._proxy_index % len(self._proxy_list)]

    def rotate_proxy(self) -> Optional[str]:
        """Advance to the next proxy in the pool and refresh session."""
        with self._lock:
            if not self._proxy_list:
                return None
            self._proxy_index = (self._proxy_index + 1) % len(self._proxy_list)
            next_proxy = self.get_active_proxy()
            logger.info("Anti-Ban rotated proxy to: %s", self._mask_proxy(next_proxy))
            self._reinit_session()
            return next_proxy

    @staticmethod
    def _mask_proxy(proxy_url: Optional[str]) -> str:
        """Mask credentials in proxy string for safe logging."""
        if not proxy_url:
            return "Direct IP (No Proxy)"
        return re.sub(r"://([^:@]+):([^@]+)@", r"://\1:****@", proxy_url)

    def _build_headers(self) -> Dict[str, str]:
        """Construct realistic Chrome browser headers matching the active User-Agent."""
        m_ver = re.search(r"Chrome/(\d+)", self._user_agent)
        major_ver = m_ver.group(1) if m_ver else "124"
        platform = '"Windows"' if "Windows" in self._user_agent else '"macOS"'
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "DNT": "1",
            "Referer": f"{VINTED_BASE_URL}/catalog",
            "Sec-Ch-Ua": f'"Chromium";v="{major_ver}", "Google Chrome";v="{major_ver}", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": platform,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": self._user_agent,
        }
        return headers

    def check_circuit_breaker(self) -> Tuple[bool, int, str]:
        """Return (in_cooldown, remaining_seconds, reason)."""
        with self._lock:
            if not self.in_cooldown:
                return False, 0, ""

            now = datetime.now(timezone.utc)
            if self.cooldown_until and now >= self.cooldown_until:
                # Cooldown expired, auto-resume
                self.in_cooldown = False
                self.cooldown_until = None
                self.cooldown_reason = ""
                self.consecutive_errors = max(0, self.consecutive_errors - 1)
                logger.info("Anti-Ban circuit breaker cooldown expired. Resuming operations.")
                return False, 0, ""

            remaining = int((self.cooldown_until - now).total_seconds()) if self.cooldown_until else 0
            return True, max(1, remaining), self.cooldown_reason

    def trip_circuit_breaker(self, status_code: int, reason: str, base_cooldown_seconds: int = 60) -> None:
        """Trip the circuit breaker to pause scraping and protect the IP."""
        with self._lock:
            self.consecutive_errors += 1
            # Exponential backoff on multiple blocks: 60s -> 120s -> 300s
            multiplier = min(5, 2 ** (self.consecutive_errors - 1))
            cooldown_seconds = min(300, base_cooldown_seconds * multiplier)

            self.in_cooldown = True
            self.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)
            self.cooldown_reason = f"HTTP {status_code}: {reason} (Pausing for {cooldown_seconds}s to prevent IP block)"
            self.last_status_code = status_code
            logger.warning("[Anti-Ban Shield] Circuit breaker tripped! %s", self.cooldown_reason)

            # If a proxy pool is configured, rotate immediately so the next run uses a new IP
            if len(self._proxy_list) > 1:
                self.rotate_proxy()

    def reset_circuit_breaker(self) -> None:
        """Manually clear circuit breaker."""
        with self._lock:
            self.in_cooldown = False
            self.cooldown_until = None
            self.cooldown_reason = ""
            self.consecutive_errors = 0

    def search_catalog(
        self,
        search_text: str,
        page: int = 1,
        per_page: int = 36,
        order: str = "newest_first",
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Query Vinted UK catalog with TLS impersonation, proxies, and error handling."""
        # 1. Check Circuit Breaker
        blocked, remaining, reason = self.check_circuit_breaker()
        if blocked:
            raise VintedRateLimitException(
                f"Anti-Ban cooldown active ({remaining}s remaining). Reason: {reason}",
                retry_after=remaining
            )

        # Add human-like random micro-jitter before request
        time.sleep(random.uniform(0.15, 0.45))

        t0 = time.time()

        # Priority 1: Vinted JSON API Gateway (https://api.vinted.co.uk/svc-catalogue/items)
        # Returns full structured JSON including seller user.id, user.login, photos, and exact price
        try:
            from .status_checker import _build_api_gateway_headers, _gateway_get, API_GATEWAY_BASE
            gw_headers = _build_api_gateway_headers()
            gw_params = {
                "search_text": search_text,
                "order": order,
                "page": str(page),
                "per_page": str(per_page),
            }
            if extra_params:
                gw_params.update(extra_params)
            from urllib.parse import urlencode
            gw_url = f"{API_GATEWAY_BASE}/svc-catalogue/items?{urlencode(gw_params)}"
            gw_resp = _gateway_get(gw_url, headers=gw_headers, timeout=12.0)
            if gw_resp.status_code == 200:
                gw_items = gw_resp.json().get("items") or []
                if gw_items:
                    self.last_latency_ms = round((time.time() - t0) * 1000, 1)
                    self.last_status_code = 200
                    self.consecutive_errors = 0
                    for it in gw_items:
                        if not it.get("photo") and isinstance(it.get("photos"), list) and it["photos"]:
                            it["photo"] = it["photos"][0]
                    return gw_items
        except Exception as gw_exc:
            logger.debug("Gateway svc-catalogue/items fallback to HTML: %s", gw_exc)

        endpoint = f"{VINTED_BASE_URL.rstrip('/')}/catalog"
        params = {
            "search_text": search_text,
            "order": order,
            "page": str(page),
        }
        if extra_params:
            params.update(extra_params)

        headers = self._build_headers()

        response = None
        for attempt in range(2):
            try:
                if not self._session:
                    self._reinit_session()

                response = self._session.get(endpoint, headers=headers, params=params, timeout=12.0)
                latency = (time.time() - t0) * 1000
                self.last_latency_ms = round(latency, 1)
                self.last_status_code = response.status_code

                # Handle 200 OK
                if response.status_code == 200:
                    self.consecutive_errors = 0

                    # Detect Next.js "Session refresh" stub page caused by expired Vinted OAuth cookies
                    if "Session refresh" in response.text and self._cookie_dict:
                        logger.warning(
                            "Vinted returned 'Session refresh' page indicating expired OAuth cookies. Evicting expired Vinted auth tokens while preserving Cloudflare clearance..."
                        )
                        for expired_key in ("access_token_web", "refresh_token_web", "_vinted_fr_session", "v_sid"):
                            self._cookie_dict.pop(expired_key, None)
                        self._raw_cookie_str = "; ".join(f"{k}={v}" for k, v in self._cookie_dict.items())
                        self._reinit_session()
                        headers = self._build_headers()
                        continue

                    items = self._parse_catalog_html(response.text)
                    if not items and response.text.startswith("{"):
                        try:
                            items = response.json().get("items") or []
                        except Exception:
                            pass
                    return items

                # Handle 403 Forbidden / Cloudflare Challenge
                if response.status_code == 403:
                    active_proxy = self.get_active_proxy()
                    if attempt == 0:
                        logger.info(
                            "Encountered 403 on catalog search (proxy=%s). Attempting automated session harvest...",
                            self._mask_proxy(active_proxy) or "Direct IP",
                        )
                        try:
                            from .session_harvester import session_harvester
                            res = session_harvester.harvest(headless=True)
                            if res.get("ok"):
                                logger.info("Automated session harvest succeeded! Retrying request...")
                                headers = self._build_headers()
                                continue
                        except Exception as h_exc:
                            logger.warning("Session harvest attempt failed: %s", h_exc)

                    if active_proxy and attempt == 0:
                        logger.warning(
                            "Proxy %s returned 403 after harvest attempt. Retrying with Direct IP...",
                            self._mask_proxy(active_proxy),
                        )
                        self._session = requests.Session(impersonate="chrome124", proxies=None)
                        if self._cookie_dict:
                            for k, v in self._cookie_dict.items():
                                self._session.cookies.set(k, v, domain="vinted.co.uk")
                        continue

                    is_cf = "cloudflare" in (response.headers.get("server") or "").lower() or "challenge" in response.text.lower()
                    msg = "Cloudflare security challenge triggered on this IP." if is_cf else "Vinted 403 Forbidden."
                    self.trip_circuit_breaker(403, msg, base_cooldown_seconds=60)
                    raise VintedBlockedException(
                        f"{msg} Auto-harvest or add a proxy in Anti-Ban Settings.",
                        status_code=403,
                        is_cloudflare=is_cf,
                    )

                # Handle 429 Too Many Requests
                if response.status_code == 429:
                    retry_after = int(response.headers.get("retry-after", 60))
                    self.trip_circuit_breaker(429, "Rate limit reached.", base_cooldown_seconds=retry_after)
                    raise VintedRateLimitException(
                        f"Vinted rate limit hit (HTTP 429). Cooling down for {retry_after}s.",
                        retry_after=retry_after
                    )

                # Other status codes
                response.raise_for_status()
                return []

            except (VintedBlockedException, VintedRateLimitException):
                raise
            except Exception as exc:
                active_proxy = self.get_active_proxy()
                if active_proxy and attempt == 0:
                    logger.warning("Proxy connection failed (%s). Retrying with Direct IP...", exc)
                    self._session = requests.Session(impersonate="chrome124", proxies=None)
                    continue

                if attempt == 1:
                    status = getattr(response, "status_code", 0) if response else 0
                    self.last_status_code = status
                    err_str = str(exc)
                    if "403" in err_str:
                        self.trip_circuit_breaker(403, "Cloudflare challenge block.", base_cooldown_seconds=60)
                        raise VintedBlockedException("Cloudflare challenge block encountered.") from exc
                    if "429" in err_str:
                        self.trip_circuit_breaker(429, "Rate limit block.", base_cooldown_seconds=60)
                        raise VintedRateLimitException("Rate limit block encountered.") from exc

                    if len(self._proxy_list) > 1:
                        self.rotate_proxy()
                    raise RuntimeError(f"Network error querying Vinted catalog: {exc}") from exc

        return []

    def _parse_catalog_html(self, html_text: str) -> List[Dict[str, Any]]:
        """Parse items from Vinted UK catalog HTML with high accuracy regex & metadata extraction."""
        soup = BeautifulSoup(html_text, "html.parser")
        item_links = soup.select('a[href*="/items/"]')
        seen_ids = set()
        items = []

        for a in item_links:
            href = a.get("href") or ""
            m_id = re.search(r'/items/(\d+)(?:-([^?]+))?', href)
            if not m_id:
                continue
            item_id = int(m_id.group(1))
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            slug_title = m_id.group(2).replace("-", " ").title() if m_id.group(2) else "Vinted Item"
            card = a.parent.parent if (a.parent and a.parent.parent) else a.parent

            raw_info = a.get("title") or ""
            img = (card.find("img") if card else None) or a.find("img")
            img_url = (img.get("src") or img.get("data-src")) if img else None
            if not raw_info and img:
                raw_info = img.get("alt") or ""

            brand = ""
            condition = ""
            size = ""
            title = slug_title
            price = 0.0

            if raw_info:
                m_brand = re.search(r',\s*Brand:\s*([^,]+)', raw_info)
                m_cond = re.search(r',\s*Condition:\s*([^,]+)', raw_info)
                m_size = re.search(r',\s*Size:\s*([^,]+)', raw_info)
                m_price = re.search(r'([\d\.]+)\s*£', raw_info)

                if m_brand:
                    brand = m_brand.group(1).strip()
                if m_cond:
                    condition = m_cond.group(1).strip()
                if m_size:
                    size = m_size.group(1).strip()
                if m_price:
                    try:
                        price = float(m_price.group(1))
                    except ValueError:
                        pass

                title_part = re.split(r',\s*(?:Brand|Condition|Size):\s*', raw_info)[0].strip()
                if title_part:
                    title = title_part

            # Fallback to card text if brand, price or size missing
            if card:
                card_text = card.get_text(" | ", strip=True)
                if not price:
                    m_price = re.search(r'£([\d\.]+)', card_text)
                    if m_price:
                        try:
                            price = float(m_price.group(1))
                        except ValueError:
                            pass

                parts = [p.strip() for p in card_text.split("|") if p.strip()]
                # Ignore numbers (favorites count) and badges like 'Bumped', 'incl.', etc.
                non_num_parts = [p for p in parts if not p.isdigit() and p.lower() not in ("bumped", "incl.", "pro")]
                if not brand and non_num_parts:
                    brand = non_num_parts[0]

            second_line = f"{size} · {condition}" if (size and condition) else (size or condition)

            items.append({
                "id": item_id,
                "title": title,
                "price": price,
                "currency": "GBP",
                "url": f"{VINTED_BASE_URL.rstrip('/')}/items/{item_id}",
                "photo": img_url,
                "photo_url": img_url,
                "brand_title": brand,
                "brand": brand,
                "size_title": size,
                "size": size,
                "condition": condition,
                "status": "active",
                "item_box": {
                    "first_line": brand,
                    "second_line": second_line,
                    "accessibility_label": raw_info or f"{title}, Brand: {brand}, Condition: {condition}, Size: {size}",
                }
            })

        return items

    def probe_connection(self, allow_auto_harvest: bool = True) -> Dict[str, Any]:
        """Perform a single live diagnostic probe to test Vinted connectivity."""
        self._reinit_session()
        endpoint = f"{VINTED_BASE_URL.rstrip('/')}/catalog"
        params = {"search_text": "shoes", "page": "1"}
        headers = self._build_headers()

        t0 = time.time()
        active_proxy = self.get_active_proxy()
        masked_proxy = self._mask_proxy(active_proxy)

        try:
            r = self._session.get(endpoint, headers=headers, params=params, timeout=10.0)
            if r.status_code == 200 and "Session refresh" in r.text and self._cookie_dict:
                for expired_key in ("access_token_web", "refresh_token_web", "_vinted_fr_session", "v_sid"):
                    self._cookie_dict.pop(expired_key, None)
                self._raw_cookie_str = "; ".join(f"{k}={v}" for k, v in self._cookie_dict.items())
                self._reinit_session()
                headers = self._build_headers()
                t0 = time.time()
                r = self._session.get(endpoint, headers=headers, params=params, timeout=10.0)

            if r.status_code == 403 and allow_auto_harvest:
                logger.info("Probe received 403 on %s — attempting automated session harvest...", masked_proxy)
                try:
                    from .session_harvester import session_harvester
                    res = session_harvester.harvest(headless=True)
                    if res.get("ok"):
                        headers = self._build_headers()
                        t0 = time.time()
                        r = self._session.get(endpoint, headers=headers, params=params, timeout=10.0)
                except Exception as h_exc:
                    logger.warning("Auto-harvest during probe failed: %s", h_exc)

            latency = round((time.time() - t0) * 1000, 1)
            self.last_latency_ms = latency
            self.last_status_code = r.status_code
            self.last_tested_at = datetime.now(timezone.utc)

            if r.status_code == 200:
                self.reset_circuit_breaker()
                return {
                    "ok": True,
                    "status_code": 200,
                    "latency_ms": latency,
                    "is_cloudflare_blocked": False,
                    "proxy_used": masked_proxy,
                    "has_cookies": bool(self._cookie_dict),
                    "message": f"Connection Excellent! HTTP 200 OK ({latency}ms).",
                    "resolution": None,
                }

            if r.status_code == 403:
                is_cf = "cloudflare" in (r.headers.get("server") or "").lower() or "challenge" in r.text.lower()
                return {
                    "ok": False,
                    "status_code": 403,
                    "latency_ms": latency,
                    "is_cloudflare_blocked": is_cf,
                    "proxy_used": masked_proxy,
                    "has_cookies": bool(self._cookie_dict),
                    "message": "Cloudflare Challenge Active (HTTP 403 Forbidden).",
                    "resolution": "Your current IP is flagged by Cloudflare. Configure a residential proxy or paste your browser's cf_clearance cookie below to bypass immediately.",
                }

            if r.status_code == 429:
                return {
                    "ok": False,
                    "status_code": 429,
                    "latency_ms": latency,
                    "is_cloudflare_blocked": False,
                    "proxy_used": masked_proxy,
                    "has_cookies": bool(self._cookie_dict),
                    "message": "Rate limit active (HTTP 429 Too Many Requests).",
                    "resolution": "Vinted rate limit triggered. Wait 60–90 seconds for cooldown, or route through a fresh proxy.",
                }

            return {
                "ok": False,
                "status_code": r.status_code,
                "latency_ms": latency,
                "is_cloudflare_blocked": False,
                "proxy_used": masked_proxy,
                "has_cookies": bool(self._cookie_dict),
                "message": f"Unexpected response code: HTTP {r.status_code}.",
                "resolution": "Check internet reachability or verify proxy credentials.",
            }

        except Exception as exc:
            latency = round((time.time() - t0) * 1000, 1)
            return {
                "ok": False,
                "status_code": 0,
                "latency_ms": latency,
                "is_cloudflare_blocked": False,
                "proxy_used": masked_proxy,
                "has_cookies": bool(self._cookie_dict),
                "message": f"Connection probe failed: {exc}",
                "resolution": "Verify proxy syntax (e.g. http://user:pass@host:port) or check network connection.",
            }

    def get_status(self) -> Dict[str, Any]:
        """Return diagnostic status information for the frontend dashboard."""
        blocked, remaining, reason = self.check_circuit_breaker()
        active_proxy = self.get_active_proxy()
        has_cf_token = "cf_clearance" in self._cookie_dict or "__cf_bm" in self._cookie_dict
        has_access_token = "access_token_web" in self._cookie_dict

        return {
            "client_engine": "curl_cffi (Chrome 124 TLS Impersonation)",
            "is_proxied": bool(active_proxy),
            "active_proxy": self._mask_proxy(active_proxy),
            "total_proxies_count": len(self._proxy_list),
            "has_custom_cookies": bool(self._cookie_dict),
            "has_cf_clearance": has_cf_token,
            "has_access_token": has_access_token,
            "raw_cookie_preview": (self._raw_cookie_str[:30] + "…") if len(self._raw_cookie_str) > 30 else self._raw_cookie_str,
            "raw_cookies": self._raw_cookie_str,
            "raw_proxies": "\n".join(self._proxy_list),
            "circuit_breaker_active": blocked,
            "cooldown_remaining_seconds": remaining,
            "cooldown_reason": reason,
            "last_status_code": self.last_status_code,
            "last_latency_ms": self.last_latency_ms,
            "last_tested_at": self.last_tested_at.isoformat() if self.last_tested_at else None,
        }


# Global singleton instance
antiban_client = AntiBanVintedClient()

