# LinkChecker-PD — Implementation Plan

A single-file Perl CLI tool that recursively crawls a given URL, checks every link it finds, and reports broken links, invalid anchor fragments, and HTTP errors.

## Proposed Changes

### [NEW] linkchecker.pl

Single-file Perl script. Sections described below.

---

### 1. CLI Parsing (`Getopt::Long`)

```
Usage: perl linkchecker.pl [--output results.tsv] <URL>
```

| Option | Description |
|---|---|
| `<URL>` (positional, required) | The starting URL to crawl |
| `--output FILE` / `-o FILE` | Optional tab-delimited output file |
| `--help` / `-h` | Print usage and exit |

Validates that the URL has an `http://` or `https://` scheme. Dies with usage message otherwise.

---

### 2. Scope Determination

Given a starting URL like `https://example.com/docs/v1/`, the **scope prefix** is the full path up to and including the trailing slash (or the directory portion if no trailing slash):

| Starting URL | Scope prefix |
|---|---|
| `https://example.com/docs/v1/` | `https://example.com/docs/v1/` |
| `https://example.com/docs/v1/index.html` | `https://example.com/docs/v1/` |

A discovered URL is **internal** (in-scope) if:
- Same scheme + host + port as the starting URL
- Path starts with the scope prefix

Everything else is **external** (out-of-scope).

---

### 3. Data Structures

```perl
my %visited;          # URL (without fragment) => HTTP status or error string
my %page_ids;         # URL (without fragment) => { id1 => 1, id2 => 1, ... }
my @results;          # Array of result records for final summary & TSV
my %page_ok;          # URL => 1 (OK) or 0 (has broken links)
```

Each result record is a hash:
```perl
{
    source_url  => "https://example.com/docs/v1/",
    line_number => 42,
    dest_url    => "https://example.com/docs/v1/setup/",
    outcome     => "200 OK"   # or "404 Not Found", "Connection timeout", etc.
}
```

---

### 4. Crawling Algorithm

```
crawl(start_url):
    fetch start_url via GET (10s timeout), following redirects manually (see §4a)
    if error → record, return
    record status in %visited
    parse HTML → extract links with line numbers
    parse HTML → collect all id attrs and <a name="..."> attrs → store in %page_ids

    for each (link, line_number):
        resolve link against base URL → absolute_url
        separate fragment from absolute_url → (url_no_frag, fragment)

        if url_no_frag already in %visited:
            use cached status
        else if url_no_frag is internal:
            GET request, following redirects manually (see §4a)
            record status
            parse for IDs (for fragment checking)
            recurse into crawl(url_no_frag)
        else (external):
            HEAD request (see §4b)

        if fragment AND url_no_frag is internal:
            check fragment against %page_ids{url_no_frag}
            if not found → report "Anchor #fragment not found"

        emit result record to STDOUT immediately
        append to @results
```

#### 4a. Redirect Handling (Internal & External)

`LWP::UserAgent` is configured with `max_redirect => 0` so we always see the raw status code.

When a 3xx response is received:
1. **Emit a result row** for the redirect hop:
   - `source_url` = the page containing the link (original referring page)
   - `line_number` = line on the source page
   - `dest_url` = the URL that returned the 3xx
   - `outcome` = e.g. `301 Moved Permanently -> https://example.com/new-location`
2. **Follow the `Location` header** to the next URL.
3. **Repeat** for up to 10 hops. If the hop limit is exceeded, report `Too many redirects (limit: 10)`.
4. After arriving at the final non-3xx response, **emit a final result row** for that response with the final URL as `dest_url`.

This applies to **both** internal and external URLs. For internal URLs, the final page content is parsed and crawled recursively.

**Example:** A link on line 63 of `https://example.com/docs/v1/` points to `https://example.com/moved`, which 301-redirects to `https://example.com/new-location`, which returns 200. Output:

```
[https://example.com/docs/v1/:63] -> https://example.com/moved ... 301 Moved Permanently -> https://example.com/new-location
[https://example.com/docs/v1/:63] -> https://example.com/new-location ... 200 OK
```

#### 4b. External URL Checking

1. Issue a `HEAD` request.
2. If the response is `405 Method Not Allowed`, **fall back to a `GET` request**.
3. If the response is a 3xx, follow redirects as described in §4a (up to 10 hops), reporting each hop.
4. Report the final status.

---

### 5. HTML Parsing — Link & Anchor Extraction

Using `HTML::Parser` with callbacks, in a **single pass** over each fetched page:

#### Links extracted (with line numbers via `$parser->current_line`):

| Tag | Attribute | Purpose |
|---|---|---|
| `<a>` | `href` | Hyperlinks |
| `<img>` | `src` | Images |
| `<script>` | `src` | Scripts |
| `<link>` | `href` | Stylesheets, icons, etc. |
| `<iframe>` | `src` | Embedded frames |
| `<frame>` | `src` | Framesets |
| `<source>` | `src` | Media sources |
| `<video>` | `src` | Video sources |
| `<audio>` | `src` | Audio sources |
| `<object>` | `data` | Embedded objects |
| `<embed>` | `src` | Embedded content |
| `<form>` | `action` | Form targets |

#### Anchors collected (for fragment validation):

- **Any element** with an `id` attribute → store the `id` value
- **`<a>` elements** with a `name` attribute → store the `name` value

Both are stored in `%page_ids{$url}` for the current page.

---

### 6. Anchor Fragment Validation

For **internal pages only** (per spec):

1. When we fetch and parse an internal page, we collect all `id` attributes (from any element) and `name` attributes (from `<a>` tags) into `%page_ids{$url}`.
2. When a link has a fragment (e.g., `#section-2`), we look up the target page's collected IDs.
3. If the fragment is not found, we report it as an error: `Anchor #section-2 not found`.

**Special case**: A fragment-only link like `#section-2` refers to the **current page**.

---

### 7. URL Normalization

Using the `URI` module:

1. Resolve relative URLs against the current page's base URL using `URI->new_abs($link, $base_url)`
2. Remove default ports (`:80` for HTTP, `:443` for HTTPS)
3. Normalize percent-encoding via `$uri->canonical`
4. Strip fragments before using as the cache key in `%visited`
5. Preserve the original fragment separately for anchor validation
6. Skip non-HTTP(S) URLs (`mailto:`, `javascript:`, `tel:`, `data:`, etc.)

---

### 8. HTTP Client Configuration

```perl
my $ua = LWP::UserAgent->new(
    timeout      => 10,
    max_redirect => 0,          # Handle redirects manually
    agent        => 'LinkChecker-PD/1.0',
);
```

**Error mapping** for non-HTTP errors (LWP internal errors detected via `$response->header('Client-Warning') eq 'Internal response'`):

| LWP Internal Message Pattern | Reported As |
|---|---|
| `Can't connect` | `Connection failed: <reason>` |
| `read timeout` | `Connection timeout (10s)` |
| `SSL connect attempt failed` | `SSL error: <reason>` |
| `Can't resolve host` | `DNS resolution failed` |
| Other internal errors | `Error: <message>` |

---

### 9. Real-Time STDOUT Output

As each link is checked, print immediately:

```
[https://example.com/docs/v1/:42] -> https://example.com/docs/v1/setup/ ... 200 OK
[https://example.com/docs/v1/:57] -> https://example.com/old-page ... 404 Not Found
[https://example.com/docs/v1/:63] -> https://example.com/moved ... 301 Moved Permanently -> https://example.com/new-location
[https://example.com/docs/v1/:63] -> https://example.com/new-location ... 200 OK
[https://example.com/docs/v1/:71] -> https://example.com/docs/v1/api/#bad-anchor ... Anchor #bad-anchor not found
[https://example.com/docs/v1/:80] -> https://example.com/down ... Connection timeout (10s)
```

Redirect chains produce **multiple rows** — one per hop plus the final response.

After the crawl completes, print a summary:

```
=== Summary ===
https://example.com/docs/v1/             OK
https://example.com/docs/v1/setup/       OK
https://example.com/docs/v1/api/         NOT OK (2 broken links)
---
Total pages spidered: 3
Broken links found: 2
```

---

### 10. Optional TSV Output File

Tab-delimited file with a header row, written at the end of the crawl:

```
Source URL	Line	Destination URL	Status
https://example.com/docs/v1/	42	https://example.com/docs/v1/setup/	200 OK
https://example.com/docs/v1/	57	https://example.com/old-page	404 Not Found
https://example.com/docs/v1/	63	https://example.com/moved	301 Moved Permanently -> https://example.com/new-location
https://example.com/docs/v1/	63	https://example.com/new-location	200 OK
```

Redirect chains produce multiple rows in the TSV as well, matching the STDOUT output.

---

### 11. Dependencies

All modules are either core Perl or widely available on CPAN:

| Module | Purpose | Core? |
|---|---|---|
| `Getopt::Long` | CLI argument parsing | ✅ Core |
| `Pod::Usage` | Usage/help message | ✅ Core |
| `URI` | URL parsing & normalization | CPAN (very common) |
| `LWP::UserAgent` | HTTP client | CPAN (very common) |
| `HTML::Parser` | HTML parsing with line numbers | CPAN (very common) |

---

### 12. Script Structure Outline

```perl
#!/usr/bin/env perl
use strict;
use warnings;

# --- Module imports ---
use Getopt::Long;
use Pod::Usage;
use URI;
use LWP::UserAgent;
use HTML::Parser;

# --- Global state ---
# %visited, %page_ids, @results, %page_ok, $ua, $scope_prefix, $output_fh

# --- Subroutines ---

sub parse_args { ... }
# Parse CLI options, validate URL, determine scope prefix.

sub is_internal { ... }
# Given a URL, return true if it falls under the scope prefix.

sub normalize_url { ... }
# Canonicalize a URL: resolve relative, remove default ports, strip fragment.
# Returns ($url_without_fragment, $fragment).

sub fetch_url { ... }
# Fetch a URL via GET. Handle redirects manually (up to 10 hops).
# Returns ($final_response, $final_url, @redirect_records).
# Each redirect_record = { url => ..., status => ..., location => ... }.

sub check_url_head { ... }
# Check a URL via HEAD. If 405, fall back to GET.
# Handle redirects manually (up to 10 hops).
# Returns ($final_response, $final_url, @redirect_records).

sub parse_html { ... }
# Parse HTML content. Returns:
#   \@links  = [ { url => ..., line => ... }, ... ]
#   \%ids    = { id_or_name => 1, ... }

sub classify_error { ... }
# Given an LWP response, return a human-readable error string
# for non-HTTP (internal) errors.

sub emit_result { ... }
# Print a result row to STDOUT immediately.
# Append to @results for later TSV output.
# Update %page_ok tracking.

sub crawl { ... }
# Main recursive crawl function.
# Takes ($url) — the URL to crawl (must be internal/in-scope).
# Fetches, parses, checks each link, recurses for internal links.

sub print_summary { ... }
# Print final summary of all spidered pages with OK/NOT OK status.

sub write_tsv { ... }
# Write @results to the TSV output file (if --output was specified).

# --- Main ---
sub main { ... }
# parse_args → crawl(start_url) → print_summary → write_tsv

main();
```

---

## Verification Plan

### Automated Testing

1. **Install dependencies**: Verify `LWP::UserAgent`, `URI`, and `HTML::Parser` are available; install via `cpanm` if needed.
2. **Create a local test fixture**: A directory of HTML files with:
   - Good internal links
   - Broken internal links (404)
   - Valid and invalid anchor fragments
   - Redirect chains (using a small test server or mocked responses)
   - External links (a known-good URL like `https://www.google.com` and a known-bad one)
   - Resource links (`<img>`, `<script>`, `<link>`)
3. **Serve locally**: `python3 -m http.server 8888` from the test fixture directory.
4. **Run**: `perl linkchecker.pl --output test_results.tsv http://localhost:8888/`
5. **Verify**:
   - All internal pages are recursively spidered
   - External links are checked (HEAD with 405 fallback)
   - Broken links are correctly reported
   - Invalid anchor fragments are reported
   - Redirect hops produce individual result rows
   - TSV output matches STDOUT output
   - Summary lists all spidered pages with correct OK/NOT OK
   - The script terminates (no infinite loops from circular links)

### Manual Verification

- Run the tool against a real documentation site and review the output for correctness.
