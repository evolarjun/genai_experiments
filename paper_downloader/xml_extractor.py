"""
PMC Full-Text XML Extractor
===========================
Parses PubMed Central (PMC) JATS XML to extract:
  - Supplementary material file references
  - Embedded main-text tables (<table-wrap>)
  - Data Availability statements & repository accessions (BioProject, BioSample, SRA, etc.)
  - Full-text API restriction detection (e.g. ASM journals)
"""

import os
import re
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional, Tuple

DATA_AVAILABILITY_HEADING_RE = re.compile(
    r"data\s+availab|availability\s+of\s+data|accession\s+number|data\s+deposition|sequence\s+data\s+availab",
    re.IGNORECASE,
)

DATA_AVAIL_ACCESSION_RE = re.compile(
    r"\b(SAMN\d+|SAMEA\d+|SAMD\d+|PRJNA\d+|PRJEB\d+|PRJDB\d+|"
    r"SRR\d+|ERR\d+|DRR\d+|SRX\d+|ERX\d+|DRX\d+|"
    r"GCA_\d+(?:\.\d+)?|GCF_\d+(?:\.\d+)?|"
    r"(?:NZ_|NC_)[A-Z]{2,4}\d+(?:\.\d+)?)\b"
)


def _local(tag: str) -> str:
    """Strip XML namespace prefix from tag name."""
    return tag.rsplit("}", 1)[-1]


def is_pmc_xml_restricted(xml_bytes_or_str: Any) -> Tuple[bool, str]:
    """
    Check if PMC efetch returned a restricted XML response (e.g. publishers like ASM
    allowing web reading on PMC but restricting automated full-text API redistribution).
    Returns (is_restricted, reason).
    """
    if isinstance(xml_bytes_or_str, bytes):
        text = xml_bytes_or_str.decode("utf-8", errors="ignore")
    else:
        text = str(xml_bytes_or_str)

    if "does not allow downloading of the full text" in text:
        return True, "Publisher does not allow downloading of full text via PMC XML API"
    if "<front" in text and "<body" not in text.lower():
        return True, "PMC XML response contains front-matter only, missing <body> (restricted by publisher)"
    return False, ""


def extract_supplement_files(xml_bytes_or_str: Any) -> List[str]:
    """
    Extract supplementary file names / relative hrefs from PMC JATS XML.
    Looks for <supplementary-material> elements and <media>/<graphic> children,
    filtering out external HTTP/HTTPS license links.
    """
    if isinstance(xml_bytes_or_str, str):
        xml_bytes = xml_bytes_or_str.encode("utf-8", errors="ignore")
    else:
        xml_bytes = xml_bytes_or_str

    supp_files: List[str] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return supp_files

    for supp in root.iter():
        if _local(supp.tag) != "supplementary-material":
            continue
        # Check supplementary-material tag itself and child media/graphic tags
        candidates = [supp] + [c for c in supp.iter() if _local(c.tag) in ("media", "graphic")]
        for el in candidates:
            href = el.attrib.get("{http://www.w3.org/1999/xlink}href") or el.attrib.get("href")
            if href and not href.startswith(("http://", "https://")) and href not in supp_files:
                supp_files.append(href.strip())

    return supp_files


def extract_tables_from_xml(xml_bytes_or_str: Any) -> List[Dict[str, Any]]:
    """
    Extract all <table-wrap> elements from JATS XML into structured dictionaries:
      {"label": "Table 1", "caption": "...", "rows": [["Col1", "Col2"], ...]}
    """
    if isinstance(xml_bytes_or_str, str):
        xml_bytes = xml_bytes_or_str.encode("utf-8", errors="ignore")
    else:
        xml_bytes = xml_bytes_or_str

    tables: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return tables

    for tw in root.iter():
        if _local(tw.tag) != "table-wrap":
            continue

        label = None
        caption = None
        for child in tw.iter():
            tag = _local(child.tag)
            if tag == "label" and label is None:
                label = "".join(child.itertext()).strip()
            elif tag == "caption" and caption is None:
                caption = " ".join("".join(child.itertext()).split())

        rows: List[List[str]] = []
        for tr in tw.iter():
            if _local(tr.tag) != "tr":
                continue
            cells = [
                " ".join("".join(cell.itertext()).split())
                for cell in tr
                if _local(cell.tag) in ("td", "th")
            ]
            if cells:
                rows.append(cells)

        if rows:
            tables.append({
                "label": label or "",
                "caption": caption or "",
                "rows": rows
            })

    return tables


def extract_data_availability_accessions(xml_bytes_or_str: Any) -> Tuple[List[str], bool]:
    """
    Parse JATS full-text XML for Data Availability statements and extract
    deposited repository accessions (BioProject, BioSample, SRA, GenBank, etc.).
    Returns (accessions_list, section_found_flag).
    """
    if isinstance(xml_bytes_or_str, str):
        xml_bytes = xml_bytes_or_str.encode("utf-8", errors="ignore")
    else:
        xml_bytes = xml_bytes_or_str

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return [], False

    section_texts: List[str] = []
    for el in root.iter():
        tag = _local(el.tag)
        if tag not in ("sec", "notes", "fn"):
            continue
        type_attr = (
            el.attrib.get("sec-type") or el.attrib.get("notes-type") or el.attrib.get("fn-type") or ""
        )
        title_text = ""
        for child in el:
            if _local(child.tag) == "title":
                title_text = "".join(child.itertext())
                break

        if "data-availability" in type_attr.lower() or DATA_AVAILABILITY_HEADING_RE.search(title_text):
            section_texts.append("".join(el.itertext()))

    if not section_texts:
        return [], False

    combined = " ".join(section_texts)
    accessions = sorted(set(DATA_AVAIL_ACCESSION_RE.findall(combined)))
    return accessions, True


def save_tables_to_tsv(
    tables: List[Dict[str, Any]],
    output_dir: str,
    prefix: str = ""
) -> List[str]:
    """
    Save extracted XML tables to clean TSV files in output_dir.
    Returns list of saved file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    saved_paths: List[str] = []

    for idx, table in enumerate(tables, start=1):
        raw_label = table.get("label") or f"Table_{idx}"
        safe_label = re.sub(r'[/\\:?*"<>|\s]+', '_', raw_label.strip()).strip('_') or f"Table_{idx}"
        fname = f"{safe_label}.tsv"
        fpath = os.path.join(output_dir, fname)

        with open(fpath, "w", encoding="utf-8") as f:
            if table.get("caption"):
                lbl = table.get("label") or f"Table {idx}"
                f.write(f"# {lbl}: {table['caption']}\n")
            for row in table.get("rows", []):
                cleaned_cells = [c.replace("\t", " ").replace("\n", " ") for c in row]
                f.write("\t".join(cleaned_cells) + "\n")

        saved_paths.append(os.path.abspath(fpath))

    return saved_paths
