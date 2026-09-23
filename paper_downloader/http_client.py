"""
HTTP Client and Session Management for PubMed PDF Downloader
============================================================
Provides thread-safe HTTP request execution with:
- Opener reuse
- SSL verification control
- Scoped cookie handling
- Per-domain rate limiting
- Automatic retries with exponential backoff for transient errors
- PDF validation
- Netscape cookie file and cURL command parsing
"""

import http.cookiejar
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Any, Optional, Tuple, List

USER_AGENT_DEFAULT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class DomainRateLimiter:
    """Thread-safe per-domain rate limiter with optional per-domain delay overrides."""

    def __init__(self, default_delay: float = 0.5, domain_delays: Optional[Dict[str, float]] = None):
        self.default_delay = default_delay
        self.domain_delays: Dict[str, float] = dict(domain_delays) if domain_delays else {}
        self._last_access: Dict[str, float] = {}
        self._lock = threading.Lock()

    def set_domain_delay(self, domain: str, delay: float) -> None:
        """Set or update a specific rate limit delay for a single domain."""
        with self._lock:
            self.domain_delays[domain.lower()] = delay

    def wait(self, url: str, delay: Optional[float] = None) -> None:
        try:
            domain = urllib.parse.urlparse(url).netloc.lower()
        except Exception:
            domain = "default"

        if delay is not None:
            target_delay = delay
        else:
            target_delay = self.domain_delays.get(domain, self.default_delay)

        if target_delay <= 0:
            return

        with self._lock:
            now = time.time()
            last_time = self._last_access.get(domain, 0.0)
            elapsed = now - last_time
            wait_time = target_delay - elapsed
            if wait_time > 0:
                time.sleep(wait_time)
            self._last_access[domain] = time.time()


def is_valid_pdf(content: Optional[bytes]) -> bool:
    """
    Verify that payload is non-empty and contains standard PDF magic bytes (%PDF).
    Threshold is 100 bytes to accommodate minimal valid PDFs or errata.
    """
    if not content or len(content) < 100:
        return False
    return content.startswith(b"%PDF") or b"%PDF-" in content[:1024]


def parse_curl_command(curl_str: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse a 'Copy as cURL' command string from browser DevTools to extract (cookie_str, user_agent)."""
    cookie_str = None
    user_agent = None

    # Match -H 'cookie: ...' or -H "Cookie: ..." or --header 'cookie: ...' or -b '...'
    cookie_match = re.search(
        r'(?:-H|--header)\s+["\']cookie:\s*([^"\']+)["\']',
        curl_str,
        re.IGNORECASE
    )
    if not cookie_match:
        cookie_match = re.search(
            r'(?:-b|--cookie)\s+["\']([^"\']+)["\']',
            curl_str,
            re.IGNORECASE
        )
    if cookie_match:
        cookie_str = cookie_match.group(1).strip()

    ua_match = re.search(
        r'(?:-H|--header)\s+["\']user-agent:\s*([^"\']+)["\']',
        curl_str,
        re.IGNORECASE
    )
    if not ua_match:
        ua_match = re.search(
            r'(?:-A|--user-agent)\s+["\']([^"\']+)["\']',
            curl_str,
            re.IGNORECASE
        )
    if ua_match:
        user_agent = ua_match.group(1).strip()

    return cookie_str, user_agent


def load_cookie_file(cookie_file_path: str, cookie_jar: http.cookiejar.CookieJar) -> bool:
    """Load cookies from a Netscape/Mozilla cookies.txt file into the provided cookie jar."""
    if not os.path.exists(cookie_file_path):
        return False
    try:
        cj = http.cookiejar.MozillaCookieJar(cookie_file_path)
        cj.load(ignore_discard=True, ignore_expires=True)
        for cookie in cj:
            cookie_jar.set_cookie(cookie)
        return True
    except Exception:
        # Fallback: check if file has simple key=value cookies
        try:
            with open(cookie_file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return True
        except Exception:
            pass
        return False


class Session:
    """
    Manages HTTP request state, connection openers, cookies, and retry logic.
    """

    def __init__(
        self,
        user_agent: str = USER_AGENT_DEFAULT,
        verify_ssl: bool = True,
        proxy: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 3,
        rate_limiter: Optional[DomainRateLimiter] = None,
        domain_delays: Optional[Dict[str, float]] = None
    ):
        self.user_agent = user_agent
        self.verify_ssl = verify_ssl
        self.proxy = proxy
        self.timeout = timeout
        self.max_retries = max_retries
        self.rate_limiter = rate_limiter or DomainRateLimiter(domain_delays=domain_delays)

        # Cookie jar for session cookies
        self.cookie_jar = http.cookiejar.CookieJar()

        # Build SSL Context
        self.ssl_ctx = ssl.create_default_context()
        if not self.verify_ssl:
            self.ssl_ctx.check_hostname = False
            self.ssl_ctx.verify_mode = ssl.CERT_NONE

        # Build reusable openers
        # 1. Standard opener with cookie persistence
        handlers_with_cookies: List[Any] = [
            urllib.request.HTTPCookieProcessor(self.cookie_jar),
            urllib.request.HTTPSHandler(context=self.ssl_ctx),
            urllib.request.HTTPHandler(),
        ]
        if self.proxy:
            handlers_with_cookies.append(
                urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
            )
        self.opener_with_cookies = urllib.request.build_opener(*handlers_with_cookies)

        # 2. Scoped opener without cookies (prevents accidental institutional cookie leakage)
        handlers_without_cookies: List[Any] = [
            urllib.request.HTTPSHandler(context=self.ssl_ctx),
            urllib.request.HTTPHandler(),
        ]
        if self.proxy:
            handlers_without_cookies.append(
                urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
            )
        self.opener_without_cookies = urllib.request.build_opener(*handlers_without_cookies)

    def _downgrade_ssl(self) -> None:
        """Rebuild openers with SSL verification disabled (auto-fallback)."""
        self.verify_ssl = False
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

        handlers_with_cookies: List[Any] = [
            urllib.request.HTTPCookieProcessor(self.cookie_jar),
            urllib.request.HTTPSHandler(context=self.ssl_ctx),
            urllib.request.HTTPHandler(),
        ]
        if self.proxy:
            handlers_with_cookies.append(
                urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
            )
        self.opener_with_cookies = urllib.request.build_opener(*handlers_with_cookies)

        handlers_without_cookies: List[Any] = [
            urllib.request.HTTPSHandler(context=self.ssl_ctx),
            urllib.request.HTTPHandler(),
        ]
        if self.proxy:
            handlers_without_cookies.append(
                urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
            )
        self.opener_without_cookies = urllib.request.build_opener(*handlers_without_cookies)

    def request(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        use_cookies: bool = True,
        cookie_str: Optional[str] = None,
        delay: Optional[float] = None
    ) -> Tuple[Optional[bytes], Dict[str, str], int, str]:
        """
        Execute an HTTP GET request with retry backoff on transient errors (429, 500, 502, 503).
        Returns (content_bytes, headers_dict, status_code, final_url).
        """
        req_headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if headers:
            req_headers.update(headers)
        if cookie_str and use_cookies:
            req_headers["Cookie"] = cookie_str

        opener = self.opener_with_cookies if use_cookies else self.opener_without_cookies
        last_status = 500
        last_headers: Dict[str, str] = {}
        final_url = url

        for attempt in range(self.max_retries + 1):
            if self.rate_limiter:
                self.rate_limiter.wait(url, delay=delay)

            req = urllib.request.Request(url, headers=req_headers)
            try:
                with opener.open(req, timeout=self.timeout) as resp:
                    content = resp.read()
                    resp_headers = dict(resp.headers)
                    final_url = resp.geturl()
                    return content, resp_headers, resp.status, final_url
            except urllib.error.HTTPError as e:
                last_status = e.code
                last_headers = dict(e.headers) if hasattr(e, "headers") else {}
                final_url = getattr(e, "url", url) or url

                # Transient errors that warrant retry
                if last_status in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    retry_after_str = last_headers.get("Retry-After")
                    sleep_time = (2 ** attempt) * 0.5
                    if retry_after_str:
                        try:
                            sleep_time = max(float(retry_after_str), sleep_time)
                        except ValueError:
                            pass
                    time.sleep(sleep_time)
                    continue

                try:
                    error_content = e.read()
                    return error_content, last_headers, last_status, final_url
                except Exception:
                    return None, last_headers, last_status, final_url

            except (urllib.error.URLError, TimeoutError, OSError) as e:
                # Detect SSL certificate verification failures — these are
                # permanent and must not be retried.  On macOS the Python
                # framework build ships without root certificates; the user
                # must either run "Install Certificates.command" from the
                # Python install folder or pass --no-verify-ssl.
                is_ssl_error = False
                cause = getattr(e, "reason", None) or e
                if isinstance(cause, ssl.SSLCertVerificationError):
                    is_ssl_error = True
                elif isinstance(cause, ssl.SSLError):
                    is_ssl_error = True
                elif "CERTIFICATE_VERIFY_FAILED" in str(e):
                    is_ssl_error = True

                if is_ssl_error:
                    if self.verify_ssl:
                        print(
                            "[SSL Error] Certificate verification failed. "
                            "This usually means Python's root certificates "
                            "are not installed.\n"
                            "  Automatic workaround: disabling SSL "
                            "verification for the remainder of this run.\n"
                            "  Permanent fix: run  "
                            "'/Applications/Python 3.x/Install "
                            "Certificates.command'  or pass --no-verify-ssl."
                        )
                        self._downgrade_ssl()
                        # Retry this one request with the downgraded context
                        continue
                    # Already unverified — nothing more we can do.
                    return None, {}, 495, url

                last_status = 504 if isinstance(e, TimeoutError) else 500
                if attempt < self.max_retries:
                    time.sleep((2 ** attempt) * 0.5)
                    continue
                return None, {}, last_status, url

            except Exception:
                return None, {}, 500, url

        return None, last_headers, last_status, final_url
