"""
Metadata Resolution for PubMed IDs and DOIs
============================================
Resolves article titles, journals, DOIs, PMIDs, and PMC IDs using:
- Europe PMC REST API
- NCBI ID Converter API
- CrossRef REST API (for direct DOI resolution)
"""

import json
import re
import urllib.parse
from typing import Dict, Any, Optional, List

from http_client import Session


def batch_resolve_pmids(
    pmids: List[str],
    session: Session,
    ncbi_api_key: Optional[str] = None,
    ncbi_email: Optional[str] = None,
    logger: Optional[Any] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Batch resolve metadata (title, journal, year, author, PMCID, DOI) for PMIDs in chunks of up to 200
    using NCBI ESummary API (https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi).
    Returns a dictionary mapping each PMID to its resolved metadata dictionary.
    """
    results: Dict[str, Dict[str, Any]] = {}
    valid_pmids = [p.strip() for p in pmids if p.strip().isdigit()]
    if not valid_pmids:
        return results

    if logger:
        logger.log(f"Batch resolving metadata for {len(valid_pmids)} PMID(s) via NCBI ESummary...", to_console=True)

    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

    for i in range(0, len(valid_pmids), 200):
        chunk = valid_pmids[i : i + 200]
        params = {
            "db": "pubmed",
            "id": ",".join(chunk),
            "retmode": "json",
            "tool": "pubmed_downloader"
        }
        if ncbi_api_key:
            params["api_key"] = ncbi_api_key
        if ncbi_email:
            params["email"] = ncbi_email

        query_str = urllib.parse.urlencode(params)
        req_url = f"{base_url}?{query_str}"

        content, _, status, _ = session.request(req_url, use_cookies=False)
        if not content and not session.verify_ssl:
            # Retry once if the first attempt triggered an automatic SSL downgrade
            content, _, status, _ = session.request(req_url, use_cookies=False)
        if not content:
            if logger:
                logger.log(f"Warning: NCBI ESummary returned status {status} for chunk starting at index {i}", to_console=False)
            continue

        try:
            payload = json.loads(content.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            if logger:
                logger.log(f"Warning: Failed to decode NCBI ESummary JSON: {e}", to_console=False)
            continue

        doc_results = payload.get("result", {})
        for pmid in chunk:
            doc = doc_results.get(pmid)
            if not doc or not isinstance(doc, dict):
                continue

            meta: Dict[str, Any] = {
                "pmid": pmid,
                "title": doc.get("title", "").rstrip(".") or None,
                "doi": None,
                "pmcid": None,
                "journal": doc.get("source") or None,
                "first_author": None,
                "year": None,
                "is_oa": False
            }

            authors = doc.get("authors") or []
            if authors and isinstance(authors, list):
                meta["first_author"] = authors[0].get("name")

            pubdate = doc.get("pubdate", "")
            m_year = re.search(r"\d{4}", pubdate)
            if m_year:
                meta["year"] = m_year.group(0)

            for aid in doc.get("articleids", []):
                idtype = aid.get("idtype", "")
                val = aid.get("value", "")
                if idtype == "pmc" and val:
                    meta["pmcid"] = val if val.upper().startswith("PMC") else f"PMC{val}"
                elif idtype == "doi" and val:
                    meta["doi"] = val

            results[pmid] = meta

    if logger:
        resolved_count = sum(1 for m in results.values() if m.get("title") or m.get("pmcid") or m.get("doi"))
        logger.log(f"NCBI ESummary resolved metadata for {resolved_count}/{len(valid_pmids)} PMID(s).", to_console=True)

    return results


def resolve_pmid_metadata(
    pmid: str,
    session: Session,
    logger: Optional[Any] = None,
    prefetched_meta: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Resolve metadata (title, DOI, PMC ID, journal, open-access flag) for a given PMID
    using Europe PMC REST API and NCBI ID Converter API.
    Reuses prefetched_meta if available.
    """
    meta: Dict[str, Any] = {
        "pmid": pmid,
        "title": None,
        "doi": None,
        "pmcid": None,
        "journal": None,
        "is_oa": False
    }

    if prefetched_meta:
        meta.update(prefetched_meta)

    # If title, doi, and pmcid are already resolved from prefetch, we are done
    if meta.get("title") and meta.get("doi") and meta.get("pmcid"):
        if logger:
            logger.log(
                f"Using prefetched metadata for PMID {pmid}: Title='{meta.get('title')}', "
                f"DOI={meta.get('doi')}, PMCID={meta.get('pmcid')}",
                to_console=False,
                pmid=pmid
            )
        return meta

    if logger:
        logger.log(f"Resolving metadata for PMID {pmid}...", to_console=False, pmid=pmid)

    # 1. Query Europe PMC REST API
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:{pmid}%20AND%20SRC:MED&format=json"
    content, _, status, _ = session.request(url, use_cookies=False)
    if content:
        try:
            data = json.loads(content.decode("utf-8"))
            results = data.get("resultList", {}).get("result", [])
            if results:
                res = results[0]
                meta["title"] = res.get("title")
                meta["doi"] = res.get("doi")
                meta["pmcid"] = res.get("pmcid")
                meta["journal"] = res.get("journalTitle")
                meta["is_oa"] = res.get("isOpenAccess") == "Y"
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            if logger:
                logger.log(f"Warning: Failed to parse Europe PMC response for PMID {pmid}: {e}", to_console=False, pmid=pmid)

    # 2. Fallback to NCBI ID Converter if PMC ID or DOI is missing
    if not meta["pmcid"] or not meta["doi"]:
        conv_url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids={pmid}&format=json"
        conv_content, _, status, _ = session.request(conv_url, use_cookies=False)
        if conv_content:
            try:
                cdata = json.loads(conv_content.decode("utf-8"))
                records = cdata.get("records", [])
                if records:
                    rec = records[0]
                    if not meta["pmcid"]:
                        meta["pmcid"] = rec.get("pmcid")
                    if not meta["doi"]:
                        meta["doi"] = rec.get("doi")
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                if logger:
                    logger.log(f"Warning: Failed to parse NCBI ID Converter response for PMID {pmid}: {e}", to_console=False, pmid=pmid)

    # 3. Retry Europe PMC if title is still missing (e.g. first call failed
    #    due to SSL auto-downgrade that has since been resolved).
    if not meta["title"]:
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:{pmid}%20AND%20SRC:MED&format=json"
        content, _, status, _ = session.request(url, use_cookies=False)
        if content:
            try:
                data = json.loads(content.decode("utf-8"))
                results = data.get("resultList", {}).get("result", [])
                if results:
                    res = results[0]
                    if not meta["title"]:
                        meta["title"] = res.get("title")
                    if not meta["doi"]:
                        meta["doi"] = res.get("doi")
                    if not meta["pmcid"]:
                        meta["pmcid"] = res.get("pmcid")
                    if not meta["journal"]:
                        meta["journal"] = res.get("journalTitle")
                    if not meta["is_oa"]:
                        meta["is_oa"] = res.get("isOpenAccess") == "Y"
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
    if logger:
        logger.log(
            f"Metadata resolved: Title='{meta.get('title')}', DOI={meta.get('doi')}, "
            f"PMCID={meta.get('pmcid')}, OpenAccess={meta.get('is_oa')}",
            to_console=False,
            pmid=pmid
        )

    return meta


def resolve_doi_metadata(
    doi: str,
    session: Session,
    logger: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Resolve metadata (title, journal, PMID, PMC ID) when a DOI is provided directly.
    Queries CrossRef and NCBI ID Converter.
    """
    clean_doi = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if clean_doi.lower().startswith(prefix):
            clean_doi = clean_doi[len(prefix):].strip()

    meta: Dict[str, Any] = {
        "pmid": None,
        "title": None,
        "doi": clean_doi,
        "pmcid": None,
        "journal": None,
        "is_oa": False
    }

    if logger:
        logger.log(f"Resolving metadata for DOI {clean_doi}...", to_console=False)

    # 1. Query NCBI ID Converter to see if PMID / PMCID exists for this DOI
    conv_url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids={clean_doi}&format=json"
    conv_content, _, _, _ = session.request(conv_url, use_cookies=False)
    if conv_content:
        try:
            cdata = json.loads(conv_content.decode("utf-8"))
            records = cdata.get("records", [])
            if records:
                rec = records[0]
                meta["pmid"] = rec.get("pmid")
                meta["pmcid"] = rec.get("pmcid")
                if rec.get("doi"):
                    meta["doi"] = rec.get("doi")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            if logger:
                logger.log(f"Warning: Failed to parse NCBI ID Converter for DOI {clean_doi}: {e}", to_console=False)

    # 2. Query CrossRef REST API for title and journal metadata
    q_doi = urllib.parse.quote(clean_doi, safe="")
    url = f"https://api.crossref.org/works/{q_doi}"
    headers = {"User-Agent": "PubMedPDFDownloader/2.0 (mailto:paperdownloader@example.com)"}
    content, _, _, _ = session.request(url, headers=headers, use_cookies=False)
    if content:
        try:
            data = json.loads(content.decode("utf-8"))
            msg = data.get("message", {})
            titles = msg.get("title", [])
            if titles:
                meta["title"] = titles[0]
            containers = msg.get("container-title", [])
            if containers:
                meta["journal"] = containers[0]
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            if logger:
                logger.log(f"Warning: Failed to parse CrossRef response for DOI {clean_doi}: {e}", to_console=False)

    # If PMID was found via NCBI, enrich with Europe PMC as well
    if meta.get("pmid"):
        pmid_meta = resolve_pmid_metadata(str(meta["pmid"]), session=session, logger=logger)
        for k in ("title", "journal", "pmcid", "is_oa"):
            if not meta.get(k) and pmid_meta.get(k):
                meta[k] = pmid_meta[k]

    return meta
