import http.client
import hmac
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

TOKEN = os.environ["WATCHSKILL_MCP_TOKEN"]
UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 8747
MAX_REQUEST_BYTES = 16 * 1024 * 1024
HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if urlsplit(self.path).path == "/healthz":
            try:
                with socket.create_connection((UPSTREAM_HOST, UPSTREAM_PORT), timeout=1):
                    self.send_response(200)
            except OSError:
                self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._proxy()

    def do_POST(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()

    def _proxy(self):
        supplied = self.headers.get("Authorization", "")
        expected = "Bearer " + TOKEN
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            body = json.dumps({"error": "unauthorized"}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("WWW-Authenticate", 'Bearer realm="watch-skill-mcp"')
            self.end_headers()
            self.wfile.write(body)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return
        if length < 0 or length > MAX_REQUEST_BYTES:
            self.send_error(413, "MCP request body exceeds the 16 MiB limit")
            return
        body = self.rfile.read(length) if length else None
        headers = {
            key: value for key, value in self.headers.items()
            if key.lower() not in HOP_HEADERS
            and key.lower() not in {"host", "content-length", "authorization"}
        }
        headers["Host"] = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"
        if body is not None:
            headers["Content-Length"] = str(len(body))

        conn = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=3600)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            upstream = conn.getresponse()
            self.send_response(upstream.status, upstream.reason)
            has_length = False
            for key, value in upstream.getheaders():
                lower = key.lower()
                if lower in HOP_HEADERS:
                    continue
                if lower == "content-length":
                    has_length = True
                self.send_header(key, value)
            if not has_length and upstream.status not in (204, 304) and not 100 <= upstream.status < 200:
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if self.command != "HEAD" and upstream.status not in (204, 304) and not 100 <= upstream.status < 200:
                while True:
                    chunk = upstream.read(65536)
                    if not chunk:
                        break
                    if has_length:
                        self.wfile.write(chunk)
                    else:
                        self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                if not has_length:
                    self.wfile.write(b"0\r\n\r\n")
        except (OSError, http.client.HTTPException):
            self.close_connection = True
            if not self.wfile.closed:
                try:
                    self.send_error(502, "Watch Skill MCP upstream unavailable")
                except OSError:
                    pass
        finally:
            conn.close()

    def log_message(self, fmt, *args):
        print(f"watch-skill-auth-proxy {self.command} {urlsplit(self.path).path}", flush=True)


ThreadingHTTPServer(("0.0.0.0", 8748), Handler).serve_forever()
