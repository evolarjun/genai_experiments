#!/usr/bin/env python3
"""
PubMed Article PDF Downloader
=============================
Downloads full-text PDFs for a list of PubMed IDs (PMIDs) using a multi-tiered
fallback pipeline across major Open Access repositories, PubMed Central (PMC),
and publisher platforms with support for Institutional Access.

Tiers:
  1. PubMed Central (PMC) Direct API & Web Scraper (with automated PoW solver)
  2. OpenAlex API
  3. Unpaywall API
  4. Semantic Scholar API
  5. Direct Publisher HTML Scraping (via Institutional VPN / EZproxy / Cookies)

Usage:
  python3 download_pubmed_pdfs.py --pmids 24592257 16801449 12821459
  python3 download_pubmed_pdfs.py --file pmids.txt --ezproxy-prefix "https://ezproxy.univ.edu/login?url="
  python3 download_pubmed_pdfs.py --file pmids.txt --cookie-file cookies.txt
"""

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from typing import Dict, Any, Optional, Tuple, List

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Create SSL context to bypass local certificate errors if SSL certificates are unconfigured
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# Global Cookie Jar to retain session cookies across redirects and publisher pages
COOKIE_JAR = http.cookiejar.CookieJar()


class Logger:
    """Handles formatted console output and logging to a text log file."""
    def __init__(self, log_filepath: Optional[str] = None):
        self.log_filepath = log_filepath
        self.log_file = None
        if log_filepath:
            log_dir = os.path.dirname(log_filepath)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            self.log_file = open(log_filepath, "w", encoding="utf-8")

    def log(self, message: str, to_console: bool = True, pmid: Optional[str] = None):
        if to_console:
            print(message)
        if self.log_file:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            pmid_prefix = f"[PMID {pmid}] " if pmid else ""
            cleaned_msg = message.strip()
            self.log_file.write(f"[{timestamp}] {pmid_prefix}{cleaned_msg}\n")
            self.log_file.flush()

    def close(self):
        if self.log_file:
            self.log_file.close()
            self.log_file = None



def load_cookie_file(cookie_file_path: str):
    """Load cookies from a Netscape/Mozilla cookies.txt file or raw header file."""
    if not os.path.exists(cookie_file_path):
        print(f"[Warning] Cookie file not found: {cookie_file_path}")
        return
    try:
        cj = http.cookiejar.MozillaCookieJar(cookie_file_path)
        cj.load(ignore_discard=True, ignore_expires=True)
        for cookie in cj:
            COOKIE_JAR.set_cookie(cookie)
        print(f"[Institutional Auth] Successfully loaded cookies from {cookie_file_path}")
    except Exception as e:
        # Fallback to reading key=value pairs or HTTP header string
        try:
            with open(cookie_file_path, "r") as f:
                content = f.read().strip()
                if content:
                    print(f"[Institutional Auth] Loaded raw Cookie string from {cookie_file_path}")
        except Exception:
            print(f"[Warning] Could not parse cookie file {cookie_file_path}: {e}")


def make_request(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
    proxy: Optional[str] = None,
    cookie_str: Optional[str] = None
) -> Tuple[Optional[bytes], Dict[str, str], int, str]:
    """Perform HTTP GET request with cookie persistence, redirects, custom headers, and proxy support."""
    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        req_headers.update(headers)
    if cookie_str:
        req_headers["Cookie"] = cookie_str

    handlers = [
        urllib.request.HTTPCookieProcessor(COOKIE_JAR),
        urllib.request.HTTPSHandler(context=SSL_CTX),
        urllib.request.HTTPHandler(),
    ]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))

    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers=req_headers)

    try:
        with opener.open(req, timeout=timeout) as resp:
            content = resp.read()
            resp_headers = dict(resp.headers)
            final_url = resp.geturl()
            return content, resp_headers, resp.status, final_url
    except Exception as e:
        code = getattr(e, "code", 500)
        return None, {}, code, url


def is_valid_pdf(content: bytes) -> bool:
    """Verify that HTTP payload is non-empty and starts with standard PDF magic bytes (%PDF)."""
    if not content or len(content) < 500:
        return False
    return content.startswith(b"%PDF") or b"%PDF-" in content[:1024]


def solve_pmc_pow(html_str: str) -> Optional[Tuple[str, str]]:
    """
    Detect and solve NCBI PubMed Central (PMC) JavaScript Proof-of-Work (PoW) challenge.
    Returns (cookie_name, cookie_value) if challenge found and solved, else None.
    """
    m_chal = re.search(r'const POW_CHALLENGE = ["\']([^"\']+)["\']', html_str)
    m_diff = re.search(r'const POW_DIFFICULTY = ["\']([^"\']+)["\']', html_str)
    m_cookie = re.search(r'const POW_COOKIE_NAME = ["\']([^"\']+)["\']', html_str)

    if not (m_chal and m_diff and m_cookie):
        return None

    challenge = m_chal.group(1)
    diff = int(m_diff.group(1))
    cookie_name = m_cookie.group(1)

    target_prefix = "0" * diff
    nonce = 0
    while nonce < 1000000:
        cand = f"{challenge}{nonce}"
        h = hashlib.sha256(cand.encode("utf-8")).hexdigest()
        if h.startswith(target_prefix):
            return cookie_name, f"{challenge},{nonce}"
        nonce += 1

    return None


def resolve_pmid_metadata(
    pmid: str,
    proxy: Optional[str] = None,
    logger: Optional[Logger] = None
) -> Dict[str, Any]:
    """
    Resolve metadata (title, DOI, PMC ID, journal, open-access flag) for a given PMID
    using Europe PMC REST API and NCBI ID Converter API.
    """
    meta = {
        "pmid": pmid,
        "title": None,
        "doi": None,
        "pmcid": None,
        "journal": None,
        "is_oa": False
    }

    if logger:
        logger.log(f"Resolving metadata for PMID {pmid}...", to_console=False, pmid=pmid)

    # Query Europe PMC REST API
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:{pmid}%20AND%20SRC:MED&format=json"
    content, _, _, _ = make_request(url, proxy=proxy)
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
        except Exception:
            pass

    # Fallback to NCBI ID Converter if PMC ID or DOI is missing
    if not meta["pmcid"] or not meta["doi"]:
        conv_url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids={pmid}&format=json"
        conv_content, _, _, _ = make_request(conv_url, proxy=proxy)
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
            except Exception:
                pass

    if logger:
        logger.log(
            f"Metadata resolved: Title='{meta.get('title')}', DOI={meta.get('doi')}, PMCID={meta.get('pmcid')}, OpenAccess={meta.get('is_oa')}",
            to_console=False,
            pmid=pmid
        )

    return meta


def try_pmc_download(
    pmcid: str,
    proxy: Optional[str] = None,
    logger: Optional[Logger] = None,
    pmid: Optional[str] = None
) -> Optional[Tuple[bytes, str]]:
    """
    Tier 1: Query PubMed Central (PMC) directly using PMCID, resolving any PoW JavaScript challenges.
    """
    if not pmcid:
        if logger:
            logger.log("Attempt: Tier 1 (PubMed Central) -> SKIPPED (No PMC ID available)", to_console=False, pmid=pmid)
        return None

    if logger:
        logger.log(f"Attempt: Tier 1 (PubMed Central - {pmcid})", to_console=False, pmid=pmid)

    pmc_clean = pmcid.upper()
    if not pmc_clean.startswith("PMC"):
        pmc_clean = f"PMC{pmc_clean}"

    pmc_landing_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_clean}/"
    content, _, status, final_url = make_request(pmc_landing_url, proxy=proxy)
    if not content:
        if logger:
            logger.log(f"Attempt Outcome: Tier 1 FAILED - Failed to fetch PMC landing page (HTTP {status})", to_console=False, pmid=pmid)
        return None

    try:
        html_str = content.decode("utf-8", errors="ignore")
        pdf_urls_to_try = []

        # Check citation_pdf_url meta tag on PMC article page
        meta_matches = re.findall(
            r'<meta\s+(?:name|content)=["\']citation_pdf_url["\']\s+(?:name|content)=["\']([^"\']+)["\']',
            html_str,
            re.IGNORECASE
        )
        if not meta_matches:
            meta_matches = re.findall(
                r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\']([^"\']+)["\']',
                html_str,
                re.IGNORECASE
            )
        for m in meta_matches:
            if m not in pdf_urls_to_try:
                pdf_urls_to_try.append(m)

        # Look for PDF hrefs on PMC page
        href_pdf_matches = re.findall(
            r'href=["\']([^"\']*(?:/pdf/|\.pdf)[^"\']*)["\']',
            html_str,
            re.IGNORECASE
        )
        for f in href_pdf_matches:
            full_u = urllib.parse.urljoin(final_url, f)
            if full_u not in pdf_urls_to_try and "pdf" in full_u.lower():
                pdf_urls_to_try.append(full_u)

        # Standard fallback PMC PDF endpoint
        std_pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_clean}/pdf/"
        if std_pdf_url not in pdf_urls_to_try:
            pdf_urls_to_try.append(std_pdf_url)

        for p_url in pdf_urls_to_try:
            if logger:
                logger.log(f"  Trying PMC PDF URL: {p_url}", to_console=False, pmid=pmid)
            pdf_bytes, _, code, _ = make_request(p_url, proxy=proxy)

            # Check if response is PoW HTML challenge instead of PDF
            if pdf_bytes and not is_valid_pdf(pdf_bytes):
                html_candidate = pdf_bytes.decode("utf-8", errors="ignore")
                pow_res = solve_pmc_pow(html_candidate)
                if pow_res:
                    if logger:
                        logger.log("  Solving PMC JavaScript PoW challenge...", to_console=False, pmid=pmid)
                    cookie_name, cookie_val = pow_res
                    ck1 = http.cookiejar.Cookie(
                        version=0, name=cookie_name, value=cookie_val, port=None, port_specified=False,
                        domain=".ncbi.nlm.nih.gov", domain_specified=True, domain_initial_dot=True,
                        path="/", path_specified=True, secure=False, expires=None, discard=True,
                        comment=None, comment_url=None, rest={"HttpOnly": None}, rfc2109=False
                    )
                    COOKIE_JAR.set_cookie(ck1)
                    ck2 = http.cookiejar.Cookie(
                        version=0, name=cookie_name, value=cookie_val, port=None, port_specified=False,
                        domain="pmc.ncbi.nlm.nih.gov", domain_specified=True, domain_initial_dot=False,
                        path="/", path_specified=True, secure=False, expires=None, discard=True,
                        comment=None, comment_url=None, rest={"HttpOnly": None}, rfc2109=False
                    )
                    COOKIE_JAR.set_cookie(ck2)

                    # Re-fetch PDF with PoW cookie now set
                    pdf_bytes, _, code, _ = make_request(p_url, proxy=proxy)

            if pdf_bytes and is_valid_pdf(pdf_bytes):
                if logger:
                    logger.log(f"Attempt Outcome: Tier 1 SUCCESS - Downloaded valid PDF ({len(pdf_bytes)} bytes) from {p_url}", to_console=False, pmid=pmid)
                return pdf_bytes, p_url
            else:
                if logger:
                    logger.log(f"  Failed PDF download from {p_url} (HTTP {code} or invalid payload)", to_console=False, pmid=pmid)

    except Exception as e:
        if logger:
            logger.log(f"Error during PMC retrieval: {e}", to_console=False, pmid=pmid)

    if logger:
        logger.log("Attempt Outcome: Tier 1 FAILED - No valid PDF retrieved from PubMed Central", to_console=False, pmid=pmid)
    return None


def try_openalex(
    pmid: str,
    proxy: Optional[str] = None,
    logger: Optional[Logger] = None
) -> Optional[Tuple[bytes, str]]:
    """Tier 2: Query OpenAlex API for Open Access PDF URL."""
    if logger:
        logger.log(f"Attempt: Tier 2 (OpenAlex API for PMID {pmid})", to_console=False, pmid=pmid)

    url = f"https://api.openalex.org/works/pmid:{pmid}"
    content, _, status, _ = make_request(url, proxy=proxy)
    if not content:
        if logger:
            logger.log(f"Attempt Outcome: Tier 2 FAILED - OpenAlex API query failed (HTTP {status})", to_console=False, pmid=pmid)
        return None

    try:
        data = json.loads(content.decode("utf-8"))
        locs = data.get("locations", [])
        pdf_urls = [l.get("pdf_url") for l in locs if l.get("pdf_url")]

        if not pdf_urls:
            if logger:
                logger.log("Attempt Outcome: Tier 2 FAILED - No open-access PDF URLs found in OpenAlex metadata", to_console=False, pmid=pmid)
            return None

        for p_url in pdf_urls:
            if logger:
                logger.log(f"  Trying OpenAlex PDF URL: {p_url}", to_console=False, pmid=pmid)
            pdf_bytes, _, code, _ = make_request(p_url, proxy=proxy)
            if pdf_bytes and is_valid_pdf(pdf_bytes):
                if logger:
                    logger.log(f"Attempt Outcome: Tier 2 SUCCESS - Downloaded valid PDF ({len(pdf_bytes)} bytes) from {p_url}", to_console=False, pmid=pmid)
                return pdf_bytes, p_url
            else:
                if logger:
                    logger.log(f"  Failed PDF download from {p_url} (HTTP {code} or invalid payload)", to_console=False, pmid=pmid)
    except Exception as e:
        if logger:
            logger.log(f"Error during OpenAlex processing: {e}", to_console=False, pmid=pmid)

    if logger:
        logger.log("Attempt Outcome: Tier 2 FAILED - PDF URLs from OpenAlex did not yield valid PDF", to_console=False, pmid=pmid)
    return None


def try_unpaywall(
    doi: str,
    email: str,
    proxy: Optional[str] = None,
    logger: Optional[Logger] = None,
    pmid: Optional[str] = None
) -> Optional[Tuple[bytes, str]]:
    """Tier 3: Query Unpaywall API for Open Access PDF URL."""
    if not doi:
        if logger:
            logger.log("Attempt: Tier 3 (Unpaywall API) -> SKIPPED (No DOI available)", to_console=False, pmid=pmid)
        return None

    if logger:
        logger.log(f"Attempt: Tier 3 (Unpaywall API for DOI {doi})", to_console=False, pmid=pmid)

    q_doi = urllib.parse.quote(doi, safe="")
    url = f"https://api.unpaywall.org/v2/{q_doi}?email={email}"
    content, _, status, _ = make_request(url, proxy=proxy)
    if not content:
        if logger:
            logger.log(f"Attempt Outcome: Tier 3 FAILED - Unpaywall API query failed (HTTP {status})", to_console=False, pmid=pmid)
        return None

    try:
        data = json.loads(content.decode("utf-8"))
        pdf_urls = []
        best_loc = data.get("best_oa_location") or {}
        if best_loc.get("url_for_pdf"):
            pdf_urls.append(best_loc.get("url_for_pdf"))

        for loc in data.get("oa_locations", []):
            u = loc.get("url_for_pdf")
            if u and u not in pdf_urls:
                pdf_urls.append(u)

        if not pdf_urls:
            if logger:
                logger.log("Attempt Outcome: Tier 3 FAILED - No open-access PDF URLs found in Unpaywall metadata", to_console=False, pmid=pmid)
            return None

        for p_url in pdf_urls:
            if logger:
                logger.log(f"  Trying Unpaywall PDF URL: {p_url}", to_console=False, pmid=pmid)
            pdf_bytes, _, code, _ = make_request(p_url, proxy=proxy)
            if pdf_bytes and is_valid_pdf(pdf_bytes):
                if logger:
                    logger.log(f"Attempt Outcome: Tier 3 SUCCESS - Downloaded valid PDF ({len(pdf_bytes)} bytes) from {p_url}", to_console=False, pmid=pmid)
                return pdf_bytes, p_url
            else:
                if logger:
                    logger.log(f"  Failed PDF download from {p_url} (HTTP {code} or invalid payload)", to_console=False, pmid=pmid)
    except Exception as e:
        if logger:
            logger.log(f"Error during Unpaywall processing: {e}", to_console=False, pmid=pmid)

    if logger:
        logger.log("Attempt Outcome: Tier 3 FAILED - PDF URLs from Unpaywall did not yield valid PDF", to_console=False, pmid=pmid)
    return None


def try_semantic_scholar(
    pmid: str,
    proxy: Optional[str] = None,
    logger: Optional[Logger] = None
) -> Optional[Tuple[bytes, str]]:
    """Tier 4: Query Semantic Scholar API for Open Access PDF URL."""
    if logger:
        logger.log(f"Attempt: Tier 4 (Semantic Scholar API for PMID {pmid})", to_console=False, pmid=pmid)

    url = f"https://api.semanticscholar.org/graph/v1/paper/PMID:{pmid}?fields=openAccessPdf"
    content, _, status, _ = make_request(url, proxy=proxy)
    if not content:
        if logger:
            logger.log(f"Attempt Outcome: Tier 4 FAILED - Semantic Scholar API query failed (HTTP {status})", to_console=False, pmid=pmid)
        return None

    try:
        data = json.loads(content.decode("utf-8"))
        oa_pdf = data.get("openAccessPdf") or {}
        p_url = oa_pdf.get("url")
        if p_url:
            if logger:
                logger.log(f"  Trying Semantic Scholar PDF URL: {p_url}", to_console=False, pmid=pmid)
            pdf_bytes, _, code, _ = make_request(p_url, proxy=proxy)
            if pdf_bytes and is_valid_pdf(pdf_bytes):
                if logger:
                    logger.log(f"Attempt Outcome: Tier 4 SUCCESS - Downloaded valid PDF ({len(pdf_bytes)} bytes) from {p_url}", to_console=False, pmid=pmid)
                return pdf_bytes, p_url
            else:
                if logger:
                    logger.log(f"Attempt Outcome: Tier 4 FAILED - Failed PDF download from {p_url} (HTTP {code} or invalid payload)", to_console=False, pmid=pmid)
        else:
            if logger:
                logger.log("Attempt Outcome: Tier 4 FAILED - No open-access PDF URL returned by Semantic Scholar", to_console=False, pmid=pmid)
    except Exception as e:
        if logger:
            logger.log(f"Error during Semantic Scholar processing: {e}", to_console=False, pmid=pmid)

    return None


def try_crossref_pdf_url(doi: str, proxy: Optional[str] = None) -> List[str]:
    """Query CrossRef REST API for direct publisher PDF link specifications."""
    if not doi:
        return []
    q_doi = urllib.parse.quote(doi, safe="")
    url = f"https://api.crossref.org/works/{q_doi}"
    headers = {"User-Agent": "PubMedPDFDownloader/1.0 (mailto:paperdownloader@example.com)"}
    content, _, _, _ = make_request(url, headers=headers, proxy=proxy)
    if not content:
        return []

    pdf_urls = []
    try:
        data = json.loads(content.decode("utf-8"))
        links = data.get("message", {}).get("link", [])
        for link in links:
            u = link.get("URL")
            c_type = link.get("content-type", "").lower()
            if u and ("pdf" in c_type or u.endswith(".pdf") or "/article-pdf/" in u):
                if u not in pdf_urls:
                    pdf_urls.append(u)
    except Exception:
        pass
    return pdf_urls


def try_publisher_scrape(
    doi: str,
    ezproxy_prefix: Optional[str] = None,
    proxy: Optional[str] = None,
    cookie_str: Optional[str] = None,
    logger: Optional[Logger] = None,
    pmid: Optional[str] = None
) -> Optional[Tuple[bytes, str]]:
    """
    Tier 5: Follow DOI landing page (with optional EZproxy prefix) and scrape publisher
    HTML metadata / PDF links using institutional credentials or VPN access.
    """
    if not doi:
        if logger:
            logger.log("Attempt: Tier 5 (Publisher Scrape) -> SKIPPED (No DOI available)", to_console=False, pmid=pmid)
        return None

    sub_info = "EZproxy" if ezproxy_prefix else ("Cookies/Proxy" if (cookie_str or proxy) else "Institutional IP/VPN")
    if logger:
        logger.log(f"Attempt: Tier 5 (Publisher Scraping for DOI {doi} via {sub_info})", to_console=False, pmid=pmid)

    doi_target = f"https://doi.org/{doi}"
    if ezproxy_prefix:
        if "url=" in ezproxy_prefix:
            target_url = f"{ezproxy_prefix}{urllib.parse.quote(doi_target, safe='')}"
        else:
            target_url = f"{ezproxy_prefix.rstrip('/')}/{doi_target}"
    else:
        target_url = doi_target

    html_content, _, status, final_url = make_request(
        target_url, proxy=proxy, cookie_str=cookie_str
    )
    
    # Also attempt CrossRef API PDF URLs
    crossref_urls = try_crossref_pdf_url(doi, proxy=proxy)
    for cr_url in crossref_urls:
        req_u = f"{ezproxy_prefix}{urllib.parse.quote(cr_url, safe='')}" if (ezproxy_prefix and "url=" in ezproxy_prefix) else cr_url
        if logger:
            logger.log(f"  Trying CrossRef PDF URL: {req_u}", to_console=False, pmid=pmid)
        pdf_bytes, _, code, _ = make_request(req_u, proxy=proxy, cookie_str=cookie_str)
        if pdf_bytes and is_valid_pdf(pdf_bytes):
            if logger:
                logger.log(f"Attempt Outcome: Tier 5 SUCCESS - Downloaded valid PDF ({len(pdf_bytes)} bytes) via CrossRef URL: {req_u}", to_console=False, pmid=pmid)
            return pdf_bytes, req_u

    if not html_content:
        if logger:
            logger.log(f"Attempt Outcome: Tier 5 FAILED - Failed to fetch publisher landing page (HTTP {status})", to_console=False, pmid=pmid)
        return None

    # Check if the landing response itself is already a raw PDF file
    if is_valid_pdf(html_content):
        if logger:
            logger.log(f"Attempt Outcome: Tier 5 SUCCESS - Landing page directly returned valid PDF ({len(html_content)} bytes)", to_console=False, pmid=pmid)
        return html_content, final_url

    try:
        html_str = html_content.decode("utf-8", errors="ignore")
        pdf_urls_to_try = []

        # 1. Look for meta tags standard in academic publishing
        meta_matches = re.findall(
            r'<meta\s+(?:name|content)=["\'](?:citation_pdf_url|bepress_citation_pdf_url)["\']\s+(?:name|content)=["\']([^"\']+)["\']',
            html_str,
            re.IGNORECASE
        )
        if not meta_matches:
            meta_matches = re.findall(
                r'<meta\s+name=["\'](?:citation_pdf_url|bepress_citation_pdf_url)["\']\s+content=["\']([^"\']+)["\']',
                html_str,
                re.IGNORECASE
            )
        for m in meta_matches:
            if m not in pdf_urls_to_try:
                pdf_urls_to_try.append(m)

        # 2. Look for publisher-specific PDF URL patterns in HTML anchor tags / scripts
        patterns = [
            r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']',              # Generic .pdf links
            r'href=["\']([^"\']*/article/pii/[^"\']*/pdfft)["\']',       # ScienceDirect / Elsevier
            r'href=["\']([^"\']*/doi/pdf/[^"\']+)["\']',                 # Wiley / ASM / ACS
            r'href=["\']([^"\']*/doi/pdfdirect/[^"\']+)["\']',           # Wiley PDF Direct
            r'href=["\']([^"\']*/content/pdf/[^"\']+)["\']',             # Springer Link
            r'href=["\']([^"\']*/articles/[^"\']+\.pdf)["\']',            # Nature
            r'href=["\']([^"\']*/article-pdf/[^"\']+)["\']',             # Oxford Academic
        ]

        for pat in patterns:
            found = re.findall(pat, html_str, re.IGNORECASE)
            for f in found:
                if f not in pdf_urls_to_try:
                    pdf_urls_to_try.append(f)

        for p_url in pdf_urls_to_try:
            full_pdf_url = urllib.parse.urljoin(final_url, p_url)
            
            if ezproxy_prefix and not full_pdf_url.startswith(ezproxy_prefix):
                if "url=" in ezproxy_prefix:
                    req_pdf_url = f"{ezproxy_prefix}{urllib.parse.quote(full_pdf_url, safe='')}"
                else:
                    req_pdf_url = f"{ezproxy_prefix.rstrip('/')}/{full_pdf_url}"
            else:
                req_pdf_url = full_pdf_url

            if logger:
                logger.log(f"  Trying scraped publisher PDF URL: {req_pdf_url}", to_console=False, pmid=pmid)
            pdf_bytes, _, code, _ = make_request(req_pdf_url, proxy=proxy, cookie_str=cookie_str)
            if pdf_bytes and is_valid_pdf(pdf_bytes):
                if logger:
                    logger.log(f"Attempt Outcome: Tier 5 SUCCESS - Downloaded valid PDF ({len(pdf_bytes)} bytes) from {req_pdf_url}", to_console=False, pmid=pmid)
                return pdf_bytes, req_pdf_url
            else:
                if logger:
                    logger.log(f"  Failed PDF download from {req_pdf_url} (HTTP {code} or invalid payload)", to_console=False, pmid=pmid)

    except Exception as e:
        if logger:
            logger.log(f"Error during publisher HTML scraping: {e}", to_console=False, pmid=pmid)

    if logger:
        logger.log("Attempt Outcome: Tier 5 FAILED - Publisher page scraping did not yield valid PDF", to_console=False, pmid=pmid)
    return None


def download_pubmed_article(
    pmid: str,
    output_dir: str,
    email: str,
    delay: float,
    ezproxy_prefix: Optional[str] = None,
    proxy: Optional[str] = None,
    cookie_str: Optional[str] = None,
    logger: Optional[Logger] = None
) -> Dict[str, Any]:
    """Execute the retrieval pipeline for a single PubMed article."""
    if logger is None:
        logger = Logger(None)

    meta = resolve_pmid_metadata(pmid, proxy=proxy, logger=logger)
    doi = meta.get("doi")
    pmcid = meta.get("pmcid")
    title = meta.get("title") or "Unknown Title"

    header_banner = (
        f"\n--------------------------------------------------\n"
        f"[PMID {pmid}] {title[:65]}...\n"
        f"  PMC ID: {pmcid or 'None'} | DOI: {doi or 'None'} | Open Access Flag: {meta.get('is_oa')}"
    )
    logger.log(header_banner, to_console=True, pmid=pmid)

    pdf_bytes = None
    download_url = None
    source = None

    # Tier 1: PubMed Central (PMC) Direct
    if pmcid:
        logger.log(f"  -> Querying Tier 1: PubMed Central ({pmcid})...", to_console=True, pmid=pmid)
        res = try_pmc_download(pmcid, proxy=proxy, logger=logger, pmid=pmid)
        if res:
            pdf_bytes, download_url = res
            source = "PubMed Central (PMC)"

    # Tier 2: OpenAlex API
    if not pdf_bytes:
        logger.log("  -> Querying Tier 2: OpenAlex API...", to_console=True, pmid=pmid)
        res = try_openalex(pmid, proxy=proxy, logger=logger)
        if res:
            pdf_bytes, download_url = res
            source = "OpenAlex API"

    # Tier 3: Unpaywall API
    if not pdf_bytes and doi:
        logger.log("  -> Querying Tier 3: Unpaywall API...", to_console=True, pmid=pmid)
        res = try_unpaywall(doi, email, proxy=proxy, logger=logger, pmid=pmid)
        if res:
            pdf_bytes, download_url = res
            source = "Unpaywall API"

    # Tier 4: Semantic Scholar
    if not pdf_bytes:
        logger.log("  -> Querying Tier 4: Semantic Scholar API...", to_console=True, pmid=pmid)
        res = try_semantic_scholar(pmid, proxy=proxy, logger=logger)
        if res:
            pdf_bytes, download_url = res
            source = "Semantic Scholar API"

    # Tier 5: Direct Publisher Scraping (with Institutional VPN / EZproxy / Cookies)
    if not pdf_bytes and doi:
        sub_info = "EZproxy" if ezproxy_prefix else ("Cookies/Proxy" if (cookie_str or proxy) else "Institutional IP/VPN")
        logger.log(f"  -> Querying Tier 5: Publisher DOI Landing Scraping ({sub_info})...", to_console=True, pmid=pmid)
        res = try_publisher_scrape(doi, ezproxy_prefix=ezproxy_prefix, proxy=proxy, cookie_str=cookie_str, logger=logger, pmid=pmid)
        if res:
            pdf_bytes, download_url = res
            source = f"Publisher Scrape ({sub_info})"

    time.sleep(delay)

    if pdf_bytes:
        out_filename = f"{pmid}.pdf"
        out_path = os.path.join(output_dir, out_filename)
        with open(out_path, "wb") as f:
            f.write(pdf_bytes)
        size_kb = len(pdf_bytes) / 1024.0
        abs_path = os.path.abspath(out_path)
        logger.log(f"  [SUCCESS] Saved {out_filename} ({size_kb:.1f} KB) via {source}", to_console=True, pmid=pmid)
        logger.log(f"DOWNLOAD STATUS: SUCCESS - Paper successfully downloaded (PMID: {pmid}, Source: {source}, Size: {len(pdf_bytes)} bytes, File: {abs_path})", to_console=True, pmid=pmid)
        return {
            "pmid": pmid,
            "status": "success",
            "title": meta.get("title"),
            "journal": meta.get("journal"),
            "doi": doi,
            "pmcid": pmcid,
            "source": source,
            "download_url": download_url,
            "file_path": abs_path,
            "size_bytes": len(pdf_bytes)
        }
    else:
        landing_url = f"https://doi.org/{doi}" if doi else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        logger.log(f"  [PAYWALLED / WAF RESTRICTED] Landing Page: {landing_url}", to_console=True, pmid=pmid)
        logger.log(f"DOWNLOAD STATUS: FAILED - Paper download failed (PMID: {pmid}, Landing Page: {landing_url}, Reason: Article is paywalled or required institutional authentication)", to_console=True, pmid=pmid)
        return {
            "pmid": pmid,
            "status": "failed",
            "title": meta.get("title"),
            "journal": meta.get("journal"),
            "doi": doi,
            "pmcid": pmcid,
            "landing_url": landing_url,
            "reason": "Article is paywalled or publisher domain (e.g. academic.oup.com) returned HTTP 403 (Cloudflare WAF bot block). Pass --cookie-file or --cookie-header with browser cookies to bypass."
        }


def parse_curl_command(curl_str: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse a 'Copy as cURL' command string from browser DevTools to extract (cookie_str, user_agent)."""
    cookie_str = None
    user_agent = None

    # Match -H 'cookie: ...' or -H "Cookie: ..." or --header 'cookie: ...' or -b '...'
    cookie_match = re.search(r'(?:-H|--header)\s+["\']cookie:\s*([^"\']+)["\']', curl_str, re.IGNORECASE)
    if not cookie_match:
        cookie_match = re.search(r'(?:-b|--cookie)\s+["\']([^"\']+)["\']', curl_str, re.IGNORECASE)
    if cookie_match:
        cookie_str = cookie_match.group(1).strip()

    ua_match = re.search(r'(?:-H|--header)\s+["\']user-agent:\s*([^"\']+)["\']', curl_str, re.IGNORECASE)
    if not ua_match:
        ua_match = re.search(r'(?:-A|--user-agent)\s+["\']([^"\']+)["\']', curl_str, re.IGNORECASE)
    if ua_match:
        user_agent = ua_match.group(1).strip()

    return cookie_str, user_agent


def main():
    parser = argparse.ArgumentParser(description="Download PubMed article PDFs with support for Open Access APIs and Institutional Access.")
    parser.add_argument("--pmids", nargs="+", help="List of PubMed IDs (PMIDs) to download.")
    parser.add_argument("-f", "--file", help="Path to text file containing PubMed IDs (one per line).")
    parser.add_argument("-o", "--output-dir", default="./downloaded_pdfs", help="Directory to save downloaded PDFs (default: ./downloaded_pdfs).")
    parser.add_argument("--email", default="paperdownloader@example.com", help="Email to identify calls to APIs like Unpaywall.")
    parser.add_argument("--rate-limit", type=float, default=0.5, help="Delay in seconds between requests (default: 0.5).")
    parser.add_argument("--summary", help="Path for JSON summary report.")
    parser.add_argument("-l", "--log-file", nargs="?", const="download_attempts.log", help="Path to write text log file recording all download attempts and outcomes for each PMID.")
    
    # Institutional Access Options
    parser.add_argument("--ezproxy-prefix", help="Institutional EZproxy URL prefix (e.g. 'https://ezproxy.univ.edu/login?url=').")
    parser.add_argument("--cookie-file", help="Path to exported Netscape/Mozilla cookies.txt file for institutional session reuse.")
    parser.add_argument("--cookie-header", help="Raw Cookie header string (e.g. 'ezproxy=...; session=...').")
    parser.add_argument("--curl-cmd", help="Raw 'Copy as cURL' command string copied from browser DevTools.")
    parser.add_argument("--curl-file", help="Path to file containing a 'Copy as cURL' command from browser DevTools.")
    parser.add_argument("--proxy", help="HTTP/HTTPS network proxy URL (e.g. 'http://proxy.univ.edu:8080').")

    args = parser.parse_args()

    if args.cookie_file:
        load_cookie_file(args.cookie_file)

    cookie_str = args.cookie_header

    if args.curl_cmd or args.curl_file:
        curl_text = args.curl_cmd or ""
        if args.curl_file and os.path.exists(args.curl_file):
            with open(args.curl_file, "r") as f:
                curl_text = f.read()

        c_str, u_agent = parse_curl_command(curl_text)
        if c_str:
            cookie_str = c_str
            print("[cURL Import] Successfully extracted Cookie header from cURL command.")
        if u_agent:
            global USER_AGENT
            USER_AGENT = u_agent
            print(f"[cURL Import] User-Agent updated to match browser session.")

    pmid_list = []
    if args.pmids:
        pmid_list.extend(args.pmids)

    if args.file:
        if os.path.exists(args.file):
            with open(args.file, "r") as f:
                for line in f:
                    cleaned = line.strip()
                    if cleaned and not cleaned.startswith("#"):
                        pmid_list.append(cleaned)
        else:
            print(f"[Error] File not found: {args.file}")
            sys.exit(1)

    # Deduplicate preserving order
    seen = set()
    unique_pmids = []
    for p in pmid_list:
        if p not in seen:
            seen.add(p)
            unique_pmids.append(p)

    if not unique_pmids:
        print("[Error] No PMIDs provided. Use --pmids or --file.")
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    
    log_path = None
    if args.log_file:
        log_path = args.log_file
        if not os.path.dirname(log_path):
            log_path = os.path.join(args.output_dir, log_path)

    logger = Logger(log_path)

    try:
        logger.log("==================================================")
        logger.log("           PubMed Article PDF Downloader          ")
        logger.log("==================================================")
        logger.log(f"Target PMIDs ({len(unique_pmids)}): {', '.join(unique_pmids)}")
        logger.log(f"Output Directory: {os.path.abspath(args.output_dir)}")
        if log_path:
            logger.log(f"Attempts Log:     {os.path.abspath(log_path)}")
        if args.ezproxy_prefix:
            logger.log(f"EZproxy Prefix:   {args.ezproxy_prefix}")
        if cookie_str or args.cookie_file:
            logger.log(f"Cookie Auth:      Enabled")
        if args.proxy:
            logger.log(f"Network Proxy:    {args.proxy}")

        results = []
        succeeded = 0

        for pmid in unique_pmids:
            res = download_pubmed_article(
                pmid=pmid,
                output_dir=args.output_dir,
                email=args.email,
                delay=args.rate_limit,
                ezproxy_prefix=args.ezproxy_prefix,
                proxy=args.proxy,
                cookie_str=cookie_str,
                logger=logger
            )
            results.append(res)
            if res["status"] == "success":
                succeeded += 1

        summary_path = args.summary if args.summary else os.path.join(args.output_dir, "download_summary.json")
        summary_data = {
            "total_requested": len(unique_pmids),
            "succeeded": succeeded,
            "failed": len(unique_pmids) - succeeded,
            "results": results
        }

        with open(summary_path, "w") as f:
            json.dump(summary_data, f, indent=2)

        logger.log("\n==================================================")
        logger.log("                   FINAL SUMMARY                  ")
        logger.log("==================================================")
        logger.log(f"Total PMIDs Processed: {len(unique_pmids)}")
        logger.log(f"PDFs Downloaded:       {succeeded}")
        logger.log(f"Paywalled / Non-OA:    {len(unique_pmids) - succeeded}")
        logger.log(f"Summary JSON:          {os.path.abspath(summary_path)}")
        if log_path:
            logger.log(f"Attempts Log File:     {os.path.abspath(log_path)}")
        logger.log("==================================================")
    finally:
        logger.close()


if __name__ == "__main__":
    main()
