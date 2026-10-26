"""
Supplementary Material Downloader
=================================
Discovers and downloads supplementary files (spreadsheets, datasets, PDFs, etc.)
for academic papers from PubMed Central (PMC).

Features:
  - Discovers supplement files via PMC JATS XML
  - Solves PMC JavaScript Proof-of-Work (PoW) challenges in pure Python
  - Supports optional headless browser fallback (Playwright)
  - Resumes and skips previously downloaded files (idempotent)
"""

import http.cookiejar
import os
import re
import urllib.parse
from typing import Dict, Any, List, Optional, Tuple

from http_client import Session
from tiers import solve_pmc_pow
from xml_extractor import extract_supplement_files, is_pmc_xml_restricted

PMC_BIN_URL = "https://pmc.ncbi.nlm.nih.gov/articles/instance/{numeric}/bin/{fname}"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def fetch_pmc_xml(
    pmcid: str,
    session: Session,
    ncbi_api_key: Optional[str] = None,
    ncbi_email: Optional[str] = None,
    logger: Optional[Any] = None,
    pmid: Optional[str] = None
) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Fetch PMC JATS full-text XML via E-utilities efetch.fcgi.
    Returns (xml_bytes, error_message).
    """
    numeric = pmcid.upper().replace("PMC", "").strip()
    if not numeric:
        return None, "Invalid PMCID"

    params = {
        "db": "pmc",
        "id": numeric,
        "rettype": "xml",
        "retmode": "xml"
    }
    if ncbi_api_key:
        params["api_key"] = ncbi_api_key
    if ncbi_email:
        params["email"] = ncbi_email

    query_str = urllib.parse.urlencode(params)
    url = f"{EUTILS_BASE}/efetch.fcgi?{query_str}"

    content, _, status, _ = session.request(url, use_cookies=False)
    if not content or status != 200:
        return None, f"Failed to fetch PMC XML (HTTP {status})"

    is_restr, restr_reason = is_pmc_xml_restricted(content)
    if is_restr and logger:
        logger.log(f"  [XML Notice] {restr_reason}", to_console=False, pmid=pmid)

    return content, None


def download_single_supplement(
    url: str,
    dest_path: str,
    referer: str,
    session: Session,
    browser_downloader: Optional[Any] = None,
    force: bool = False,
    logger: Optional[Any] = None,
    pmid: Optional[str] = None
) -> Dict[str, Any]:
    """
    Download a single supplementary file, handling PoW challenges and browser fallbacks.
    Returns dict with status, size_bytes, path, or error.
    """
    fname = os.path.basename(dest_path)

    # Resume check
    if not force and os.path.exists(dest_path):
        size = os.path.getsize(dest_path)
        if size >= 100:
            if logger:
                logger.log(f"    [SKIPPED] Supplement {fname} already exists ({size / 1024.0:.1f} KB)", to_console=False, pmid=pmid)
            return {
                "filename": fname,
                "url": url,
                "status": "success",
                "skipped_existing": True,
                "file_path": os.path.abspath(dest_path),
                "size_bytes": size
            }

    headers = {"Referer": referer}
    content, _, status, _ = session.request(url, use_cookies=True, headers=headers)

    # Check for anti-bot PoW HTML challenge
    if content:
        low_snippet = content[:300].decode("utf-8", errors="ignore").lower()
        if "<html" in low_snippet or "<!doctype html" in low_snippet:
            html_text = content.decode("utf-8", errors="ignore")
            pow_sol = solve_pmc_pow(html_text)
            if pow_sol:
                if logger:
                    logger.log("    Solving PMC Proof-of-Work challenge for supplement download...", to_console=False, pmid=pmid)
                cookie_name, cookie_val = pow_sol
                for domain, initial_dot in [(".ncbi.nlm.nih.gov", True), ("pmc.ncbi.nlm.nih.gov", False)]:
                    ck = http.cookiejar.Cookie(
                        version=0, name=cookie_name, value=cookie_val, port=None, port_specified=False,
                        domain=domain, domain_specified=True, domain_initial_dot=initial_dot,
                        path="/", path_specified=True, secure=False, expires=None, discard=True,
                        comment=None, comment_url=None, rest={"HttpOnly": None}, rfc2109=False
                    )
                    session.cookie_jar.set_cookie(ck)
                # Retry request with PoW cookie set
                content, _, status, _ = session.request(url, use_cookies=True, headers=headers)

    # Check if download succeeded and is not an HTML interstitial
    is_html = False
    if content:
        low_start = content[:150].decode("utf-8", errors="ignore").lower()
        if "<!doctype html" in low_start or "<html" in low_start:
            is_html = True

    # If plain HTTP failed or was blocked by bot challenge, try browser fallback
    if (not content or is_html) and browser_downloader is not None:
        if logger:
            logger.log(f"    Attempting browser fallback for supplement {fname}...", to_console=False, pmid=pmid)
        b_content, b_err = browser_downloader.fetch(url, referer=referer)
        if b_content:
            content = b_content
            is_html = False
        elif logger and b_err:
            logger.log(f"    Browser fallback failed: {b_err}", to_console=False, pmid=pmid)

    if not content or is_html or len(content) < 100:
        err_msg = "Received HTML interstitial or anti-bot challenge" if is_html else f"HTTP {status} or empty content"
        if logger:
            logger.log(f"    [FAILED] Supplement {fname} ({err_msg})", to_console=False, pmid=pmid)
        return {
            "filename": fname,
            "url": url,
            "status": "failed",
            "error": err_msg
        }

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(content)

    if logger:
        logger.log(f"    [SAVED] Supplement {fname} ({len(content) / 1024.0:.1f} KB)", to_console=False, pmid=pmid)

    return {
        "filename": fname,
        "url": url,
        "status": "success",
        "file_path": os.path.abspath(dest_path),
        "size_bytes": len(content)
    }


def download_supplements_for_article(
    pmcid: str,
    output_dir: str,
    session: Session,
    xml_bytes: Optional[bytes] = None,
    browser_downloader: Optional[Any] = None,
    force: bool = False,
    logger: Optional[Any] = None,
    pmid: Optional[str] = None
) -> Dict[str, Any]:
    """
    Discover and download all supplementary files for an article.
    """
    clean_pmc = pmcid.upper()
    if not clean_pmc.startswith("PMC"):
        clean_pmc = f"PMC{clean_pmc}"
    numeric_pmc = clean_pmc.replace("PMC", "")

    # Fetch XML if not provided
    if not xml_bytes:
        xml_bytes, err = fetch_pmc_xml(clean_pmc, session=session, logger=logger, pmid=pmid)
        if not xml_bytes:
            return {
                "discovered": 0,
                "downloaded": 0,
                "failed": 0,
                "files": [],
                "error": err or "Could not fetch PMC XML"
            }

    supp_files = extract_supplement_files(xml_bytes)
    if not supp_files:
        return {
            "discovered": 0,
            "downloaded": 0,
            "failed": 0,
            "files": []
        }

    if logger:
        logger.log(f"  Discovered {len(supp_files)} supplementary file(s) in PMC XML", to_console=True, pmid=pmid)

    referer = f"https://pmc.ncbi.nlm.nih.gov/articles/{clean_pmc}/"
    file_results: List[Dict[str, Any]] = []
    downloaded_count = 0
    failed_count = 0

    for href in supp_files:
        base_name = href.rsplit("/", 1)[-1].strip()
        if not base_name:
            continue
        dest_path = os.path.join(output_dir, base_name)
        url = PMC_BIN_URL.format(numeric=numeric_pmc, fname=base_name)

        res = download_single_supplement(
            url=url,
            dest_path=dest_path,
            referer=referer,
            session=session,
            browser_downloader=browser_downloader,
            force=force,
            logger=logger,
            pmid=pmid
        )
        file_results.append(res)
        if res.get("status") == "success":
            downloaded_count += 1
        else:
            failed_count += 1

    return {
        "discovered": len(supp_files),
        "downloaded": downloaded_count,
        "failed": failed_count,
        "files": file_results
    }
