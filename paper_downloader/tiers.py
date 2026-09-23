"""
Resolution Tiers for PubMed PDF Retrieval
=========================================
Implements the cascading 5-tier fallback pipeline:
  Tier 1: PubMed Central (PMC) with JavaScript Proof-of-Work solver
  Tier 2: OpenAlex API
  Tier 3: Unpaywall API
  Tier 4: Semantic Scholar API
  Tier 5: Direct Publisher Scraping (with Institutional VPN, EZproxy, Cookies)
"""

import hashlib
import http.cookiejar
import json
import os
import re
import urllib.parse
from typing import Dict, Any, Optional, Tuple, List

from http_client import Session, is_valid_pdf


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


def _check_and_log_payload(
    content: Optional[bytes],
    url: str,
    logger: Optional[Any] = None,
    pmid: Optional[str] = None,
    save_html_dir: Optional[str] = None,
    tier_name: Optional[str] = None
) -> bool:
    """Helper to check if content is a valid PDF, logging and optionally saving HTML if received instead."""
    if is_valid_pdf(content):
        return True
    if content and len(content) > 10:
        snippet = content[:500].decode("utf-8", errors="ignore").lower()
        if "<html" in snippet or "<!doctype" in snippet:
            if logger:
                logger.log(
                    f"  Notice: Received HTML instead of PDF from {url} (likely login page, paywall, or challenge)",
                    to_console=False,
                    pmid=pmid
                )
            if save_html_dir:
                try:
                    os.makedirs(save_html_dir, exist_ok=True)
                    safe_id = re.sub(r'[/\\:?*"<>|\s]+', '_', (pmid or "sample").strip())
                    tier_tag = f"_{tier_name}" if tier_name else ""
                    sample_path = os.path.join(save_html_dir, f"{safe_id}{tier_tag}.html")
                    with open(sample_path, "wb") as f:
                        f.write(content)
                    if logger:
                        logger.log(f"  Saved HTML challenge/paywall sample to {sample_path}", to_console=False, pmid=pmid)
                except Exception as e:
                    if logger:
                        logger.log(f"  Warning: Failed to save HTML sample: {e}", to_console=False, pmid=pmid)
    return False


def try_pmc_download(
    pmcid: str,
    session: Session,
    logger: Optional[Any] = None,
    pmid: Optional[str] = None,
    save_html_dir: Optional[str] = None
) -> Optional[Tuple[bytes, str]]:
    """Tier 1: Query PubMed Central (PMC) directly using PMCID, resolving PoW challenges."""
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
    content, _, status, final_url = session.request(pmc_landing_url, use_cookies=True)
    if not content:
        if logger:
            logger.log(f"Attempt Outcome: Tier 1 FAILED - Failed to fetch PMC landing page (HTTP {status})", to_console=False, pmid=pmid)
        return None

    try:
        html_str = content.decode("utf-8", errors="ignore")
        pdf_urls_to_try: List[str] = []

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

        pmc_headers = {"Referer": pmc_landing_url}
        for p_url in pdf_urls_to_try:
            if logger:
                logger.log(f"  Trying PMC PDF URL: {p_url}", to_console=False, pmid=pmid)
            pdf_bytes, _, code, _ = session.request(p_url, use_cookies=True, headers=pmc_headers)

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
                    session.cookie_jar.set_cookie(ck1)
                    ck2 = http.cookiejar.Cookie(
                        version=0, name=cookie_name, value=cookie_val, port=None, port_specified=False,
                        domain="pmc.ncbi.nlm.nih.gov", domain_specified=True, domain_initial_dot=False,
                        path="/", path_specified=True, secure=False, expires=None, discard=True,
                        comment=None, comment_url=None, rest={"HttpOnly": None}, rfc2109=False
                    )
                    session.cookie_jar.set_cookie(ck2)

                    # Re-fetch PDF with PoW cookie now set
                    pdf_bytes, _, code, _ = session.request(p_url, use_cookies=True, headers=pmc_headers)

            if _check_and_log_payload(pdf_bytes, p_url, logger=logger, pmid=pmid, save_html_dir=save_html_dir, tier_name="pmc"):
                if logger:
                    logger.log(
                        f"Attempt Outcome: Tier 1 SUCCESS - Downloaded valid PDF ({len(pdf_bytes)} bytes) from {p_url}",
                        to_console=False,
                        pmid=pmid
                    )
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
    pmid: Optional[str],
    session: Session,
    doi: Optional[str] = None,
    logger: Optional[Any] = None
) -> Optional[Tuple[bytes, str]]:
    """Tier 2: Query OpenAlex API for Open Access PDF URL."""
    ident = f"pmid:{pmid}" if pmid else (f"doi:{doi}" if doi else None)
    if not ident:
        return None

    if logger:
        logger.log(f"Attempt: Tier 2 (OpenAlex API for {ident})", to_console=False, pmid=pmid)

    url = f"https://api.openalex.org/works/{ident}"
    content, _, status, _ = session.request(url, use_cookies=False)
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
            pdf_bytes, _, code, _ = session.request(p_url, use_cookies=False)
            if _check_and_log_payload(pdf_bytes, p_url, logger=logger, pmid=pmid):
                if logger:
                    logger.log(f"Attempt Outcome: Tier 2 SUCCESS - Downloaded valid PDF ({len(pdf_bytes)} bytes) from {p_url}", to_console=False, pmid=pmid)
                return pdf_bytes, p_url
            else:
                if logger:
                    logger.log(f"  Failed PDF download from {p_url} (HTTP {code} or invalid payload)", to_console=False, pmid=pmid)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        if logger:
            logger.log(f"Error during OpenAlex processing: {e}", to_console=False, pmid=pmid)

    if logger:
        logger.log("Attempt Outcome: Tier 2 FAILED - PDF URLs from OpenAlex did not yield valid PDF", to_console=False, pmid=pmid)
    return None


def try_unpaywall(
    doi: str,
    email: str,
    session: Session,
    logger: Optional[Any] = None,
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
    content, _, status, _ = session.request(url, use_cookies=False)
    if not content:
        if logger:
            logger.log(f"Attempt Outcome: Tier 3 FAILED - Unpaywall API query failed (HTTP {status})", to_console=False, pmid=pmid)
        return None

    try:
        data = json.loads(content.decode("utf-8"))
        pdf_urls: List[str] = []
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
            pdf_bytes, _, code, _ = session.request(p_url, use_cookies=False)
            if _check_and_log_payload(pdf_bytes, p_url, logger=logger, pmid=pmid):
                if logger:
                    logger.log(f"Attempt Outcome: Tier 3 SUCCESS - Downloaded valid PDF ({len(pdf_bytes)} bytes) from {p_url}", to_console=False, pmid=pmid)
                return pdf_bytes, p_url
            else:
                if logger:
                    logger.log(f"  Failed PDF download from {p_url} (HTTP {code} or invalid payload)", to_console=False, pmid=pmid)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        if logger:
            logger.log(f"Error during Unpaywall processing: {e}", to_console=False, pmid=pmid)

    if logger:
        logger.log("Attempt Outcome: Tier 3 FAILED - PDF URLs from Unpaywall did not yield valid PDF", to_console=False, pmid=pmid)
    return None


def try_semantic_scholar(
    pmid: Optional[str],
    session: Session,
    doi: Optional[str] = None,
    logger: Optional[Any] = None
) -> Optional[Tuple[bytes, str]]:
    """Tier 4: Query Semantic Scholar API for Open Access PDF URL."""
    ident = f"PMID:{pmid}" if pmid else (f"DOI:{doi}" if doi else None)
    if not ident:
        return None

    if logger:
        logger.log(f"Attempt: Tier 4 (Semantic Scholar API for {ident})", to_console=False, pmid=pmid)

    url = f"https://api.semanticscholar.org/graph/v1/paper/{ident}?fields=openAccessPdf"
    content, _, status, _ = session.request(url, use_cookies=False)
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
            pdf_bytes, _, code, _ = session.request(p_url, use_cookies=False)
            if _check_and_log_payload(pdf_bytes, p_url, logger=logger, pmid=pmid):
                if logger:
                    logger.log(f"Attempt Outcome: Tier 4 SUCCESS - Downloaded valid PDF ({len(pdf_bytes)} bytes) from {p_url}", to_console=False, pmid=pmid)
                return pdf_bytes, p_url
            else:
                if logger:
                    logger.log(f"Attempt Outcome: Tier 4 FAILED - Failed PDF download from {p_url} (HTTP {code} or invalid payload)", to_console=False, pmid=pmid)
        else:
            if logger:
                logger.log("Attempt Outcome: Tier 4 FAILED - No open-access PDF URL returned by Semantic Scholar", to_console=False, pmid=pmid)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        if logger:
            logger.log(f"Error during Semantic Scholar processing: {e}", to_console=False, pmid=pmid)

    return None


def try_crossref_pdf_url(doi: str, session: Session) -> List[str]:
    """Query CrossRef REST API for direct publisher PDF link specifications."""
    if not doi:
        return []
    q_doi = urllib.parse.quote(doi, safe="")
    url = f"https://api.crossref.org/works/{q_doi}"
    headers = {"User-Agent": "PubMedPDFDownloader/2.0 (mailto:paperdownloader@example.com)"}
    content, _, _, _ = session.request(url, headers=headers, use_cookies=False)
    if not content:
        return []

    pdf_urls: List[str] = []
    try:
        data = json.loads(content.decode("utf-8"))
        links = data.get("message", {}).get("link", [])
        for link in links:
            u = link.get("URL")
            c_type = link.get("content-type", "").lower()
            if u and ("pdf" in c_type or u.endswith(".pdf") or "/article-pdf/" in u):
                if u not in pdf_urls:
                    pdf_urls.append(u)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return pdf_urls


def try_publisher_scrape(
    doi: str,
    session: Session,
    ezproxy_prefix: Optional[str] = None,
    cookie_str: Optional[str] = None,
    logger: Optional[Any] = None,
    pmid: Optional[str] = None,
    save_html_dir: Optional[str] = None
) -> Optional[Tuple[bytes, str]]:
    """
    Tier 5: Follow DOI landing page (with optional EZproxy prefix) and scrape publisher
    HTML metadata / PDF links using institutional credentials or VPN access.
    Scoped institutional cookies are only used in this tier.
    """
    if not doi:
        if logger:
            logger.log("Attempt: Tier 5 (Publisher Scrape) -> SKIPPED (No DOI available)", to_console=False, pmid=pmid)
        return None

    sub_info = "EZproxy" if ezproxy_prefix else ("Cookies/Proxy" if (cookie_str or session.proxy) else "Institutional IP/VPN")
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

    html_content, _, status, final_url = session.request(
        target_url, use_cookies=True, cookie_str=cookie_str
    )

    # Also attempt CrossRef API PDF URLs
    crossref_urls = try_crossref_pdf_url(doi, session=session)
    for cr_url in crossref_urls:
        req_u = f"{ezproxy_prefix}{urllib.parse.quote(cr_url, safe='')}" if (ezproxy_prefix and "url=" in ezproxy_prefix) else cr_url
        if logger:
            logger.log(f"  Trying CrossRef PDF URL: {req_u}", to_console=False, pmid=pmid)
        pdf_bytes, _, code, _ = session.request(req_u, use_cookies=True, cookie_str=cookie_str)
        if _check_and_log_payload(pdf_bytes, req_u, logger=logger, pmid=pmid, save_html_dir=save_html_dir, tier_name="crossref"):
            if logger:
                logger.log(f"Attempt Outcome: Tier 5 SUCCESS - Downloaded valid PDF ({len(pdf_bytes)} bytes) via CrossRef URL: {req_u}", to_console=False, pmid=pmid)
            return pdf_bytes, req_u

    if not html_content:
        if logger:
            logger.log(f"Attempt Outcome: Tier 5 FAILED - Failed to fetch publisher landing page (HTTP {status})", to_console=False, pmid=pmid)
        return None

    # Check if the landing response itself is already a raw PDF file
    if _check_and_log_payload(html_content, final_url, logger=logger, pmid=pmid, save_html_dir=save_html_dir, tier_name="publisher_landing"):
        if logger:
            logger.log(f"Attempt Outcome: Tier 5 SUCCESS - Landing page directly returned valid PDF ({len(html_content)} bytes)", to_console=False, pmid=pmid)
        return html_content, final_url

    try:
        html_str = html_content.decode("utf-8", errors="ignore")
        pdf_urls_to_try: List[str] = []

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

        pub_headers = {"Referer": final_url}
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
            pdf_bytes, _, code, _ = session.request(req_pdf_url, use_cookies=True, cookie_str=cookie_str, headers=pub_headers)
            if _check_and_log_payload(pdf_bytes, req_pdf_url, logger=logger, pmid=pmid, save_html_dir=save_html_dir, tier_name="publisher"):
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
