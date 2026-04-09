from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
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
            self.wfile.write(b"Success")
        else:
            self.send_response(404)
            self.end_headers()
    def do_HEAD(self):
        self.do_GET()
HTTPServer(("", 8889), H).serve_forever()
