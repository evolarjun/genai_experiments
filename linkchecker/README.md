# LinkChecker-PD

LinkChecker-PD is a single-threaded, highly capable recursive Perl CLI crawler that validates links on web pages. Given a starting URL, it fetches the page, scans it for a wide variety of resource links, and validates them. It recursively spiders internal connections while gracefully validating external links without crawling out of scope.

## Features & Edge Cases Handled

- **Comprehensive Link Extraction:** Scans not just `<a>` hyperlinks, but all resource attributes (`img src`, `script src`, `link href`, `iframe src`, `video src`, `audio src`, `object data`, `embed src`, `form action`, etc.).
- **Smart Scope Enforcement:** The crawler calculates a "scope prefix" derived from the start URL (usually by stripping the final path segment). It will only recursively crawl URLs that match this scope prefix. Alternatively, you can rigorously enforce this boundary via the `--root` command-line option.
- **Anchor Fragment Validation:** Ensures `#fragments` link to actual elements existing on internal web pages. It looks for matching `id` attributes on *any* element and matches `<a name="...">` attributes.
- **Dynamic Redirect Chains:** Automatically traces `3xx` HTTP redirect chains (up to 10 hops) for both internal and external links. Real-time logging maps the URL causing the redirect -> the next hop with the `[URL:NA]` line number formatting natively ensuring explicit clarity on *where* the redirects route.
- **Intelligent External Validation:** Out-of-scope (external) links are validated using rapid HTTP `HEAD` requests. If a server denies the `HEAD` request (`405 Method Not Allowed`), the crawler safely falls back to a `GET` request. 
- **Graceful SSL Operations:** Performs verification against `HTTPS` targets without forcing reliance on localized macOS CA certificate bundles which regularly throw default connection `500` errors.

## Usage

```bash
perl linkchecker.pl [options] <URL>
```

### Options

- `-o, --output FILE` : Outputs the real-time link checks into an optional tab-delimited file.
- `-r, --root URL`    : Limits spidering purely to URLs located beneath this root. If not passed, the root is derived automatically from the tested command-line URL.
- `-s, --skip FILE`   : Points to a text file containing line-separated URL prefixes to ignore. Skipping a link emits the outcome as `Skipped`.
- `-h, --help`        : Print the help options message.

### Output Example

The crawler logs output in real-time straight to STDOUT showcasing `[Source:LineNumber] -> Target ... Outcome`:

```text
Starting crawl of http://localhost:8889/
[http://localhost:8889/:1] -> http://localhost:8889/r1 ... 301 Moved Permanently -> http://localhost:8889/r2
[http://localhost:8889/r1:NA] -> http://localhost:8889/r2 ... 302 Found -> http://localhost:8889/final
[http://localhost:8889/r2:NA] -> http://localhost:8889/final ... 200 OK

=== Summary ===
http://localhost:8889/                             OK
http://localhost:8889/final                        OK
---
Total pages spidered: 2
```

## Dependencies

LinkChecker-PD relies strictly on Perl core libraries and extremely common CPAN module expansions:
- `LWP::UserAgent` - For core robust HTTP transaction mechanisms.
- `HTML::Parser` - For DOM traversal and line number extractions.
- `URI` - Resolves, standardizes, and translates absolute variables.
- `Getopt::Long` - CLI flags parsing.

You can install these dependencies using `cpanm` if they are not already installed on your system:
```bash
cpanm LWP::UserAgent URI HTML::Parser
```
