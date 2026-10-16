#!/usr/bin/env python3
"""LCARS bridge for Omnigent.

Serves the LCARS UI from ./app and proxies /api/* to the local
Omnigent server (127.0.0.1:6767), attaching the local-server
signature. SSE responses are streamed through.

Run:  python3 server.py   ->  http://127.0.0.1:6768
"""
import os
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

OG_BASE = os.environ.get("OMNIGENT_SERVER", "http://127.0.0.1:6767").rstrip("/")
PORT = int(os.environ.get("LCARS_PORT", "6768"))
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
SIG_PATH = os.path.expanduser("~/.omnigent/local_server.sig")

with open(SIG_PATH) as f:
    SIG = f.read().strip()


class BridgeHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def _forward(self, method):
        path = self.path
        if path == "/api":
            path = "/"
        elif path.startswith("/api/"):
            path = path[len("/api"):]
        url = OG_BASE + path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=body, method=method)
        for h in ("Content-Type", "Accept"):
            if self.headers.get(h):
                req.add_header(h, self.headers[h])
        req.add_header("X-Local-Server-Signature", SIG)
        try:
            resp = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            resp = e
        except Exception as e:
            self.send_error(502, f"omnigent unreachable: {e}")
            return

        code = getattr(resp, "status", None) or resp.code
        ctype = resp.headers.get("Content-Type", "application/octet-stream")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        if "text/event-stream" in ctype:
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    chunk = resp.read(1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                resp.close()
        else:
            data = resp.read()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            resp.close()

    def do_GET(self):
        if self.path == "/api" or self.path.startswith("/api/"):
            self._forward("GET")
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._forward("POST")
        else:
            self.send_error(405)

    def do_PUT(self):
        if self.path.startswith("/api/"):
            self._forward("PUT")
        else:
            self.send_error(405)

    def do_PATCH(self):
        if self.path.startswith("/api/"):
            self._forward("PATCH")
        else:
            self.send_error(405)

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._forward("DELETE")
        else:
            self.send_error(405)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    print(f"LCARS bridge on http://127.0.0.1:{PORT} -> {OG_BASE}")
    ThreadingHTTPServer(("127.0.0.1", PORT), BridgeHandler).serve_forever()
