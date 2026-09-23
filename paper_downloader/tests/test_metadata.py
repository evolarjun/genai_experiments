"""Unit tests for metadata resolution module."""

import json
import unittest
from unittest.mock import MagicMock

from http_client import Session
from metadata import resolve_pmid_metadata, resolve_doi_metadata, batch_resolve_pmids


class TestMetadata(unittest.TestCase):

    def setUp(self):
        self.session = Session(timeout=5)
        self.session.request = MagicMock()

    def test_resolve_pmid_metadata_europe_pmc(self):
        europe_pmc_payload = json.dumps({
            "resultList": {
                "result": [{
                    "title": "Study of AmpC Beta-Lactamases",
                    "doi": "10.1093/jac/dkn123",
                    "pmcid": "PMC1234567",
                    "journalTitle": "J Antimicrob Chemother",
                    "isOpenAccess": "Y"
                }]
            }
        }).encode("utf-8")

        self.session.request.return_value = (europe_pmc_payload, {}, 200, "https://example.com")

        meta = resolve_pmid_metadata("12345678", session=self.session)
        self.assertEqual(meta["pmid"], "12345678")
        self.assertEqual(meta["title"], "Study of AmpC Beta-Lactamases")
        self.assertEqual(meta["doi"], "10.1093/jac/dkn123")
        self.assertEqual(meta["pmcid"], "PMC1234567")
        self.assertEqual(meta["journal"], "J Antimicrob Chemother")
        self.assertTrue(meta["is_oa"])

    def test_resolve_pmid_metadata_ncbi_fallback(self):
        # Europe PMC returns result without DOI or PMCID
        europe_pmc_payload = json.dumps({
            "resultList": {
                "result": [{
                    "title": "Fallback Study",
                    "doi": None,
                    "pmcid": None,
                    "journalTitle": "Microbiology"
                }]
            }
        }).encode("utf-8")

        # NCBI ID converter payload
        ncbi_payload = json.dumps({
            "records": [{
                "pmid": "99999999",
                "doi": "10.1128/aac.0001-22",
                "pmcid": "PMC9999999"
            }]
        }).encode("utf-8")

        self.session.request.side_effect = [
            (europe_pmc_payload, {}, 200, "https://europepmc.org"),
            (ncbi_payload, {}, 200, "https://ncbi.nlm.nih.gov")
        ]

        meta = resolve_pmid_metadata("99999999", session=self.session)
        self.assertEqual(meta["doi"], "10.1128/aac.0001-22")
        self.assertEqual(meta["pmcid"], "PMC9999999")
        self.assertEqual(meta["title"], "Fallback Study")

    def test_resolve_doi_metadata(self):
        ncbi_payload = json.dumps({
            "records": [{
                "pmid": "87654321",
                "doi": "10.1016/j.cell.2023.01.001",
                "pmcid": "PMC8765432"
            }]
        }).encode("utf-8")

        crossref_payload = json.dumps({
            "message": {
                "title": ["Mechanism of CRISPR-Cas Systems"],
                "container-title": ["Cell"]
            }
        }).encode("utf-8")

        europe_pmc_payload = json.dumps({
            "resultList": {
                "result": [{
                    "title": "Mechanism of CRISPR-Cas Systems",
                    "doi": "10.1016/j.cell.2023.01.001",
                    "pmcid": "PMC8765432",
                    "journalTitle": "Cell",
                    "isOpenAccess": "Y"
                }]
            }
        }).encode("utf-8")

        self.session.request.side_effect = [
            (ncbi_payload, {}, 200, "https://ncbi.nlm.nih.gov"),
            (crossref_payload, {}, 200, "https://crossref.org"),
            (europe_pmc_payload, {}, 200, "https://europepmc.org")
        ]

        meta = resolve_doi_metadata("10.1016/j.cell.2023.01.001", session=self.session)
        self.assertEqual(meta["doi"], "10.1016/j.cell.2023.01.001")
        self.assertEqual(meta["pmid"], "87654321")
        self.assertEqual(meta["pmcid"], "PMC8765432")
        self.assertEqual(meta["title"], "Mechanism of CRISPR-Cas Systems")
        self.assertEqual(meta["journal"], "Cell")

    def test_batch_resolve_pmids(self):
        esummary_payload = json.dumps({
            "result": {
                "uids": ["24592257", "12345678"],
                "24592257": {
                    "title": "Emergence of Escherichia coli producing ESAC in animals.",
                    "authors": [{"name": "Madec JY"}],
                    "pubdate": "2014 Feb 18",
                    "source": "Front Microbiol",
                    "articleids": [
                        {"idtype": "pubmed", "value": "24592257"},
                        {"idtype": "doi", "value": "10.3389/fmicb.2014.00053"},
                        {"idtype": "pmc", "value": "PMC3924575"}
                    ]
                },
                "12345678": {
                    "title": "Another Study.",
                    "authors": [{"name": "Smith J"}],
                    "pubdate": "2020",
                    "source": "Nature",
                    "articleids": [
                        {"idtype": "doi", "value": "10.1038/s41586-020-0001"}
                    ]
                }
            }
        }).encode("utf-8")

        self.session.request.return_value = (esummary_payload, {}, 200, "https://eutils.ncbi.nlm.nih.gov")

        res = batch_resolve_pmids(
            ["24592257", "12345678"],
            session=self.session,
            ncbi_api_key="test_api_key",
            ncbi_email="test@example.com"
        )

        self.assertIn("24592257", res)
        self.assertIn("12345678", res)
        self.assertEqual(res["24592257"]["title"], "Emergence of Escherichia coli producing ESAC in animals")
        self.assertEqual(res["24592257"]["doi"], "10.3389/fmicb.2014.00053")
        self.assertEqual(res["24592257"]["pmcid"], "PMC3924575")
        self.assertEqual(res["24592257"]["first_author"], "Madec JY")
        self.assertEqual(res["24592257"]["year"], "2014")
        self.assertEqual(res["24592257"]["journal"], "Front Microbiol")

        self.assertEqual(res["12345678"]["title"], "Another Study")
        self.assertIsNone(res["12345678"]["pmcid"])
        self.assertEqual(res["12345678"]["doi"], "10.1038/s41586-020-0001")

        # Verify api_key and email were included in the request URL
        called_url = self.session.request.call_args[0][0]
        self.assertIn("api_key=test_api_key", called_url)
        self.assertIn("email=test%40example.com", called_url)

    def test_resolve_pmid_metadata_with_prefetched(self):
        prefetched = {
            "pmid": "24592257",
            "title": "Cached Title",
            "doi": "10.3389/fmicb.2014.00053",
            "pmcid": "PMC3924575",
            "journal": "Front Microbiol"
        }
        # session.request should NOT be called because title, doi, and pmcid are present
        meta = resolve_pmid_metadata("24592257", session=self.session, prefetched_meta=prefetched)
        self.assertEqual(meta["title"], "Cached Title")
        self.assertEqual(meta["doi"], "10.3389/fmicb.2014.00053")
        self.assertEqual(meta["pmcid"], "PMC3924575")
        self.session.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
