"""Unit tests for CLI and orchestration."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from http_client import Session
from download_pubmed_pdfs import (
    Logger,
    is_doi_identifier,
    sanitize_filename,
    download_pubmed_article,
)


class TestCli(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.session = Session(timeout=5)
        self.mock_pdf = b"%PDF-1.4 " + b"Z" * 300

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_identifier_classification(self):
        self.assertTrue(is_doi_identifier("10.1093/jac/dkn123"))
        self.assertTrue(is_doi_identifier("doi:10.1128/aac.47.7.2138"))
        self.assertTrue(is_doi_identifier("example/123"))
        self.assertFalse(is_doi_identifier("24592257"))
        self.assertFalse(is_doi_identifier("12821459"))

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename("10.1093/jac/dkn123"), "10.1093_jac_dkn123")
        self.assertEqual(sanitize_filename("24592257"), "24592257")

    def test_logger_context_manager(self):
        log_file = os.path.join(self.test_dir, "test.log")
        with Logger(log_file) as logger:
            logger.log("Hello log", to_console=False, pmid="123")
        self.assertTrue(os.path.exists(log_file))
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("[123] Hello log", content)

    @patch("download_pubmed_pdfs.resolve_pmid_metadata")
    @patch("download_pubmed_pdfs.try_pmc_download")
    def test_resume_skip_existing(self, mock_pmc, mock_meta):
        mock_meta.return_value = {
            "pmid": "11111111",
            "title": "Existing Paper",
            "doi": "10.1000/1",
            "pmcid": "PMC1111111",
            "journal": "Nature",
            "is_oa": True
        }
        mock_pmc.return_value = (self.mock_pdf, "https://pmc.gov/pdf")

        # 1. First download succeeds and creates file
        res1 = download_pubmed_article(
            identifier="11111111",
            output_dir=self.test_dir,
            session=self.session,
            email="test@example.com",
            force=False
        )
        self.assertEqual(res1["status"], "success")
        self.assertFalse(res1.get("skipped_existing", False))
        self.assertEqual(mock_pmc.call_count, 1)

        # 2. Second download with force=False skips
        res2 = download_pubmed_article(
            identifier="11111111",
            output_dir=self.test_dir,
            session=self.session,
            email="test@example.com",
            force=False
        )
        self.assertEqual(res2["status"], "success")
        self.assertTrue(res2.get("skipped_existing", False))
        # mock_pmc should NOT have been called again
        self.assertEqual(mock_pmc.call_count, 1)

        # 3. Third download with force=True re-downloads
        res3 = download_pubmed_article(
            identifier="11111111",
            output_dir=self.test_dir,
            session=self.session,
            email="test@example.com",
            force=True
        )
        self.assertEqual(res3["status"], "success")
        self.assertFalse(res3.get("skipped_existing", False))
        self.assertEqual(mock_pmc.call_count, 2)

    def test_cli_new_arguments(self):
        import argparse
        import sys

        # Verify arguments can be parsed without error
        test_argv = [
            "download_pubmed_pdfs.py",
            "--pmids", "12345678",
            "--output-dir", self.test_dir,
            "--ncbi-api-key", "my_secret_key",
            "--ncbi-email", "me@domain.edu",
            "--save-html-samples", os.path.join(self.test_dir, "custom_samples")
        ]
        with patch.object(sys, "argv", test_argv):
            from download_pubmed_pdfs import main
            # Mock process_item or exits to test argument parsing up to execution
            with patch("download_pubmed_pdfs.download_pubmed_article") as mock_dl:
                mock_dl.return_value = {"status": "success", "identifier": "12345678"}
                with patch("sys.exit") as mock_exit:
                    main()
                    mock_exit.assert_called_with(0)


if __name__ == "__main__":
    unittest.main()
