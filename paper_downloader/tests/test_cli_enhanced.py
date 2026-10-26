import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from download_pubmed_pdfs import download_pubmed_article, main
from tests.test_xml_extractor import SAMPLE_JATS_XML


class TestCliEnhanced(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("download_pubmed_pdfs.resolve_pmid_metadata")
    @patch("download_pubmed_pdfs.try_pmc_download")
    @patch("download_pubmed_pdfs.fetch_pmc_xml")
    @patch("download_pubmed_pdfs.download_supplements_for_article")
    def test_download_article_with_supplements_and_tables(
        self,
        mock_download_supps,
        mock_fetch_xml,
        mock_try_pmc,
        mock_resolve_meta
    ):
        mock_resolve_meta.return_value = {
            "pmid": "99999999",
            "pmcid": "PMC9999999",
            "title": "Test Enhanced Article",
            "journal": "Test Journal",
            "doi": "10.1000/test",
            "is_oa": True
        }
        mock_try_pmc.return_value = (b"%PDF-1.4 dummy pdf data " * 10, "https://pmc/article.pdf")
        mock_fetch_xml.return_value = (SAMPLE_JATS_XML.encode("utf-8"), None)
        mock_download_supps.return_value = {
            "discovered": 2,
            "downloaded": 2,
            "failed": 0,
            "files": [{"filename": "supp1.xlsx", "status": "success"}]
        }

        mock_session = MagicMock()
        res = download_pubmed_article(
            identifier="99999999",
            output_dir=self.temp_dir,
            session=mock_session,
            email="test@example.com",
            include_supplements=True,
            extract_tables=True,
            bvbrc=False
        )

        self.assertEqual(res["status"], "success")
        self.assertTrue(os.path.exists(res["file_path"]))

        # Verify Data Availability mining
        self.assertIn("data_availability", res)
        self.assertTrue(res["data_availability"]["section_found"])
        self.assertIn("PRJNA543210", res["data_availability"]["accessions"])

        # Verify Table extraction
        self.assertIn("tables", res)
        self.assertEqual(res["tables"]["count"], 1)
        self.assertTrue(os.path.exists(res["tables"]["files"][0]))

        # Verify Supplements info
        self.assertIn("supplements", res)
        self.assertEqual(res["supplements"]["downloaded"], 2)


if __name__ == "__main__":
    unittest.main()
