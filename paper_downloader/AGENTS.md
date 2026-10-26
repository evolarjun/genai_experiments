# AGENTS.md

This document provides context, usage instructions, architectural details, and operational guidance for AI agents working with this PubMed PDF Downloader tool.

---

## 1. Overview & Purpose

The `paper_downloader` project provides an automated tool ([download_pubmed_pdfs.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/download_pubmed_pdfs.py)) to accept PubMed IDs (PMIDs) or DOIs, resolve paper metadata, and download full-text article PDFs.

Because academic articles are distributed across various open-access repositories, open archives, and publisher platforms, the tool implements a **cascading multi-tier fallback pipeline** to maximize retrieval rates while maintaining compliance and validation. It also supports **Institutional Access** via campus IP/VPN, EZproxy prefixes, exported Netscape cookie files, raw cookie headers, browser DevTools cURL command import, and network proxies to automatically fetch subscription-restricted (paywalled) articles.

---

## 2. Quick Start & CLI Reference

### Command Line Examples

#### Basic Usage (PMIDs and DOIs)

```bash
# Download PDFs for specific PMIDs or DOIs:
python3 download_pubmed_pdfs.py --pmids 24592257 10.1093/jac/dkn123 --output-dir ./downloaded_pdfs

# Download PDFs from a list stored in a text file (one PMID or DOI per line):
python3 download_pubmed_pdfs.py --file pmids.txt --output-dir ./downloaded_pdfs

# Run on the included sample PMID dataset:
python3 download_pubmed_pdfs.py --file blaEC_point_mut.pmid --output-dir ./downloaded_pdfs
```

#### Resume & Skipping Existing Files

```bash
# By default, files already present in output-dir (size >= 100 bytes) are skipped:
python3 download_pubmed_pdfs.py --file pmids.txt

# Force re-downloading all items regardless of existing files:
python3 download_pubmed_pdfs.py --file pmids.txt --force
```

#### Concurrency, Rate Limiting & Networking

```bash
# Concurrency: Run with 4 parallel worker threads (default is 1 sequential):
python3 download_pubmed_pdfs.py --file pmids.txt --parallel 4

# Custom timeout (seconds) and per-domain rate limit delay (seconds):
python3 download_pubmed_pdfs.py --file pmids.txt --timeout 45 --rate-limit 0.5

# Provide custom contact email for Unpaywall / API polite pools:
python3 download_pubmed_pdfs.py --file pmids.txt --email researcher@univ.edu

# Disable SSL verification (useful if system root certificates are unconfigured):
python3 download_pubmed_pdfs.py --file pmids.txt --no-verify-ssl
```

#### Logging & Summaries

```bash
# Enable attempt logging with default filename (download_attempts.log in output-dir):
python3 download_pubmed_pdfs.py --file pmids.txt --log-file

# Specify custom log file location:
python3 download_pubmed_pdfs.py --file pmids.txt --log-file ./logs/attempts.log

# Specify custom location for JSON summary report:
python3 download_pubmed_pdfs.py --file pmids.txt --summary ./reports/run_summary.json

# CI / Scripting: Exit with code 0 even if some papers fail to download:
python3 download_pubmed_pdfs.py --file pmids.txt --no-fail
```

#### Institutional Access Options

```bash
# 1. Automatic IP / Campus Network / Institutional VPN:
# Simply run the script while connected to campus Wi-Fi or institutional VPN.
python3 download_pubmed_pdfs.py --file pmids.txt

# 2. EZproxy Prefix:
python3 download_pubmed_pdfs.py --file pmids.txt --ezproxy-prefix "https://ezproxy.univ.edu/login?url="

# 3. Exported Cookies (Netscape/Mozilla format from extensions like 'Get cookies.txt LOCALLY'):
python3 download_pubmed_pdfs.py --file pmids.txt --cookie-file cookies.txt

# 4. Raw Cookie Header string:
python3 download_pubmed_pdfs.py --file pmids.txt --cookie-header "ezproxy=xyz123; session_id=abc456"

# 5. Browser DevTools 'Copy as cURL' import (extracts cookies and matching User-Agent):
# Via command argument:
python3 download_pubmed_pdfs.py --file pmids.txt --curl-cmd "curl 'https://publisher.com' -H 'cookie: ...' -H 'user-agent: ...'"

# Via cURL command saved to a text file:
python3 download_pubmed_pdfs.py --file pmids.txt --curl-file ./browser_curl.txt

# 6. Institutional Network Proxy:
python3 download_pubmed_pdfs.py --file pmids.txt --proxy "http://proxy.univ.edu:8080"
```

---

### CLI Parameter Reference Table

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--pmids` | | Space-separated list | `None` | One or more PMIDs or DOIs to download. |
| `--file` | `-f` | String (path) | `None` | Path to text file containing PMIDs/DOIs (one per line, `#` comments ignored). |
| `--output-dir` | `-o` | String (path) | `./downloaded_pdfs` | Directory where downloaded `.pdf` files are saved. |
| `--summary` | | String (path) | `{output_dir}/download_summary.json` | Path to write the JSON summary report. |
| `--log-file` | `-l` | String (optional path) | `download_attempts.log` | Records timestamped attempt logs. If passed without value, defaults to `download_attempts.log` in output dir. |
| `--email` | | String | `paperdownloader@example.com` | Contact email for Unpaywall API requests. |
| `--rate-limit` | | Float | `0.5` | Minimum seconds between HTTP requests to the same domain. |
| `--timeout` | | Integer | `30` | Request timeout in seconds. |
| `--parallel` | | Integer | `1` | Number of concurrent worker threads. |
| `--force` | | Boolean flag | `False` | Force re-download even if output PDF already exists. |
| `--no-fail` | | Boolean flag | `False` | Exit with status code `0` even if downloads fail. |
| `--no-verify-ssl` | | Boolean flag | `False` | Disable SSL certificate verification (insecure). |
| `--ezproxy-prefix` | | String (URL) | `None` | Institutional EZproxy URL prefix (e.g. `https://ezproxy.univ.edu/login?url=`). |
| `--cookie-file` | | String (path) | `None` | Path to Netscape/Mozilla `cookies.txt` file for session reuse. |
| `--cookie-header` | | String | `None` | Raw HTTP `Cookie` header string. |
| `--curl-cmd` | | String | `None` | Browser DevTools "Copy as cURL" command string (extracts cookies & UA). |
| `--curl-file` | | String (path) | `None` | Path to file containing a browser DevTools "Copy as cURL" command. |
| `--proxy` | | String (URL) | `None` | HTTP/HTTPS proxy URL (e.g. `http://proxy.univ.edu:8080`). |
| `--ncbi-api-key` | | String | `None` (or `$NCBI_API_KEY`) | NCBI API key raising E-Utilities rate limits to 10 req/s. |
| `--ncbi-email` | | String | `None` (or `$NCBI_EMAIL`) | Contact email for NCBI E-Utilities requests. |
| `--save-html-samples` | | String (optional path) | `None` | Output directory to save HTML challenge or paywall bodies for debugging. Defaults to `anti_bot_samples` in output dir if passed without value. |
| `--include-supplements` | | Boolean flag | `False` | Discover and download supplementary material files (spreadsheets, datasets, appendices). |
| `--supplements-dir` | | String (path) | `{output_dir}/supplements` | Directory to save supplementary files. |
| `--extract-tables` | | Boolean flag | `False` | Extract embedded main-text tables from PMC JATS XML into formatted TSV files. |
| `--tables-dir` | | String (path) | `{output_dir}/tables` | Directory to save extracted TSV tables. |
| `--use-browser` | | Boolean flag | `False` | Enable Playwright headless Chromium fallback for anti-bot or JS interstitial challenges. |
| `--bvbrc` | | Boolean flag | `False` | Query BV-BRC Data API for bacterial genomes and accessions linked to PMID. |

---

### Identifier & Filename Handling

1. **Target Identification**:
   - Items starting with `10.`, containing `/`, or starting with `doi:` are treated as DOIs.
   - All other numeric identifiers are treated as PMIDs.
   - Target inputs are automatically deduplicated while preserving original order.
2. **File Naming**:
   - If a PMID is available (either provided directly or resolved from DOI metadata), the file is saved as `{pmid}.pdf`.
   - If only a DOI is available, illegal path characters (`/ \ : ? * " < > | \s`) are replaced with underscores to produce `{sanitized_doi}.pdf`.

---

## 3. Architecture & Resolution Tiers

When given a PubMed ID or DOI, the script executes:

```mermaid
flowchart TD
    Start([Input: PMID or DOI]) --> Meta[Metadata Resolution\nEurope PMC / NCBI ID Converter / CrossRef]
    Meta --> CheckCache{File exists in\noutput dir & size >= 100B?}
    CheckCache -- Yes (unless --force) --> Skip[Skip & Return Local Cache Status]
    CheckCache -- No / Force --> T1{Tier 1: PubMed Central\nHas PMCID?}
    T1 -- Yes --> PMC[PMC Direct & PoW Solver]
    PMC -- Valid PDF --> Success([Saved PDF])
    PMC -- No PDF --> T2[Tier 2: OpenAlex API]
    T1 -- No --> T2
    T2 -- Valid PDF --> Success
    T2 -- No PDF --> T3{Tier 3: Unpaywall API\nHas DOI?}
    T3 -- Yes --> Unpaywall[Query Unpaywall API]
    Unpaywall -- Valid PDF --> Success
    Unpaywall -- No PDF --> T4[Tier 4: Semantic Scholar API]
    T3 -- No --> T4
    T4 -- Valid PDF --> Success
    T4 -- No PDF --> T5{Tier 5: Publisher Scrape\nHas DOI?}
    T5 -- Yes --> PubScrape[Follow DOI / EZproxy\nCrossRef PDF URLs\nScrape meta citation_pdf_url\nPublisher pattern heuristics\nScoped Cookies / VPN]
    PubScrape -- Valid PDF --> Success
    PubScrape -- No PDF --> Fail([Mark Paywalled / Failed\nRecord Landing URL])
    T5 -- No --> Fail
```

### 1. Metadata Resolution ([metadata.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/metadata.py))
- **NCBI ESummary Batch Resolution**:
  - Automatically batches requested PMIDs in chunks of up to 200 via `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi` before the download loop starts.
  - Pulls paper title, first author, publication year, PMCID, and DOI directly from NCBI PubMed.
  - Supports `--ncbi-api-key` / `NCBI_API_KEY` and `--ncbi-email` / `NCBI_EMAIL` to raise rate limits and ensure reliable throughput.
- **PMID Resolution (Fallback & Single Lookup)**:
  - Queries Europe PMC REST API (`https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:{pmid} AND SRC:MED&format=json`).
  - Falls back to NCBI ID Converter (`https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids={pmid}&format=json`) if PMCID or DOI is missing.
  - Automatically retries Europe PMC if initial attempts were affected by transient SSL auto-downgrade.
- **DOI Resolution**:
  - Queries NCBI ID Converter first to discover any mapped PMID or PMCID.
  - Queries CrossRef REST API (`https://api.crossref.org/works/{doi}`) for paper title and journal container name.
  - If a PMID was discovered, enriches metadata using Europe PMC.

### 2. Cascading Retrieval Pipeline ([tiers.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/tiers.py))
- **Tier 1 (PubMed Central - PMC)**:
  - Fetches the PMC article landing page (`https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/`).
  - Passes the landing page as an HTTP `Referer` header on subsequent PDF fetch requests to prevent CDN bot blocking.
  - Scans for `<meta name="citation_pdf_url">`, anchor tags linking to `/pdf/` or `.pdf`, and default `/pdf/` endpoints.
  - **Automated JavaScript Proof-of-Work (PoW) Solver**: When NCBI issues an anti-bot PoW HTML challenge containing `POW_CHALLENGE`, `POW_DIFFICULTY`, and `POW_COOKIE_NAME`, [solve_pmc_pow](file:///Users/aprasad/dev/genai_experiments/paper_downloader/tiers.py#L22-L48) performs iterative SHA-256 hashing to solve the nonce and injects the resulting cookie into the session cookie jar for `.ncbi.nlm.nih.gov` and `pmc.ncbi.nlm.nih.gov`.
- **Tier 2 (OpenAlex API)**:
  - Queries `https://api.openalex.org/works/pmid:{pmid}` or `doi:{doi}`.
  - Parses `locations` objects for direct open-access repository PDF URLs (`pdf_url`).
- **Tier 3 (Unpaywall API)**:
  - Queries `https://api.unpaywall.org/v2/{doi}?email={email}`.
  - Tests `best_oa_location` and all alternate `oa_locations` for open-access PDF links.
- **Tier 4 (Semantic Scholar API)**:
  - Queries `https://api.semanticscholar.org/graph/v1/paper/{ident}?fields=openAccessPdf`.
  - Downloads PDF directly if `openAccessPdf.url` is returned.
- **Tier 5 (Direct Publisher HTML Scrape & Institutional Access)**:
  - Scoped cookies and institutional proxy / EZproxy are attached **only** during this tier to prevent leaking private credentials to third-party open access APIs.
  - Probes CrossRef REST API direct PDF link specifications ([try_crossref_pdf_url](file:///Users/aprasad/dev/genai_experiments/paper_downloader/tiers.py#L343-L367)).
  - Follows `https://doi.org/{doi}` (rewritten via `--ezproxy-prefix` if configured). Handles both query param (`?url=`) and prefix rewriting. Passes publisher landing page as HTTP `Referer` header.
  - Checks if the response itself is already a raw PDF.
  - Scrapes HTML `<meta>` tags: `citation_pdf_url` and `bepress_citation_pdf_url`.
  - Scrapes publisher-specific PDF URL patterns from anchor links:
    - ScienceDirect / Elsevier: `/article/pii/.../pdfft`
    - Wiley / ASM / ACS: `/doi/pdf/...` and `/doi/pdfdirect/...`
    - Springer Link: `/content/pdf/...`
    - Nature: `/articles/...pdf`
    - Oxford Academic: `/article-pdf/...`
    - Generic `.pdf` links

### 3. HTTP Client & Validation Engine ([http_client.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/http_client.py))
- **Connection & Opener Reuse**: Builds reusable `urllib.request` openers with `HTTPCookieProcessor`, `HTTPSHandler`, and optional `ProxyHandler`.
- **Credential Isolation**: Maintains two distinct openers:
  - `opener_with_cookies`: Used for Tier 1 (PMC PoW cookies) and Tier 5 (Institutional cookies/sessions).
  - `opener_without_cookies`: Used for open-access APIs (Europe PMC, CrossRef, OpenAlex, Unpaywall, Semantic Scholar) so institutional tokens are never leaked.
- **Domain Rate Limiter ([DomainRateLimiter](file:///Users/aprasad/dev/genai_experiments/paper_downloader/http_client.py#L32-L58))**: Thread-safe per-domain access tracker. Workers downloading from different domains proceed without delay, while requests targeting the same host respect `--rate-limit` (default: 0.5s). Automatically scales NCBI domain delays down to 0.1s (10 req/s) when `--ncbi-api-key` is detected.
- **Anti-Bot / Paywall Sample Logging**: When `--save-html-samples` is configured, raw HTML challenge bodies returned instead of PDFs are saved to disk under `<output_dir>/anti_bot_samples/` for inspection and debugging.
- **Exponential Backoff Retries**: Automatically retries HTTP 429 (rate limited), 500, 502, 503, and 504 errors up to 3 times, respecting server-provided `Retry-After` headers.
- **Automatic macOS SSL Auto-Downgrade**: On macOS, standard Python installations may lack bundled root certificates (producing `SSLCertVerificationError`). The client detects this condition, logs instructions on running `Install Certificates.command` or `--no-verify-ssl`, automatically downgrades the SSL context for the session, and seamlessly retries the request.
- **PDF Verification ([is_valid_pdf](file:///Users/aprasad/dev/genai_experiments/paper_downloader/http_client.py#L60-L68))**: Enforces minimum payload size (>= 100 bytes) and verifies standard magic bytes (`%PDF` at byte 0 or `%PDF-` within the first 1024 bytes). Detects when an HTML page was returned instead of a PDF (paywall challenge).

---

## 4. Key Files & Structure

- [download_pubmed_pdfs.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/download_pubmed_pdfs.py): Main CLI orchestrator with argument parsing, parallel execution, resume/skip logic, and summary generation.
- [http_client.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/http_client.py): HTTP session manager, connection pooling, rate limiter, cURL parser, and PDF validator.
- [metadata.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/metadata.py): Metadata resolver for PMIDs and DOIs (Europe PMC, NCBI ID Converter, CrossRef, BV-BRC Data API).
- [tiers.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/tiers.py): Implementation of Tiers 1 through 5, CrossRef link resolution, and PMC Proof-of-Work solver.
- [xml_extractor.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/xml_extractor.py): PMC JATS XML parser for table extraction, supplement discovery, Data Availability mining, and publisher XML restriction detection.
- [supplements.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/supplements.py): Supplementary file downloader with PMC binary endpoint construction and PoW solver integration.
- [browser_downloader.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/browser_downloader.py): Optional headless Chromium browser fallback (Playwright) for JS proof-of-work and dynamic challenges.
- [blaEC_point_mut.pmid](file:///Users/aprasad/dev/genai_experiments/paper_downloader/blaEC_point_mut.pmid): Sample list of test PMIDs.
- `tests/`: Automated unit test suite.
  - [test_http_client.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/tests/test_http_client.py)
  - [test_metadata.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/tests/test_metadata.py)
  - [test_tiers.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/tests/test_tiers.py)
  - [test_cli.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/tests/test_cli.py)
  - [test_xml_extractor.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/tests/test_xml_extractor.py)
  - [test_supplements.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/tests/test_supplements.py)
  - [test_browser_downloader.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/tests/test_browser_downloader.py)
  - [test_bvbrc.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/tests/test_bvbrc.py)
  - [test_cli_enhanced.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/tests/test_cli_enhanced.py)
- `downloaded_pdfs/`: Default output directory for downloaded `.pdf` files.
- `downloaded_pdfs/download_summary.json`: JSON summary report detailing status (`success` | `failed`), metadata, resolution source, file size, and landing URLs for paywalled articles.
- `downloaded_pdfs/download_attempts.log`: Default attempts log file generated when `--log-file` option is used.
- [requirements.txt](file:///Users/aprasad/dev/genai_experiments/paper_downloader/requirements.txt): Optional convenience dependencies (`tqdm`, `pytest`).
- [AGENTS.md](file:///Users/aprasad/dev/genai_experiments/paper_downloader/AGENTS.md): This documentation file.

---

## 5. Output Summary Schema & Status Logging

### Output Summary Schema (`download_summary.json`)

The script writes a structured JSON report to `--summary` (default: `{output_dir}/download_summary.json`).

```json
{
  "total_requested": 3,
  "succeeded": 2,
  "failed": 1,
  "results": [
    {
      "identifier": "24592257",
      "pmid": "24592257",
      "status": "success",
      "title": "Emergence of Escherichia coli producing extended-spectrum AmpC β-lactamases (ESAC) in animals.",
      "journal": "Front Microbiol",
      "doi": "10.3389/fmicb.2014.00053",
      "pmcid": "PMC3924575",
      "source": "OpenAlex API",
      "download_url": "https://www.frontiersin.org/articles/10.3389/fmicb.2014.00053/pdf",
      "file_path": "/path/to/downloaded_pdfs/24592257.pdf",
      "size_bytes": 1055060
    },
    {
      "identifier": "16801449",
      "pmid": "16801449",
      "status": "success",
      "skipped_existing": true,
      "title": "Structural basis of the interaction between...",
      "journal": "J Mol Biol",
      "doi": "10.1016/j.jmb.2006.05.044",
      "pmcid": null,
      "source": "Local Cache (Existing File)",
      "file_path": "/path/to/downloaded_pdfs/16801449.pdf",
      "size_bytes": 482104
    },
    {
      "identifier": "12821459",
      "pmid": "12821459",
      "status": "failed",
      "title": "High-level expression of ampC beta-lactamase...",
      "journal": "Antimicrob Agents Chemother",
      "doi": "10.1128/aac.47.7.2138-2144.2003",
      "pmcid": "PMC161857",
      "landing_url": "https://doi.org/10.1128/aac.47.7.2138-2144.2003",
      "reason": "Article is paywalled, requires subscription authentication, or blocked by bot protection. Pass --cookie-file, --cookie-header, or institutional VPN/EZproxy to access."
    }
  ]
}
```

### Machine-Parseable Status Logging

During execution, the CLI prints standardized single-line status messages to stdout and the log file that can be parsed with regex:

```text
DOWNLOAD STATUS: SUCCESS - Paper successfully downloaded (ID: 24592257, Source: OpenAlex API, Size: 1055060 bytes, File: /path/to/downloaded_pdfs/24592257.pdf)
DOWNLOAD STATUS: FAILED - Paper download failed (ID: 12821459, Landing Page: https://doi.org/10.1128/aac.47.7.2138-2144.2003, Reason: Article is paywalled or required institutional authentication)
```

---

## 6. Running Tests

Run the full unit test suite without external dependencies using Python's standard `unittest`:

```bash
python3 -m unittest discover tests -v
```

Or with `pytest` if installed:

```bash
pytest tests/ -v
```

---

## 7. Guidelines for AI Agents

- **Zero Mandatory Dependencies**: Runs entirely on Python 3.8+ standard library (`urllib`, `http.cookiejar`, `ssl`, `json`, `concurrent.futures`, `re`, `hashlib`).
- **Interactive Browser Subagent Pairing**: When encountering paywalled articles in an automated agent loop, agents can open an authenticated browser session, log into the campus portal, copy a browser request as cURL, and invoke `download_pubmed_pdfs.py` with `--curl-file` or `--curl-cmd`.
- **Per-Domain Rate Limits**: Default delay is 0.5s per domain. Parallel workers (`--parallel N`) run concurrently across different domains without triggering 429 throttling on any single host.
- **Idempotency & Resume**: Running the script multiple times against the same output directory is safe. Existing files (>= 100 bytes) are skipped with `skipped_existing: true` unless `--force` is passed.
- **Exit Codes**: Returns exit code `1` when any requested paper fails to download, making it easy to trap failures in pipelines. Use `--no-fail` to return exit code `0` when partial downloads are acceptable.
- **Path Verification**: Always verify that the returned `file_path` exists on disk and is non-empty before processing downloaded PDFs.
