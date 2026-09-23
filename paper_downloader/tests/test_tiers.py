"""Unit tests for resolution tiers module."""

import json
import unittest
from unittest.mock import MagicMock

from http_client import Session
from tiers import (
    solve_pmc_pow,
    try_pmc_download,
    try_openalex,
    try_unpaywall,
    try_semantic_scholar,
    try_crossref_pdf_url,
    try_publisher_scrape,
)


class TestTiers(unittest.TestCase):

    def setUp(self):
        self.session = Session(timeout=5)
        self.session.request = MagicMock()
        self.mock_pdf = b"%PDF-1.4 " + b"X" * 300

    def test_solve_pmc_pow(self):
        html_challenge = (
            '<html><script>'
            'const POW_CHALLENGE = "chal12345";\n'
            'const POW_DIFFICULTY = "1";\n'
            'const POW_COOKIE_NAME = "ncbi_pow";\n'
            '</script></html>'
        )
        res = solve_pmc_pow(html_challenge)
        self.assertIsNotNone(res)
        cookie_name, cookie_val = res
        self.assertEqual(cookie_name, "ncbi_pow")
        self.assertTrue(cookie_val.startswith("chal12345,"))

    def test_try_pmc_download(self):
        landing_html = (
            '<html><head>'
            '<meta name="citation_pdf_url" content="https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/pdf/main.pdf">'
            '</head><body>PMC Article</body></html>'
        ).encode("utf-8")

        self.session.request.side_effect = [
            (landing_html, {}, 200, "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"),
            (self.mock_pdf, {}, 200, "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/pdf/main.pdf")
        ]

        res = try_pmc_download("PMC1234567", session=self.session)
        self.assertIsNotNone(res)
        content, url = res
        self.assertEqual(content, self.mock_pdf)
        self.assertEqual(url, "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/pdf/main.pdf")

    def test_try_openalex(self):
        openalex_json = json.dumps({
            "locations": [
                {"pdf_url": "https://repository.univ.edu/paper.pdf"}
            ]
        }).encode("utf-8")

        self.session.request.side_effect = [
            (openalex_json, {}, 200, "https://api.openalex.org/works/pmid:12345"),
            (self.mock_pdf, {}, 200, "https://repository.univ.edu/paper.pdf")
        ]

        res = try_openalex(pmid="12345", session=self.session)
        self.assertIsNotNone(res)
        content, url = res
        self.assertEqual(content, self.mock_pdf)
        self.assertEqual(url, "https://repository.univ.edu/paper.pdf")

    def test_try_unpaywall(self):
        unpaywall_json = json.dumps({
            "best_oa_location": {
                "url_for_pdf": "https://zenodo.org/record/123/files/article.pdf"
            },
            "oa_locations": []
        }).encode("utf-8")

        self.session.request.side_effect = [
            (unpaywall_json, {}, 200, "https://api.unpaywall.org/v2/..."),
            (self.mock_pdf, {}, 200, "https://zenodo.org/record/123/files/article.pdf")
        ]

        res = try_unpaywall(doi="10.1234/test", email="user@test.org", session=self.session)
        self.assertIsNotNone(res)
        content, url = res
        self.assertEqual(content, self.mock_pdf)
        self.assertEqual(url, "https://zenodo.org/record/123/files/article.pdf")

    def test_try_semantic_scholar(self):
        s2_json = json.dumps({
            "openAccessPdf": {
                "url": "https://pdfs.semanticscholar.org/abc/def.pdf"
            }
        }).encode("utf-8")

        self.session.request.side_effect = [
            (s2_json, {}, 200, "https://api.semanticscholar.org/..."),
            (self.mock_pdf, {}, 200, "https://pdfs.semanticscholar.org/abc/def.pdf")
        ]

        res = try_semantic_scholar(pmid="9999", session=self.session)
        self.assertIsNotNone(res)
        content, url = res
        self.assertEqual(content, self.mock_pdf)
        self.assertEqual(url, "https://pdfs.semanticscholar.org/abc/def.pdf")

    def test_try_crossref_pdf_url(self):
        crossref_json = json.dumps({
            "message": {
                "link": [
                    {"URL": "https://publisher.com/fulltext.html", "content-type": "text/html"},
                    {"URL": "https://publisher.com/article.pdf", "content-type": "application/pdf"}
                ]
            }
        }).encode("utf-8")

        self.session.request.return_value = (crossref_json, {}, 200, "https://api.crossref.org/works/10.1234/test")
        urls = try_crossref_pdf_url("10.1234/test", session=self.session)
        self.assertEqual(urls, ["https://publisher.com/article.pdf"])

    def test_try_publisher_scrape(self):
        publisher_html = (
            '<html><head>'
            '<meta name="citation_pdf_url" content="https://academic.oup.com/jac/article-pdf/123.pdf">'
            '</head><body>Journal Page</body></html>'
        ).encode("utf-8")

        # 1. Landing request returns HTML
        # 2. CrossRef request returns empty
        # 3. PDF request returns PDF bytes
        self.session.request.side_effect = [
            (publisher_html, {}, 200, "https://academic.oup.com/jac/article/123"),
            (b"{}", {}, 200, "https://api.crossref.org"),
            (self.mock_pdf, {}, 200, "https://academic.oup.com/jac/article-pdf/123.pdf")
        ]

        res = try_publisher_scrape(doi="10.1093/jac/123", session=self.session)
        self.assertIsNotNone(res)
        content, url = res
        self.assertEqual(content, self.mock_pdf)
        self.assertEqual(url, "https://academic.oup.com/jac/article-pdf/123.pdf")

    def test_try_pmc_download_referer(self):
        landing_html = (
            '<html><head>'
            '<meta name="citation_pdf_url" content="https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9999/pdf/test.pdf">'
            '</head><body>PMC Article</body></html>'
        ).encode("utf-8")

        self.session.request.side_effect = [
            (landing_html, {}, 200, "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9999/"),
            (self.mock_pdf, {}, 200, "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9999/pdf/test.pdf")
        ]

        res = try_pmc_download("PMC9999", session=self.session)
        self.assertIsNotNone(res)
        # Check that the second call (PDF fetch) passed Referer header
        second_call_kwargs = self.session.request.call_args_list[1][1]
        self.assertIn("headers", second_call_kwargs)
        self.assertEqual(second_call_kwargs["headers"].get("Referer"), "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9999/")

    def test_check_and_log_payload_saves_html(self):
        import os
        import tempfile
        from tiers import _check_and_log_payload

        html_bytes = b"<!DOCTYPE html><html><body><h1>Anti-bot challenge</h1></body></html>"
        with tempfile.TemporaryDirectory() as tmp_dir:
            is_valid = _check_and_log_payload(
                html_bytes,
                url="https://publisher.com/block",
                pmid="12345",
                save_html_dir=tmp_dir,
                tier_name="test_tier"
            )
            self.assertFalse(is_valid)
            saved_file = os.path.join(tmp_dir, "12345_test_tier.html")
            self.assertTrue(os.path.exists(saved_file))
            with open(saved_file, "rb") as f:
                self.assertEqual(f.read(), html_bytes)


if __name__ == "__main__":
    unittest.main()
