import os
import shutil
import tempfile
import unittest

from xml_extractor import (
    is_pmc_xml_restricted,
    extract_supplement_files,
    extract_tables_from_xml,
    extract_data_availability_accessions,
    save_tables_to_tsv,
)

SAMPLE_JATS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Archiving and Interchange DTD v1.2 20190208//EN" "JATS-archivearticle1.dtd">
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <front>
    <article-meta>
      <title-group>
        <article-title>Genomic epidemiology of MRSA</article-title>
      </title-group>
    </article-meta>
  </front>
  <body>
    <sec>
      <title>Results</title>
      <p>Susceptibility results are shown below.</p>
      <table-wrap id="T1" position="float">
        <label>Table 1</label>
        <caption><p>Antimicrobial susceptibility profile of clinical isolates</p></caption>
        <table>
          <thead>
            <tr><th>Isolate</th><th>Drug</th><th>MIC (mg/L)</th><th>Interpretation</th></tr>
          </thead>
          <tbody>
            <tr><td>ISO001</td><td>Vancomycin</td><td>1.0</td><td>S</td></tr>
            <tr><td>ISO002</td><td>Ciprofloxacin</td><td>&gt;32</td><td>R</td></tr>
          </tbody>
        </table>
      </table-wrap>
      <supplementary-material id="supp1" xlink:href="supp_table1.xlsx" mimetype="application" mime-subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet">
        <label>Supplementary Table S1</label>
        <caption><p>Complete MIC data across 50 antibiotics.</p></caption>
      </supplementary-material>
      <supplementary-material id="supp2">
        <media xlink:href="dataset_s2.csv" mimetype="text" mime-subtype="csv"/>
        <license xlink:href="http://creativecommons.org/licenses/by/4.0/"/>
      </supplementary-material>
    </sec>
    <sec sec-type="data-availability">
      <title>Data Availability</title>
      <p>Raw sequencing reads were deposited under BioProject PRJNA543210 and SRA run SRR9876543. BioSample accessions are SAMN12345678 and SAMN12345679. Assemblies are available at GCA_000123455.1.</p>
    </sec>
  </body>
</article>
"""

RESTRICTED_ASM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<article>
  <front>
    <article-meta>
      <title-group>
        <article-title>Restricted paper title</article-title>
      </title-group>
      <custom-meta-group>
        <custom-meta>
          <meta-name>publisher-restriction</meta-name>
          <meta-value>The publisher of this article does not allow downloading of the full text in XML.</meta-value>
        </custom-meta>
      </custom-meta-group>
    </article-meta>
  </front>
</article>
"""


class TestXmlExtractor(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_restricted_xml_detection(self):
        is_restr, reason = is_pmc_xml_restricted(RESTRICTED_ASM_XML)
        self.assertTrue(is_restr)
        self.assertIn("does not allow downloading", reason)

        is_restr2, _ = is_pmc_xml_restricted(SAMPLE_JATS_XML)
        self.assertFalse(is_restr2)

        # Front-matter only without body
        front_only = "<article><front><title>Front only</title></front></article>"
        is_restr3, reason3 = is_pmc_xml_restricted(front_only)
        self.assertTrue(is_restr3)
        self.assertIn("missing <body>", reason3)

    def test_extract_supplement_files(self):
        supps = extract_supplement_files(SAMPLE_JATS_XML)
        self.assertEqual(supps, ["supp_table1.xlsx", "dataset_s2.csv"])
        # Ensure http CC license href was not included
        self.assertNotIn("http://creativecommons.org/licenses/by/4.0/", supps)

    def test_extract_tables_from_xml(self):
        tables = extract_tables_from_xml(SAMPLE_JATS_XML)
        self.assertEqual(len(tables), 1)
        t = tables[0]
        self.assertEqual(t["label"], "Table 1")
        self.assertIn("Antimicrobial susceptibility profile", t["caption"])
        self.assertEqual(len(t["rows"]), 3)  # header + 2 data rows
        self.assertEqual(t["rows"][0], ["Isolate", "Drug", "MIC (mg/L)", "Interpretation"])
        self.assertEqual(t["rows"][1], ["ISO001", "Vancomycin", "1.0", "S"])
        self.assertEqual(t["rows"][2], ["ISO002", "Ciprofloxacin", ">32", "R"])

    def test_save_tables_to_tsv(self):
        tables = extract_tables_from_xml(SAMPLE_JATS_XML)
        saved = save_tables_to_tsv(tables, self.temp_dir)
        self.assertEqual(len(saved), 1)
        self.assertTrue(os.path.exists(saved[0]))
        with open(saved[0], "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("# Table 1: Antimicrobial susceptibility profile", content)
        self.assertIn("Isolate\tDrug\tMIC (mg/L)\tInterpretation", content)
        self.assertIn("ISO001\tVancomycin\t1.0\tS", content)

    def test_extract_data_availability_accessions(self):
        accessions, found = extract_data_availability_accessions(SAMPLE_JATS_XML)
        self.assertTrue(found)
        self.assertIn("PRJNA543210", accessions)
        self.assertIn("SRR9876543", accessions)
        self.assertIn("SAMN12345678", accessions)
        self.assertIn("SAMN12345679", accessions)
        self.assertIn("GCA_000123455.1", accessions)


if __name__ == "__main__":
    unittest.main()
