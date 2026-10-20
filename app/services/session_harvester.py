"""Automated Vinted session and Cloudflare clearance harvester using native Chrome engine."""

import logging
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

from .http_client import antiban_client

logger = logging.getLogger(__name__)

VINTED_URL = "https://www.vinted.co.uk/catalog?search_text=shoes"
MAC_CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SessionHarvester:
    """Automates harvesting of Cloudflare clearance and Vinted web session tokens.

    Uses a two-phase native Chrome launch (untouched by CDP during the initial
    Cloudflare Turnstile handshake, followed by post-solve CDP attachment) so that
    datacenter proxies pass Cloudflare without triggering CDP Runtime.enable checks.
    """

    def __init__(self):
        self._lock = threading.Lock()

    def harvest(self, headless: bool = True, timeout_seconds: int = 25) -> Dict[str, Any]:
        """Launch Chrome through the active proxy to solve Cloudflare and harvest cookies."""
        with self._lock:
            active_proxy = antiban_client.get_active_proxy()
            logger.info(
                "Starting automated Vinted session harvest (proxy=%s)...",
                antiban_client._mask_proxy(active_proxy),
            )

            if os.path.exists(MAC_CHROME_BINARY):
                return self._harvest_native_chrome(active_proxy, timeout_seconds)
            return self._harvest_playwright_fallback(active_proxy, headless, timeout_seconds)

    def _harvest_native_chrome(self, active_proxy: Optional[str], timeout_seconds: int) -> Dict[str, Any]:
        """Launch native Chrome without CDP attached during Turnstile execution."""
        tmp_dir = tempfile.mkdtemp(prefix="vinted_chrome_harvest_")
        port = _find_free_port()
        proc: Optional[subprocess.Popen] = None

        try:
            cmd = [
                MAC_CHROME_BINARY,
                f"--user-data-dir={tmp_dir}",
                f"--remote-debugging-port={port}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
                "--window-position=-2400,-2400",
                "--window-size=1280,800",
            ]

            if active_proxy:
                parsed = urlparse(active_proxy)
                if parsed.hostname and parsed.port:
                    proxy_server = f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port}"
                    cmd.append(f"--proxy-server={proxy_server}")

            cmd.append(VINTED_URL)

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Phase 1: Wait untouched by CDP so Cloudflare Turnstile completes cleanly
            time.sleep(6.5)

            # Phase 2: Connect over CDP after Turnstile has resolved to extract cookies & UA
            captured_cookies = []
            ua = ""
            cleared = False

            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.pages[0] if context.pages else context.new_page()

                max_ticks = max(4, int(timeout_seconds - 6))
                for _ in range(max_ticks):
                    try:
                        title = page.title()
                        ua = page.evaluate("navigator.userAgent")
                        cookies = context.cookies("https://www.vinted.co.uk")
                        names = {c["name"] for c in cookies}

                        if "access_token_web" in names or (
                            "cf_clearance" in names
                            and "just a moment" not in title.lower()
                            and "please wait" not in title.lower()
                        ):
                            cleared = True
                            captured_cookies = cookies
                            break
                    except Exception:
                        pass
                    page.wait_for_timeout(1000)

                if not captured_cookies:
                    try:
                        captured_cookies = context.cookies("https://www.vinted.co.uk")
                        ua = ua or page.evaluate("navigator.userAgent")
                        names = {c["name"] for c in captured_cookies}
                        if "cf_clearance" in names or "access_token_web" in names:
                            cleared = True
                    except Exception:
                        pass

                browser.close()

            if not cleared or not captured_cookies:
                return {
                    "ok": False,
                    "cookies_count": len(captured_cookies),
                    "message": "Cloudflare clearance could not be completed within timeout.",
                }

            cookie_pairs = [f"{c['name']}={c['value']}" for c in captured_cookies]
            cookie_str = "; ".join(cookie_pairs)

            antiban_client.save_config(
                proxy_str="\n".join(antiban_client._proxy_list),
                cookie_str=cookie_str,
                user_agent=ua,
            )

            logger.info(
                "Successfully harvested %d Vinted session cookies (UA=%s, proxy=%s).",
                len(captured_cookies),
                ua,
                antiban_client._mask_proxy(active_proxy),
            )
            return {
                "ok": True,
                "cookies_count": len(captured_cookies),
                "user_agent": ua,
                "message": f"Successfully harvested {len(captured_cookies)} fresh session cookies via {antiban_client._mask_proxy(active_proxy)}.",
            }

        except Exception as exc:
            logger.error("Native Chrome session harvest failed: %s", exc)
            return {
                "ok": False,
                "cookies_count": 0,
                "message": f"Session harvest failed: {exc}",
            }
        finally:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=4)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _harvest_playwright_fallback(
        self, active_proxy: Optional[str], headless: bool, timeout_seconds: int
    ) -> Dict[str, Any]:
        """Fallback harvester if standard Google Chrome binary path is unavailable."""
        try:
            with sync_playwright() as p:
                pw_proxy = None
                if active_proxy:
                    parsed = urlparse(active_proxy)
                    if parsed.hostname and parsed.port:
                        pw_proxy = {"server": f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port}"}
                        if parsed.username:
                            pw_proxy["username"] = parsed.username
                        if parsed.password:
                            pw_proxy["password"] = parsed.password

                browser = p.chromium.launch(
                    headless=headless,
                    proxy=pw_proxy,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
                context = browser.new_context(locale="en-GB", viewport={"width": 1280, "height": 800})
                page = context.new_page()
                ua = page.evaluate("navigator.userAgent")
                page.goto(VINTED_URL, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)

                captured_cookies = []
                for _ in range(timeout_seconds):
                    page.wait_for_timeout(1000)
                    cookies = context.cookies("https://www.vinted.co.uk")
                    names = {c["name"] for c in cookies}
                    if "access_token_web" in names or "cf_clearance" in names:
                        captured_cookies = cookies
                        break

                browser.close()
                if not captured_cookies:
                    return {"ok": False, "cookies_count": 0, "message": "Timeout waiting for clearance."}

                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in captured_cookies)
                antiban_client.save_config(
                    proxy_str="\n".join(antiban_client._proxy_list),
                    cookie_str=cookie_str,
                    user_agent=ua,
                )
                return {
                    "ok": True,
                    "cookies_count": len(captured_cookies),
                    "user_agent": ua,
                    "message": f"Harvested {len(captured_cookies)} cookies.",
                }
        except Exception as exc:
            return {"ok": False, "cookies_count": 0, "message": f"Fallback harvest failed: {exc}"}


session_harvester = SessionHarvester()
