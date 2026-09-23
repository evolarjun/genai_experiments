"""Unit tests for http_client module."""

import http.cookiejar
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

from http_client import (
    is_valid_pdf,
    parse_curl_command,
    load_cookie_file,
    DomainRateLimiter,
    Session,
)


class TestHttpClient(unittest.TestCase):

    def test_is_valid_pdf(self):
        # Empty or None
        self.assertFalse(is_valid_pdf(None))
        self.assertFalse(is_valid_pdf(b""))

        # Too short (< 100 bytes)
        self.assertFalse(is_valid_pdf(b"%PDF-1.4 short"))

        # HTML content
        html_payload = b"<html><body><h1>Access Denied</h1></body></html>" * 5
        self.assertFalse(is_valid_pdf(html_payload))

        # Valid PDF starting with %PDF-
        valid_pdf = b"%PDF-1.5\n" + b"0" * 200
        self.assertTrue(is_valid_pdf(valid_pdf))

        # Valid PDF with %PDF- in first 1024 bytes (e.g. slight leading whitespace)
        pdf_with_preamble = b"\r\n\t%PDF-1.4\n" + b"1" * 200
        self.assertTrue(is_valid_pdf(pdf_with_preamble))

    def test_parse_curl_command(self):
        curl_cmd = (
            "curl 'https://publisher.com/article.pdf' "
            "-H 'User-Agent: CustomBrowser/1.0' "
            "-H 'Cookie: session_id=xyz123; ezproxy=abc'"
        )
        cookie, ua = parse_curl_command(curl_cmd)
        self.assertEqual(cookie, "session_id=xyz123; ezproxy=abc")
        self.assertEqual(ua, "CustomBrowser/1.0")

        # Test alternative curl syntax (-b and -A)
        curl_cmd_alt = "curl 'https://publisher.com' -b 'token=456' -A 'TestAgent/2.0'"
        cookie_alt, ua_alt = parse_curl_command(curl_cmd_alt)
        self.assertEqual(cookie_alt, "token=456")
        self.assertEqual(ua_alt, "TestAgent/2.0")

        # Test empty or non-matching curl
        cookie_none, ua_none = parse_curl_command("curl https://example.com")
        self.assertIsNone(cookie_none)
        self.assertIsNone(ua_none)

    def test_domain_rate_limiter(self):
        limiter = DomainRateLimiter(default_delay=0.05)
        start = time.time()
        limiter.wait("https://api.example.com/one")
        limiter.wait("https://api.example.com/two")
        elapsed = time.time() - start
        self.assertGreaterEqual(elapsed, 0.04)

        # Different domain should not block
        start_diff = time.time()
        limiter.wait("https://otherdomain.com/test")
        diff_elapsed = time.time() - start_diff
        self.assertLess(diff_elapsed, 0.05)

    def test_domain_rate_limiter_domain_delays(self):
        limiter = DomainRateLimiter(
            default_delay=0.1,
            domain_delays={"fast.example.com": 0.01}
        )
        # Fast domain should respect custom delay
        start_fast = time.time()
        limiter.wait("https://fast.example.com/one")
        limiter.wait("https://fast.example.com/two")
        fast_elapsed = time.time() - start_fast
        self.assertGreaterEqual(fast_elapsed, 0.008)
        self.assertLess(fast_elapsed, 0.08)

        # Dynamic delay update
        limiter.set_domain_delay("dynamic.example.com", 0.02)
        start_dyn = time.time()
        limiter.wait("https://dynamic.example.com/a")
        limiter.wait("https://dynamic.example.com/b")
        dyn_elapsed = time.time() - start_dyn
        self.assertGreaterEqual(dyn_elapsed, 0.018)

    def test_load_cookie_file(self):
        cj = http.cookiejar.CookieJar()
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".txt") as tf:
            tf.write(
                "# Netscape HTTP Cookie File\n"
                ".example.com\tTRUE\t/\tFALSE\t2147483647\ttest_cookie\tvalue123\n"
            )
            tf.flush()
            tmp_path = tf.name

        try:
            success = load_cookie_file(tmp_path, cj)
            self.assertTrue(success)
            cookies = list(cj)
            self.assertEqual(len(cookies), 1)
            self.assertEqual(cookies[0].name, "test_cookie")
            self.assertEqual(cookies[0].value, "value123")
        finally:
            import os
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @patch("urllib.request.OpenerDirector.open")
    def test_session_retry_on_429(self, mock_open):
        import urllib.error

        # First call raises 429, second call succeeds
        mock_resp_success = MagicMock()
        mock_resp_ctx = MagicMock()
        mock_resp_ctx.read.return_value = b"%PDF-1.4 success data" * 10
        mock_resp_ctx.headers = {}
        mock_resp_ctx.status = 200
        mock_resp_ctx.geturl.return_value = "https://example.com/paper.pdf"
        mock_resp_success.__enter__.return_value = mock_resp_ctx

        http_err = urllib.error.HTTPError(
            url="https://example.com/paper.pdf",
            code=429,
            msg="Too Many Requests",
            hdrs={"Retry-After": "0.01"},
            fp=MagicMock(read=lambda: b"rate limited")
        )

        mock_open.side_effect = [http_err, mock_resp_success]

        session = Session(max_retries=2, timeout=5)
        # Disable domain wait to speed up unit test
        session.rate_limiter = DomainRateLimiter(default_delay=0)

        content, headers, status, final_url = session.request("https://example.com/paper.pdf")
        self.assertEqual(status, 200)
        self.assertTrue(is_valid_pdf(content))
        self.assertEqual(mock_open.call_count, 2)


if __name__ == "__main__":
    unittest.main()
