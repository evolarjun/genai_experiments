# AGENTS.md

This document provides context, usage instructions, architectural details, and operational guidance for AI agents working with this PubMed PDF Downloader tool.

---

## 1. Overview & Purpose

The `paper_downloader` project provides an automated tool ([download_pubmed_pdfs.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/download_pubmed_pdfs.py)) to accept PubMed IDs (PMIDs), resolve paper metadata, and download full-text article PDFs.

Because academic articles are distributed across various open-access repositories, open archives, and publisher platforms, the tool implements a **cascading multi-tier fallback pipeline** to maximize retrieval rates while maintaining compliance and validation. It also supports **Institutional Access** via campus IP/VPN, EZproxy prefixes, cookie session reuse, and network proxies to automatically fetch subscription-restricted (paywalled) articles.

---

## 2. Quick Start

### Command Line Interface

```bash
# Download PDFs for specific PMIDs:
python3 download_pubmed_pdfs.py --pmids 24592257 34265844 --output-dir ./downloaded_pdfs

# Download PDFs from a list stored in a text file (one PMID per line):
python3 download_pubmed_pdfs.py --file pmids.txt --output-dir ./downloaded_pdfs

# Download PDFs with detailed attempt logging enabled:
python3 download_pubmed_pdfs.py --file pmids.txt --log-file

# Specify custom log file location:
python3 download_pubmed_pdfs.py --file pmids.txt --log-file ./logs/attempts.log

# Specify custom contact email (for Unpaywall API) and rate limits:
python3 download_pubmed_pdfs.py --file pmids.txt --email research@example.com --rate-limit 0.5
```

### Institutional Access Options

```bash
# 1. Automatic IP / Campus Network / Institutional VPN:
# Simply run the script while connected to campus Wi-Fi or institutional VPN.
python3 download_pubmed_pdfs.py --file pmids.txt

# 2. EZproxy Prefix:
python3 download_pubmed_pdfs.py --file pmids.txt --ezproxy-prefix "https://ezproxy.univ.edu/login?url="

# 3. Exported Cookies (Browser Session):
python3 download_pubmed_pdfs.py --file pmids.txt --cookie-file cookies.txt

# 4. Institutional Proxy:
python3 download_pubmed_pdfs.py --file pmids.txt --proxy "http://proxy.univ.edu:8080"
```

---

## 3. Architecture & Resolution Tiers

When given a PubMed ID, the script performs:

1. **Metadata Resolution**:
   - Queries Europe PMC REST API (`https://www.ebi.ac.uk/europepmc/webservices/rest/search`) and NCBI ID Converter (`https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/`).
   - Extracts title, journal, DOI, PubMed Central ID (PMC ID), and Open Access flags.

2. **Cascading Retrieval Pipeline**:
   - **Tier 1 (PubMed Central - PMC)**: Direct retrieval & JavaScript Proof-of-Work solver.
   - **Tier 2 (OpenAlex API)**: Queries OpenAlex (`https://api.openalex.org/works/pmid:{pmid}`) for direct open-access repository PDF URLs.
   - **Tier 3 (Unpaywall API)**: Queries Unpaywall (`https://api.unpaywall.org/v2/{doi}`) using DOI for open-access PDF links.
   - **Tier 4 (Semantic Scholar API)**: Queries Semantic Scholar (`https://api.semanticscholar.org/graph/v1/paper/PMID:{pmid}`) for open-access PDF URLs.
   - **Tier 5 (Direct Publisher Scrape)**: Follows `https://doi.org/{doi}` (or via EZproxy prefix) and parses HTML `<meta name="citation_pdf_url">` tags and publisher-specific PDF URL schemes (Elsevier, Wiley, Springer, Nature, ASM, etc.) using cookie-persistent requests.

3. **PDF Validation & Detailed Attempt Logging**:
   - Verifies HTTP 200 response status.
   - Verifies payload size (>500 bytes) and checks for magic header `%PDF-`.
   - Distinguishes between successful downloads and paywalled/restricted articles.
   - When `--log-file` (`-l`) is passed, records timestamped entries for every tier attempt, candidate URL tried, PoW challenge outcome, and explicit `DOWNLOAD STATUS: SUCCESS` / `DOWNLOAD STATUS: FAILED` lines for each PMID.

---

## 4. Key Files & Structure

- [download_pubmed_pdfs.py](file:///Users/aprasad/dev/genai_experiments/paper_downloader/download_pubmed_pdfs.py): Main Python executable script.
- `downloaded_pdfs/`: Default output directory for downloaded `.pdf` files.
- `downloaded_pdfs/download_summary.json`: JSON summary report detailing status (`success` | `failed`), metadata, resolution source, file size, and landing URLs for paywalled articles.
- `downloaded_pdfs/download_attempts.log`: Default attempts log file generated when `--log-file` option is used.
- [AGENTS.md](file:///Users/aprasad/dev/genai_experiments/paper_downloader/AGENTS.md): This documentation file.

---

## 5. Output Summary Schema (`download_summary.json`)

```json
{
  "total_requested": 7,
  "succeeded": 1,
  "failed": 6,
  "results": [
    {
      "pmid": "24592257",
      "status": "success",
      "title": "Emergence of Escherichia coli producing extended-spectrum AmpC β-lactamases (ESAC) in animals.",
      "journal": "Front Microbiol",
      "doi": "10.3389/fmicb.2014.00053",
      "pmcid": "PMC3924575",
      "source": "OpenAlex API",
      "download_url": "https://www.frontiersin.org/articles/10.3389/fmicb.2014.00053/pdf",
      "file_path": ".../downloaded_pdfs/24592257.pdf",
      "size_bytes": 1055060
    },
    {
      "pmid": "12821459",
      "status": "failed",
      "title": "High-level expression of ampC beta-lactamase...",
      "journal": "Antimicrob Agents Chemother",
      "doi": "10.1128/aac.47.7.2138-2144.2003",
      "pmcid": "PMC161857",
      "landing_url": "https://doi.org/10.1128/aac.47.7.2138-2144.2003",
      "reason": "Article is paywalled or required institutional authentication."
    }
  ]
}
```

---

## 6. Guidelines for Agents

- **No Third-Party Python Package Dependencies Required**: Uses standard library modules (`urllib`, `http.cookiejar`, `json`, `re`, `ssl`, `argparse`, `os`).
- **Rate Limits**: Maintain a minimum delay between requests (`--rate-limit 0.5`) to avoid triggering HTTP 429 rate limit errors from OpenAlex, Europe PMC, or Unpaywall.
- **Paywalled Articles**: If an article fails, recommend connecting to institutional VPN or passing `--ezproxy-prefix` / `--cookie-file`.
