# Testing LinkChecker-PD

To ensure `linkchecker.pl` functions smoothly across edge cases, testing it locally using a temporary HTTP server is recommended. This isolates the logic testing from general internet flakiness and allows specific configurations.

## 1. Automated Unit Testing

### Setting Up a Test Directory structure

First, populate an HTML document pointing to a variety of distinct situations (i.e., valid internal links, broken targets, SSL breaking URLs, and redirect loops).

Create a test folder configuration like the script below:
```bash
mkdir -p test_site
cat << 'EOF' > test_site/index.html
<html><body>
<a href="page2.html">Page 2</a>
<a href="broken.html">Broken</a>
<a href="https://example.invalid">External Broken</a>
<a href="page2.html#section-1">Good Anchor</a>
<a href="page2.html#bad-anchor">Bad Anchor</a>
</body></html>
EOF

cat << 'EOF' > test_site/page2.html
<html><body>
<h1 id="section-1">Section 1</h1>
<a href="index.html">Back</a>
</body></html>
EOF
```

### Running the Python Mock Server

Once the directory acts as your website's root, serve the files over HTTP using Python:
```bash
cd test_site
python3 -m http.server 8888
```

### Executing the Validation Command

In a secondary terminal tab run the standard output process testing against your locally operating python network:
```bash
./linkchecker.pl http://localhost:8888/
```

You should see output similar to the following:
```
Starting crawl of http://localhost:8888/
[http://localhost:8888/:3] -> http://localhost:8888/broken.html ... 404 File not found
[http://localhost:8888/:4] -> https://example.invalid/ ... DNS resolution failed
[http://localhost:8888/:6] -> http://localhost:8888/page2.html#bad-anchor ... Anchor #bad-anchor not found
```

## 2. Advanced Redirect Chain Tracking

To safely validate the way the Perl script breaks down redirection hops and formats recursive `[REDIRECT_URL:NA]` variables without falsely marking active source pages as completely broken: Python's `HTTPServer` structure can be modeled to force redirects. 

Save `redirect_server.py` with the following HTTP hooks:
```python
from http.server import BaseHTTPRequestHandler, HTTPServer

class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<a href='/r1'>Redirect 1</a>")
        elif self.path == "/r1":
            self.send_response(301)
            self.send_header("Location", "/r2")
            self.end_headers()
        elif self.path == "/r2":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.end_headers()
        elif self.path == "/final":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"Success Target")
        else:
            self.send_response(404)
            self.end_headers()
            
    def do_HEAD(self):
        self.do_GET()

HTTPServer(("", 8889), RequestHandler).serve_forever()
```

Run this server locally via `python3 redirect_server.py` and target the spider:

```bash
./linkchecker.pl http://localhost:8889/
```

The output seamlessly validates intermediate redirect tracking logic:
```
[http://localhost:8889/:1] -> http://localhost:8889/r1 ... 301 Moved Permanently -> http://localhost:8889/r2
[http://localhost:8889/r1:NA] -> http://localhost:8889/r2 ... 302 Found -> http://localhost:8889/final
[http://localhost:8889/r2:NA] -> http://localhost:8889/final ... 200 OK
```
