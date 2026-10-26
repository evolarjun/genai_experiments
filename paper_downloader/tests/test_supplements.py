import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from http_client import Session
from supplements import (
    fetch_pmc_xml,
    download_single_supplement,
    download_supplements_for_article,
)
from tests.test_xml_extractor import SAMPLE_JATS_XML


class TestSupplements(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.session = Session()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_fetch_pmc_xml(self):
        self.session.request = MagicMock(return_value=(SAMPLE_JATS_XML.encode("utf-8"), {}, 200, "https://example.com"))
        xml_bytes, err = fetch_pmc_xml("PMC123456", session=self.session)
        self.assertIsNotNone(xml_bytes)
        self.assertIsNone(err)
        self.assertIn(b"Genomic epidemiology", xml_bytes)

    def test_download_single_supplement_success(self):
        binary_data = b"Dummy Excel content bytes " * 10
        self.session.request = MagicMock(return_value=(binary_data, {}, 200, "https://example.com/bin/supp.xlsx"))

        dest_file = os.path.join(self.temp_dir, "supp.xlsx")
        res = download_single_supplement(
            url="https://pmc.ncbi.nlm.nih.gov/articles/instance/123456/bin/supp.xlsx",
            dest_path=dest_file,
            referer="https://pmc.ncbi.nlm.nih.gov/articles/PMC123456/",
            session=self.session
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["size_bytes"], len(binary_data))
        self.assertTrue(os.path.exists(dest_file))

    def test_download_single_supplement_pow_challenge(self):
        # First call returns PMC PoW challenge HTML, second call returns actual binary
        pow_html = b"""
        <html><body>
        <script>
        const POW_CHALLENGE = "testchallenge123";
        const POW_DIFFICULTY = "2";
        const POW_COOKIE_NAME = "cloudpmc-viewer-pow";
        </script>
        </body></html>
        """
        binary_data = b"Actual file data bytes after solving PoW challenge" * 5

        self.session.request = MagicMock(side_effect=[
            (pow_html, {}, 200, "https://example.com/bin/data.csv"),
            (binary_data, {}, 200, "https://example.com/bin/data.csv")
        ])

        dest_file = os.path.join(self.temp_dir, "data.csv")
        res = download_single_supplement(
            url="https://pmc.ncbi.nlm.nih.gov/articles/instance/123456/bin/data.csv",
            dest_path=dest_file,
            referer="https://pmc.ncbi.nlm.nih.gov/articles/PMC123456/",
            session=self.session
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["size_bytes"], len(binary_data))
        self.assertTrue(os.path.exists(dest_file))
        # Ensure PoW cookie was set in the session cookie jar
        cookies = list(self.session.cookie_jar)
        pow_cookies = [c for c in cookies if c.name == "cloudpmc-viewer-pow"]
        self.assertTrue(len(pow_cookies) >= 1)

    def test_download_single_supplement_resume_skip(self):
        dest_file = os.path.join(self.temp_dir, "existing.xlsx")
        with open(dest_file, "wb") as f:
            f.write(b"existing payload " * 15)

        self.session.request = MagicMock()
        res = download_single_supplement(
            url="https://pmc.ncbi.nlm.nih.gov/articles/instance/123456/bin/existing.xlsx",
            dest_path=dest_file,
            referer="https://pmc.ncbi.nlm.nih.gov/articles/PMC123456/",
            session=self.session,
            force=False
        )
        self.assertEqual(res["status"], "success")
        self.assertTrue(res.get("skipped_existing"))
        self.session.request.assert_not_called()

    def test_download_supplements_for_article(self):
        binary_data = b"Sample supplement binary content for testing " * 10
        self.session.request = MagicMock(return_value=(binary_data, {}, 200, "https://example.com"))

        res = download_supplements_for_article(
            pmcid="PMC123456",
            output_dir=self.temp_dir,
            session=self.session,
            xml_bytes=SAMPLE_JATS_XML.encode("utf-8")
        )
        self.assertEqual(res["discovered"], 2)
        self.assertEqual(res["downloaded"], 2)
        self.assertEqual(res["failed"], 0)
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "supp_table1.xlsx")))
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "dataset_s2.csv")))


if __name__ == "__main__":
    unittest.main()
