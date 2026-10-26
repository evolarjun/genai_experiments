"""
Browser-based Downloader Fallback (Playwright)
==============================================
Provides optional headless browser capabilities to solve complex JavaScript
Proof-of-Work (PoW) challenges or dynamic Cloudflare/interstitial pages that
cannot be solved via plain HTTP requests.

Degrades gracefully to 'unavailable' if playwright is not installed.
"""

import os
from pathlib import Path
from typing import Optional, Tuple, Any

from http_client import USER_AGENT_DEFAULT


class BrowserDownloader:
    """
    Lazily launches a single headless Chromium browser instance via Playwright
    and reuses it across downloads that hit anti-bot or JS interstitial challenges.
    """

    def __init__(self, user_agent: str = USER_AGENT_DEFAULT):
        self.user_agent = user_agent
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._unavailable_reason: Optional[str] = None
        self._tried_launch = False

    def _ensure_launched(self) -> bool:
        if self._context is not None:
            return True
        if self._tried_launch:
            return False
        self._tried_launch = True

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._unavailable_reason = (
                "Playwright is not installed. To enable browser-based fallback, install it via: "
                "`pip install playwright && playwright install chromium`."
            )
            return False

        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._context = self._browser.new_context(user_agent=self.user_agent)
            return True
        except Exception as e:
            self._unavailable_reason = (
                f"Failed to launch headless Chromium ({e}). "
                "Ensure browser binaries are installed via `playwright install chromium`."
            )
            self._pw = None
            self._browser = None
            self._context = None
            return False

    def is_available(self) -> Tuple[bool, Optional[str]]:
        """Check if browser capability is ready or why it is unavailable."""
        ready = self._ensure_launched()
        return ready, self._unavailable_reason

    def fetch(self, url: str, referer: Optional[str] = None, timeout_ms: int = 45000) -> Tuple[Optional[bytes], Optional[str]]:
        """
        Navigate to referer and download url using headless Chromium.
        Returns (content_bytes, error_message).
        """
        if not self._ensure_launched():
            return None, self._unavailable_reason

        page = None
        try:
            page = self._context.new_page()

            # Navigate to referer first to allow session / PoW cookies to be set
            if referer:
                try:
                    page.goto(referer, timeout=timeout_ms, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                except Exception:
                    pass

            # Intercept download
            try:
                with page.expect_download(timeout=timeout_ms) as download_info:
                    try:
                        page.goto(url, timeout=timeout_ms)
                    except Exception:
                        pass  # Downloads often trigger navigation abortion in playwright
                download = download_info.value
                tmp_path = download.path()
                if not tmp_path or not os.path.exists(tmp_path):
                    return None, "Browser reported download finished, but no file path was captured"
                data = Path(tmp_path).read_bytes()
                # Check if download is HTML interstitial rather than requested file
                low_start = data[:100].lower()
                if b"<!doctype html" in low_start or b"<html" in low_start:
                    return None, "Browser download returned an HTML interstitial instead of target file"
                return data, None
            except Exception as e:
                return None, f"Browser download failed or timed out: {e}"

        except Exception as e:
            return None, f"Browser execution error: {e}"
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass

    def close(self):
        """Close browser context and stop Playwright."""
        for obj in (self._context, self._browser):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw = None
        self._browser = None
        self._context = None
