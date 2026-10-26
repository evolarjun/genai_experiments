import json
import unittest
from unittest.mock import MagicMock

from http_client import Session
from metadata import fetch_bvbrc_genomes


class TestBvbrc(unittest.TestCase):

    def setUp(self):
        self.session = Session()

    def test_fetch_bvbrc_genomes(self):
        sample_bvbrc_response = [
            {
                "genome_id": "1280.123",
                "bioproject_accession": "PRJNA12345",
                "biosample_accession": "SAMN00112233",
                "assembly_accession": "GCA_001234565.1",
                "genbank_accessions": "CP001234,CP001235",
                "refseq_accessions": "NC_001234.1"
            },
            {
                "genome_id": "1280.456",
                "bioproject_accession": "PRJNA12345",
                "biosample_accession": "SAMN00112234",
                "assembly_accession": "GCA_001234566.1",
                "genbank_accessions": "CP001236",
                "refseq_accessions": "NC_001235.1"
            }
        ]
        self.session.request = MagicMock(return_value=(
            json.dumps(sample_bvbrc_response).encode("utf-8"),
            {},
            200,
            "https://www.bv-brc.org/api/genome/"
        ))

        res = fetch_bvbrc_genomes(pmid="24592257", session=self.session)
        self.assertEqual(res["genome_ids"], ["1280.123", "1280.456"])
        self.assertEqual(res["bioproject_accessions"], ["PRJNA12345"])
        self.assertEqual(res["biosample_accessions"], ["SAMN00112233", "SAMN00112234"])
        self.assertEqual(res["assembly_accessions"], ["GCA_001234565.1", "GCA_001234566.1"])
        self.assertIn("CP001234", res["genbank_accessions"])
        self.assertIn("NC_001234.1", res["refseq_accessions"])

    def test_fetch_bvbrc_genomes_invalid_pmid(self):
        res = fetch_bvbrc_genomes(pmid="not_a_pmid", session=self.session)
        self.assertEqual(res["genome_ids"], [])


if __name__ == "__main__":
    unittest.main()
