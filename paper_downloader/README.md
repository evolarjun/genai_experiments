# PubMed PDF Downloader

This tool downloads full-text article PDFs for PubMed IDs (PMIDs) and DOIs. A PMID is a unique number that identifies a PubMed record. A DOI is a permanent digital object identifier for an article. The tool searches open-access archives, PubMed Central, and publisher websites.

## Requirements

You must have Python 3.8 or a newer version installed on your computer. The tool uses the standard library of Python. You do not have to install extra packages to run the tool. If you want a progress bar or test tools, install the optional packages from requirements.txt.

## Installation

Follow these steps to set up the software on your computer.

1. Clone the repository or download the source files to your computer.
2. Open your terminal and go to the project directory.
3. If you want the optional packages, run the install command with pip:

```bash
pip install -r requirements.txt
```

The command installs the optional packages. You can now run the downloader script.

## Basic usage

You run the tool with the script download_pubmed_pdfs.py. If you want to download articles by ID, pass one or more PMIDs or DOIs with the --pmids flag:

```bash
python3 download_pubmed_pdfs.py --pmids 24592257 10.1093/jac/dkn123
```

By default, the script saves downloaded files into the downloaded_pdfs directory. If you want to save PDFs to a specific folder, use the -o or --output-dir flag:

```bash
python3 download_pubmed_pdfs.py --pmids 24592257 --output-dir ./my_pdfs
```

If you want to download articles from a file, put one ID on each line in a text file. The script ignores empty lines and lines that start with a hash symbol. Run the script with the -f or --file flag:

```bash
python3 download_pubmed_pdfs.py --file blaEC_point_mut.pmid
```

## Existing files and resume

The tool skips files that already exist in the output folder. It makes sure that the file size is at least 100 bytes. If you want to download all files again, add the --force flag:

```bash
python3 download_pubmed_pdfs.py --file blaEC_point_mut.pmid --force
```

The script will overwrite the existing PDF files in the target directory.

## Parallel downloads

By default, the script downloads one article at a time. If you have many articles, you can download several files at the same time. Use the --parallel flag with the number of worker threads:

```bash
python3 download_pubmed_pdfs.py --file blaEC_point_mut.pmid --parallel 4
```

The tool tracks rate limits for each web domain separately. Workers that connect to different websites run without delay.

## Logs and summary reports

The tool creates a JSON summary file named download_summary.json in the output folder. The file lists the status, file path, and source for each article. If you want a text log of every download attempt, add the -l or --log-file flag:

```bash
python3 download_pubmed_pdfs.py --file blaEC_point_mut.pmid --log-file
```

If you want a custom log file location, pass the path after the flag. The log file records timestamps and server responses for each request.

## Institutional access

Some articles require a paid subscription from an academic institution. If you are on campus, connect to your campus Wi-Fi network or university VPN. If your university uses an EZproxy server, pass the login URL prefix with --ezproxy-prefix:

```bash
python3 download_pubmed_pdfs.py --file blaEC_point_mut.pmid --ezproxy-prefix "https://ezproxy.univ.edu/login?url="
```

If you have a cookies.txt file from an authenticated browser session, pass the file with --cookie-file:

```bash
python3 download_pubmed_pdfs.py --file blaEC_point_mut.pmid --cookie-file cookies.txt
```

If you copied a cURL command from browser developer tools, pass the command with --curl-cmd:

```bash
python3 download_pubmed_pdfs.py --pmids 12821459 --curl-cmd "curl 'https://publisher.com' -H 'cookie: ...' -H 'user-agent: ...'"
```

The script extracts the session cookies and the browser User-Agent from the command.

## Automated tests

The project includes unit tests for all modules. You do not need network access to run the test suite. If you want to run the tests, use the Python unittest module:

```bash
python3 -m unittest discover tests -v
```

If you installed pytest, you can run pytest instead:

```bash
pytest tests/ -v
```

The command runs all test cases and shows the results.
