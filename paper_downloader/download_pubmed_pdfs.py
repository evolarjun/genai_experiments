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
from metadata import resolve_pmid_metadata, resolve_doi_metadata, batch_resolve_pmids
from tiers import (
    try_pmc_download,
    try_openalex,
    try_unpaywall,
    try_semantic_scholar,
    try_publisher_scrape,
)

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
    save_html_dir: Optional[str] = None
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

    # 2. Check if already exists (Resume / Skip functionality)
    if not force and os.path.exists(out_path):
        size_bytes = os.path.getsize(out_path)
        if size_bytes >= 100:
            logger.log(f"  [SKIPPED] Already downloaded ({size_bytes / 1024.0:.1f} KB at {out_name}). Use --force to re-download.", to_console=True, pmid=identifier)
            return {
                "identifier": identifier,
                "pmid": pmid,
                "status": "success",
                "skipped_existing": True,
                "title": title,
                "journal": meta.get("journal"),
                "doi": doi,
                "pmcid": pmcid,
                "source": "Local Cache (Existing File)",
                "file_path": abs_path,
                "size_bytes": size_bytes
            }

    pdf_bytes = None
    download_url = None
    source = None

    # Tier 1: PubMed Central (PMC) Direct
    if pmcid:
        logger.log(f"  -> Querying Tier 1: PubMed Central ({pmcid})...", to_console=True, pmid=identifier)
        res = try_pmc_download(pmcid, session=session, logger=logger, pmid=identifier, save_html_dir=save_html_dir)
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
            save_html_dir=save_html_dir
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
        return {
            "identifier": identifier,
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
        landing_url = f"https://doi.org/{doi}" if doi else (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else f"https://doi.org/{identifier}")
        logger.log(f"  [PAYWALLED / WAF RESTRICTED] Landing Page: {landing_url}", to_console=True, pmid=identifier)
        logger.log(
            f"DOWNLOAD STATUS: FAILED - Paper download failed "
            f"(ID: {identifier}, Landing Page: {landing_url}, Reason: Article is paywalled or required institutional authentication)",
            to_console=True,
            pmid=identifier
        )
        return {
            "identifier": identifier,
            "pmid": pmid,
            "status": "failed",
            "title": meta.get("title"),
            "journal": meta.get("journal"),
            "doi": doi,
            "pmcid": pmcid,
            "landing_url": landing_url,
            "reason": (
                "Article is paywalled, requires subscription authentication, or blocked by bot protection. "
                "Pass --cookie-file, --cookie-header, or institutional VPN/EZproxy to access."
            )
        }


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
                save_html_dir=save_html_dir
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

        logger.log("\n==================================================")
        logger.log("                   FINAL SUMMARY                  ")
        logger.log("==================================================")
        logger.log(f"Total Processed:       {len(targets)}")
        logger.log(f"PDFs Downloaded:       {succeeded}")
        logger.log(f"Paywalled / Failed:    {len(targets) - succeeded}")
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
