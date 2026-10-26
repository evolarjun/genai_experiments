#!/usr/bin/env python3
"""
PubMed Article PDF Downloader
=============================
Downloads full-text PDFs for PubMed IDs (PMIDs) and DOIs using a multi-tiered
fallback pipeline across major Open Access repositories, PubMed Central (PMC),
and publisher platforms with support for Institutional Access.

Tiers:
  1. PubMed Central (PMC) Direct API & Web Scraper (with automated PoW solver)
  2. OpenAlex API
  3. Unpaywall API
  4. Semantic Scholar API
  5. Direct Publisher HTML Scraping (via Institutional VPN / EZproxy / Cookies)
"""

import argparse
import concurrent.futures
import json
import os
import re
import sys
import threading
import time
from typing import Dict, Any, Optional, Tuple, List

from http_client import (
    Session,
    DomainRateLimiter,
    load_cookie_file,
    parse_curl_command,
    is_valid_pdf,
    USER_AGENT_DEFAULT,
)
from metadata import resolve_pmid_metadata, resolve_doi_metadata, batch_resolve_pmids, fetch_bvbrc_genomes
from tiers import (
    try_pmc_download,
    try_openalex,
    try_unpaywall,
    try_semantic_scholar,
    try_publisher_scrape,
)
from xml_extractor import (
    is_pmc_xml_restricted,
    extract_tables_from_xml,
    save_tables_to_tsv,
    extract_data_availability_accessions,
)
from supplements import (
    fetch_pmc_xml,
    download_supplements_for_article,
)
from browser_downloader import BrowserDownloader

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


class Logger:
    """Handles formatted console output and logging to a text log file (supports context manager)."""

    def __init__(self, log_filepath: Optional[str] = None):
        self.log_filepath = log_filepath
        self.log_file = None
        self._lock = threading.Lock()
        if log_filepath:
            log_dir = os.path.dirname(log_filepath)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            self.log_file = open(log_filepath, "w", encoding="utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def log(self, message: str, to_console: bool = True, pmid: Optional[str] = None):
        with self._lock:
            if to_console:
                print(message)
            if self.log_file:
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                pmid_prefix = f"[{pmid}] " if pmid else ""
                cleaned_msg = message.strip()
                self.log_file.write(f"[{timestamp}] {pmid_prefix}{cleaned_msg}\n")
                self.log_file.flush()

    def close(self):
        with self._lock:
            if self.log_file:
                self.log_file.close()
                self.log_file = None


def sanitize_filename(identifier: str) -> str:
    """Sanitize identifier (PMID or DOI) into a safe filename base."""
    # Replace slashes, colons, spaces with underscores
    safe = re.sub(r'[/\\:?*"<>|\s]+', '_', identifier.strip())
    return safe.strip('._') or "article"


def is_doi_identifier(ident: str) -> bool:
    """Check if the given identifier is a DOI rather than a PMID."""
    cleaned = ident.strip()
    return cleaned.startswith("10.") or "/" in cleaned or cleaned.lower().startswith("doi:")


def download_pubmed_article(
    identifier: str,
    output_dir: str,
    session: Session,
    email: str,
    ezproxy_prefix: Optional[str] = None,
    cookie_str: Optional[str] = None,
    logger: Optional[Logger] = None,
    force: bool = False,
    progress_str: str = "",
    prefetched_meta: Optional[Dict[str, Any]] = None,
    save_html_dir: Optional[str] = None,
    include_supplements: bool = False,
    supplements_dir: Optional[str] = None,
    extract_tables: bool = False,
    tables_dir: Optional[str] = None,
    use_browser: bool = False,
    browser_downloader: Optional[Any] = None,
    bvbrc: bool = False,
    ncbi_api_key: Optional[str] = None,
    ncbi_email: Optional[str] = None
) -> Dict[str, Any]:
    """Execute the retrieval pipeline for a single article (PMID or DOI)."""
    if logger is None:
        logger = Logger(None)

    is_doi = is_doi_identifier(identifier)
    
    # 1. Resolve metadata
    if is_doi:
        meta = resolve_doi_metadata(identifier, session=session, logger=logger)
    else:
        meta = resolve_pmid_metadata(
            identifier, session=session, logger=logger, prefetched_meta=prefetched_meta
        )

    pmid = meta.get("pmid") or (None if is_doi else identifier)
    doi = meta.get("doi") or (identifier if is_doi else None)
    pmcid = meta.get("pmcid")
    title = meta.get("title") or "Unknown Title"

    out_name = f"{pmid}.pdf" if pmid else f"{sanitize_filename(doi)}.pdf"
    out_path = os.path.join(output_dir, out_name)
    abs_path = os.path.abspath(out_path)

    prefix_tag = f"[{progress_str}] " if progress_str else ""
    header_banner = (
        f"\n--------------------------------------------------\n"
        f"{prefix_tag}[Article: {identifier}] {title[:65]}...\n"
        f"  PMC ID: {pmcid or 'None'} | DOI: {doi or 'None'} | Open Access Flag: {meta.get('is_oa')}"
    )
    logger.log(header_banner, to_console=True, pmid=identifier)

    # Optional BV-BRC genome query
    bvbrc_data = None
    if bvbrc and pmid:
        bvbrc_data = fetch_bvbrc_genomes(pmid=str(pmid), session=session, logger=logger)

    # 2. Check if already exists (Resume / Skip functionality)
    pdf_bytes = None
    download_url = None
    source = None
    skipped_existing = False

    if not force and os.path.exists(out_path):
        size_bytes = os.path.getsize(out_path)
        if size_bytes >= 100:
            logger.log(f"  [SKIPPED] Already downloaded ({size_bytes / 1024.0:.1f} KB at {out_name}). Use --force to re-download.", to_console=True, pmid=identifier)
            source = "Local Cache (Existing File)"
            skipped_existing = True
            pdf_bytes = b""  # Marker for existing PDF

    if not skipped_existing:
        # Tier 1: PubMed Central (PMC) Direct
        if pmcid:
            logger.log(f"  -> Querying Tier 1: PubMed Central ({pmcid})...", to_console=True, pmid=identifier)
            res = try_pmc_download(
                pmcid,
                session=session,
                logger=logger,
                pmid=identifier,
                save_html_dir=save_html_dir,
                browser_downloader=browser_downloader
            )
            if res:
                pdf_bytes, download_url = res
                source = "PubMed Central (PMC)"

        # Tier 2: OpenAlex API
        if not pdf_bytes:
            logger.log("  -> Querying Tier 2: OpenAlex API...", to_console=True, pmid=identifier)
            res = try_openalex(pmid=pmid, doi=doi, session=session, logger=logger)
            if res:
                pdf_bytes, download_url = res
                source = "OpenAlex API"

        # Tier 3: Unpaywall API
        if not pdf_bytes and doi:
            logger.log("  -> Querying Tier 3: Unpaywall API...", to_console=True, pmid=identifier)
            res = try_unpaywall(doi=doi, email=email, session=session, logger=logger, pmid=identifier)
            if res:
                pdf_bytes, download_url = res
                source = "Unpaywall API"

        # Tier 4: Semantic Scholar
        if not pdf_bytes and (pmid or doi):
            logger.log("  -> Querying Tier 4: Semantic Scholar API...", to_console=True, pmid=identifier)
            res = try_semantic_scholar(pmid=pmid, doi=doi, session=session, logger=logger)
            if res:
                pdf_bytes, download_url = res
                source = "Semantic Scholar API"

        # Tier 5: Direct Publisher Scraping (with Institutional VPN / EZproxy / Cookies)
        if not pdf_bytes and doi:
            sub_info = "EZproxy" if ezproxy_prefix else ("Cookies/Proxy" if (cookie_str or session.proxy) else "Institutional IP/VPN")
            logger.log(f"  -> Querying Tier 5: Publisher DOI Landing Scraping ({sub_info})...", to_console=True, pmid=identifier)
            res = try_publisher_scrape(
                doi=doi,
                session=session,
                ezproxy_prefix=ezproxy_prefix,
                cookie_str=cookie_str,
                logger=logger,
                pmid=identifier,
                save_html_dir=save_html_dir,
                browser_downloader=browser_downloader
            )
            if res:
                pdf_bytes, download_url = res
                source = f"Publisher Scrape ({sub_info})"

        if pdf_bytes:
            with open(out_path, "wb") as f:
                f.write(pdf_bytes)
            size_kb = len(pdf_bytes) / 1024.0
            logger.log(f"  [SUCCESS] Saved {out_name} ({size_kb:.1f} KB) via {source}", to_console=True, pmid=identifier)
            logger.log(
                f"DOWNLOAD STATUS: SUCCESS - Paper successfully downloaded "
                f"(ID: {identifier}, Source: {source}, Size: {len(pdf_bytes)} bytes, File: {abs_path})",
                to_console=True,
                pmid=identifier
            )
        else:
            landing_url = f"https://doi.org/{doi}" if doi else (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else f"https://doi.org/{identifier}")
            logger.log(f"  [PAYWALLED / WAF RESTRICTED] Landing Page: {landing_url}", to_console=True, pmid=identifier)
            logger.log(
                f"DOWNLOAD STATUS: FAILED - Paper download failed "
                f"(ID: {identifier}, Landing Page: {landing_url}, Reason: Article is paywalled or required institutional authentication)",
                to_console=True,
                pmid=identifier
            )

    # 3. Handle PMC Full-Text XML capabilities (Supplements, Tables, Data Availability)
    xml_bytes = None
    pmc_xml_restricted = False
    pmc_xml_restricted_reason = None
    data_availability_info = None
    extracted_tables_info = None
    supplements_info = None

    if pmcid and (include_supplements or extract_tables or True):
        # Fetch PMC XML if needed
        xml_bytes, xml_err = fetch_pmc_xml(
            pmcid=pmcid,
            session=session,
            ncbi_api_key=ncbi_api_key,
            ncbi_email=ncbi_email,
            logger=logger,
            pmid=identifier
        )
        if xml_bytes:
            is_restr, restr_reason = is_pmc_xml_restricted(xml_bytes)
            if is_restr:
                pmc_xml_restricted = True
                pmc_xml_restricted_reason = restr_reason

            # Data availability mining
            da_accessions, da_found = extract_data_availability_accessions(xml_bytes)
            if da_found or da_accessions:
                data_availability_info = {
                    "section_found": da_found,
                    "accessions": da_accessions
                }
                if da_accessions and logger:
                    logger.log(f"  Data Availability accessions found: {', '.join(da_accessions[:6])}{' ...' if len(da_accessions) > 6 else ''}", to_console=True, pmid=identifier)

            # Table extraction
            if extract_tables:
                tables = extract_tables_from_xml(xml_bytes)
                safe_target_dir = sanitize_filename(pmid or identifier)
                eff_tables_dir = os.path.join(tables_dir or os.path.join(output_dir, "tables"), safe_target_dir)
                saved_paths = save_tables_to_tsv(tables, eff_tables_dir)
                extracted_tables_info = {
                    "count": len(tables),
                    "files": saved_paths
                }
                if logger:
                    logger.log(f"  Extracted {len(tables)} table(s) to {eff_tables_dir}", to_console=True, pmid=identifier)

            # Supplementary materials download
            if include_supplements:
                safe_target_dir = sanitize_filename(pmid or identifier)
                eff_supp_dir = os.path.join(supplements_dir or os.path.join(output_dir, "supplements"), safe_target_dir)
                supp_res = download_supplements_for_article(
                    pmcid=pmcid,
                    output_dir=eff_supp_dir,
                    session=session,
                    xml_bytes=xml_bytes,
                    browser_downloader=browser_downloader,
                    force=force,
                    logger=logger,
                    pmid=identifier
                )
                supplements_info = supp_res
        elif include_supplements:
            supplements_info = {
                "discovered": 0,
                "downloaded": 0,
                "failed": 0,
                "files": [],
                "error": xml_err or "Failed to fetch PMC XML"
            }

    # 4. Compile article result
    is_success = bool(skipped_existing or pdf_bytes)
    result_dict: Dict[str, Any] = {
        "identifier": identifier,
        "pmid": pmid,
        "status": "success" if is_success else "failed",
        "title": meta.get("title"),
        "journal": meta.get("journal"),
        "doi": doi,
        "pmcid": pmcid,
    }

    if is_success:
        result_dict["source"] = source
        if skipped_existing:
            result_dict["skipped_existing"] = True
            result_dict["file_path"] = abs_path
            result_dict["size_bytes"] = os.path.getsize(out_path)
        else:
            result_dict["download_url"] = download_url
            result_dict["file_path"] = abs_path
            result_dict["size_bytes"] = len(pdf_bytes) if pdf_bytes else 0
    else:
        landing_url = f"https://doi.org/{doi}" if doi else (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else f"https://doi.org/{identifier}")
        result_dict["landing_url"] = landing_url
        result_dict["reason"] = (
            "Article is paywalled, requires subscription authentication, or blocked by bot protection. "
            "Pass --cookie-file, --cookie-header, or institutional VPN/EZproxy to access."
        )

    if pmc_xml_restricted:
        result_dict["pmc_xml_restricted"] = True
        result_dict["pmc_xml_restricted_reason"] = pmc_xml_restricted_reason

    if data_availability_info:
        result_dict["data_availability"] = data_availability_info

    if extracted_tables_info is not None:
        result_dict["tables"] = extracted_tables_info

    if supplements_info is not None:
        result_dict["supplements"] = supplements_info

    if bvbrc_data is not None:
        result_dict["bvbrc"] = bvbrc_data

    return result_dict


def main():
    parser = argparse.ArgumentParser(
        description="Download PubMed / DOI article PDFs with Open Access APIs and Institutional Access."
    )
    parser.add_argument("--pmids", nargs="+", help="List of PubMed IDs (PMIDs) or DOIs to download.")
    parser.add_argument("-f", "--file", help="Path to text file containing PMIDs or DOIs (one per line).")
    parser.add_argument("-o", "--output-dir", default="./downloaded_pdfs", help="Directory to save downloaded PDFs (default: ./downloaded_pdfs).")
    parser.add_argument("--email", default="paperdownloader@example.com", help="Email to identify calls to APIs like Unpaywall.")
    parser.add_argument("--rate-limit", type=float, default=0.5, help="Delay in seconds between requests to the same domain (default: 0.5).")
    parser.add_argument("--summary", help="Path for JSON summary report.")
    parser.add_argument("-l", "--log-file", nargs="?", const="download_attempts.log", help="Path to write text log file recording all download attempts.")

    # Institutional Access Options
    parser.add_argument("--ezproxy-prefix", help="Institutional EZproxy URL prefix (e.g. 'https://ezproxy.univ.edu/login?url=').")
    parser.add_argument("--cookie-file", help="Path to exported Netscape/Mozilla cookies.txt file for institutional session reuse.")
    parser.add_argument("--cookie-header", help="Raw Cookie header string (e.g. 'ezproxy=...; session=...').")
    parser.add_argument("--curl-cmd", help="Raw 'Copy as cURL' command string copied from browser DevTools.")
    parser.add_argument("--curl-file", help="Path to file containing a 'Copy as cURL' command from browser DevTools.")
    parser.add_argument("--proxy", help="HTTP/HTTPS network proxy URL (e.g. 'http://proxy.univ.edu:8080').")

    # New flags from improvements plan
    parser.add_argument("--no-verify-ssl", action="store_true", help="Disable SSL certificate verification (insecure, prints warning).")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP request timeout in seconds (default: 30).")
    parser.add_argument("--parallel", type=int, default=1, help="Number of concurrent worker threads for downloads (default: 1 sequential).")
    parser.add_argument("--force", action="store_true", help="Re-download files even if they already exist in the output directory.")
    parser.add_argument("--no-fail", action="store_true", help="Exit with code 0 even if one or more downloads fail.")

    # External techniques: NCBI API Key, Email, and HTML Sample Inspection
    parser.add_argument("--ncbi-api-key", default=os.environ.get("NCBI_API_KEY"), help="NCBI API key to raise E-Utilities rate limit to 10 req/s.")
    parser.add_argument("--ncbi-email", default=os.environ.get("NCBI_EMAIL"), help="Contact email for NCBI E-Utilities queries.")
    parser.add_argument("--save-html-samples", nargs="?", const="anti_bot_samples", default=None, help="Directory to save raw HTML bodies when anti-bot or paywalls block a PDF.")

    # Enhanced capabilities: Supplements, XML Tables, Headless Browser, and BV-BRC
    parser.add_argument("--include-supplements", action="store_true", help="Discover and download supplementary materials (spreadsheets, datasets, PDFs).")
    parser.add_argument("--supplements-dir", help="Custom directory to store supplementary files (default: {output_dir}/supplements).")
    parser.add_argument("--extract-tables", action="store_true", help="Extract embedded main-text tables from PMC XML into TSV files.")
    parser.add_argument("--tables-dir", help="Custom directory to store extracted tables (default: {output_dir}/tables).")
    parser.add_argument("--use-browser", action="store_true", help="Enable Playwright headless Chromium fallback for anti-bot or JS interstitial challenges.")
    parser.add_argument("--bvbrc", action="store_true", help="Query BV-BRC Data API for bacterial genomes and accessions linked to PMID.")

    args = parser.parse_args()

    # SSL verification setup
    verify_ssl = not args.no_verify_ssl
    if not verify_ssl:
        print("[Warning] SSL certificate verification is DISABLED (--no-verify-ssl). Network traffic may be vulnerable.")

    # Cookie and User-Agent extraction
    cookie_str = args.cookie_header
    user_agent = USER_AGENT_DEFAULT

    if args.curl_cmd or args.curl_file:
        curl_text = args.curl_cmd or ""
        if args.curl_file and os.path.exists(args.curl_file):
            with open(args.curl_file, "r", encoding="utf-8") as f:
                curl_text = f.read()

        c_str, u_agent = parse_curl_command(curl_text)
        if c_str:
            cookie_str = c_str
            print("[cURL Import] Successfully extracted Cookie header from cURL command.")
        if u_agent:
            user_agent = u_agent
            print("[cURL Import] User-Agent updated to match browser session.")

    # Collect target identifiers (PMIDs or DOIs)
    raw_list: List[str] = []
    if args.pmids:
        raw_list.extend(args.pmids)

    if args.file:
        if os.path.exists(args.file):
            with open(args.file, "r", encoding="utf-8") as f:
                for line in f:
                    cleaned = line.strip()
                    if cleaned and not cleaned.startswith("#"):
                        raw_list.append(cleaned)
        else:
            print(f"[Error] File not found: {args.file}")
            sys.exit(1)

    # Deduplicate while preserving order
    seen = set()
    targets: List[str] = []
    for item in raw_list:
        if item not in seen:
            seen.add(item)
            targets.append(item)

    if not targets:
        print("[Error] No PMIDs or DOIs provided. Use --pmids or --file.")
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    log_path = None
    if args.log_file:
        log_path = args.log_file
        if not os.path.dirname(log_path):
            log_path = os.path.join(args.output_dir, log_path)

    save_html_dir = None
    if args.save_html_samples:
        if args.save_html_samples == "anti_bot_samples":
            save_html_dir = os.path.join(args.output_dir, "anti_bot_samples")
        else:
            save_html_dir = os.path.abspath(args.save_html_samples)
        os.makedirs(save_html_dir, exist_ok=True)

    # Configure per-domain delays (e.g. NCBI rate limit scaling with API key)
    ncbi_delay = 0.1 if args.ncbi_api_key else min(0.34, args.rate_limit)
    domain_delays = {
        "eutils.ncbi.nlm.nih.gov": ncbi_delay,
        "ncbi.nlm.nih.gov": ncbi_delay,
        "www.ncbi.nlm.nih.gov": ncbi_delay,
        "pmc.ncbi.nlm.nih.gov": ncbi_delay
    }
    rate_limiter = DomainRateLimiter(default_delay=args.rate_limit, domain_delays=domain_delays)

    with Logger(log_path) as logger:
        logger.log("==================================================")
        logger.log("           PubMed Article PDF Downloader          ")
        logger.log("==================================================")
        logger.log(f"Target Articles ({len(targets)}): {', '.join(targets[:10])}{' ...' if len(targets) > 10 else ''}")
        logger.log(f"Output Directory: {os.path.abspath(args.output_dir)}")
        if log_path:
            logger.log(f"Attempts Log:     {os.path.abspath(log_path)}")
        if save_html_dir:
            logger.log(f"HTML Samples:     {os.path.abspath(save_html_dir)}")
        if args.ncbi_api_key:
            logger.log(f"NCBI API Key:     Configured (10 req/s)")
        if args.ezproxy_prefix:
            logger.log(f"EZproxy Prefix:   {args.ezproxy_prefix}")
        if cookie_str or args.cookie_file:
            logger.log(f"Cookie Auth:      Enabled")
        if args.proxy:
            logger.log(f"Network Proxy:    {args.proxy}")
        if args.include_supplements:
            logger.log(f"Supplements:      Enabled ({os.path.abspath(args.supplements_dir or os.path.join(args.output_dir, 'supplements'))})")
        if args.extract_tables:
            logger.log(f"Table Extraction: Enabled ({os.path.abspath(args.tables_dir or os.path.join(args.output_dir, 'tables'))})")
        if args.use_browser:
            logger.log("Headless Browser: Enabled (Playwright Chromium)")
        if args.bvbrc:
            logger.log("BV-BRC Lookup:    Enabled")
        logger.log(f"Concurrency:      {args.parallel} thread(s)")
        logger.log(f"SSL Verification: {'Enabled' if verify_ssl else 'Disabled'}")

        # Shared base session builder
        def create_worker_session() -> Session:
            sess = Session(
                user_agent=user_agent,
                verify_ssl=verify_ssl,
                proxy=args.proxy,
                timeout=args.timeout,
                rate_limiter=rate_limiter
            )
            if args.cookie_file:
                load_cookie_file(args.cookie_file, sess.cookie_jar)
            return sess

        # Thread-local browser downloader management
        thread_local = threading.local()

        def get_worker_browser() -> Optional[BrowserDownloader]:
            if not args.use_browser:
                return None
            if not hasattr(thread_local, "browser"):
                b = BrowserDownloader(user_agent=user_agent)
                ready, reason = b.is_available()
                if ready:
                    thread_local.browser = b
                else:
                    logger.log(f"[Warning] Playwright browser fallback unavailable: {reason}", to_console=True)
                    thread_local.browser = None
            return thread_local.browser

        # Batch resolve metadata for all PMIDs via NCBI ESummary
        pmid_targets = [ident for ident in targets if not is_doi_identifier(ident)]
        prefetched_metadata: Dict[str, Dict[str, Any]] = {}
        if pmid_targets:
            batch_sess = create_worker_session()
            prefetched_metadata = batch_resolve_pmids(
                pmids=pmid_targets,
                session=batch_sess,
                ncbi_api_key=args.ncbi_api_key,
                ncbi_email=args.ncbi_email,
                logger=logger
            )

        results: List[Dict[str, Any]] = []
        succeeded = 0
        total_count = len(targets)

        # Worker execution function
        def process_item(item_info: Tuple[int, str]) -> Dict[str, Any]:
            idx, identifier = item_info
            progress_str = f"{idx}/{total_count}"
            sess = create_worker_session()
            browser_downloader = get_worker_browser()
            return download_pubmed_article(
                identifier=identifier,
                output_dir=args.output_dir,
                session=sess,
                email=args.email,
                ezproxy_prefix=args.ezproxy_prefix,
                cookie_str=cookie_str,
                logger=logger,
                force=args.force,
                progress_str=progress_str,
                prefetched_meta=prefetched_metadata.get(identifier),
                save_html_dir=save_html_dir,
                include_supplements=args.include_supplements,
                supplements_dir=args.supplements_dir,
                extract_tables=args.extract_tables,
                tables_dir=args.tables_dir,
                use_browser=args.use_browser,
                browser_downloader=browser_downloader,
                bvbrc=args.bvbrc,
                ncbi_api_key=args.ncbi_api_key,
                ncbi_email=args.ncbi_email
            )

        items = list(enumerate(targets, start=1))

        if args.parallel > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
                res_iter = executor.map(process_item, items)
                for res in res_iter:
                    results.append(res)
                    if res.get("status") == "success":
                        succeeded += 1
        else:
            for item in items:
                res = process_item(item)
                results.append(res)
                if res.get("status") == "success":
                    succeeded += 1

        summary_path = args.summary if args.summary else os.path.join(args.output_dir, "download_summary.json")
        summary_data = {
            "total_requested": len(targets),
            "succeeded": succeeded,
            "failed": len(targets) - succeeded,
            "results": results
        }

        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2)

        total_supplements = sum(r.get("supplements", {}).get("downloaded", 0) for r in results)
        total_tables = sum(r.get("tables", {}).get("count", 0) for r in results)

        logger.log("\n==================================================")
        logger.log("                   FINAL SUMMARY                  ")
        logger.log("==================================================")
        logger.log(f"Total Processed:       {len(targets)}")
        logger.log(f"PDFs Downloaded:       {succeeded}")
        logger.log(f"Paywalled / Failed:    {len(targets) - succeeded}")
        if args.include_supplements:
            logger.log(f"Supplements Saved:     {total_supplements}")
        if args.extract_tables:
            logger.log(f"Tables Extracted:      {total_tables}")
        logger.log(f"Summary JSON:          {os.path.abspath(summary_path)}")
        if log_path:
            logger.log(f"Attempts Log File:     {os.path.abspath(log_path)}")
        logger.log("==================================================")

        # Exit code determination
        if succeeded < len(targets) and not args.no_fail:
            sys.exit(1)
        else:
            sys.exit(0)


if __name__ == "__main__":
    main()
