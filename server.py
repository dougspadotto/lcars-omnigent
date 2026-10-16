#!/usr/bin/env python3
"""LCARS bridge for Omnigent.

Serves the LCARS UI from ./app and proxies /api/* to the local
Omnigent server (127.0.0.1:6767), attaching the local-server
signature. SSE responses are streamed through.

Run:  python3 server.py   ->  http://127.0.0.1:6768
"""
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

OG_BASE = os.environ.get("OMNIGENT_SERVER", "http://127.0.0.1:6767").rstrip("/")
OG_HOST = "127.0.0.1"
OG_PORT = 6767
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
PORT = int(os.environ.get("LCARS_PORT", "6768"))
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
SIG_PATH = os.path.expanduser("~/.omnigent/local_server.sig")

with open(SIG_PATH) as f:
    SIG = f.read().strip()

# Read-only terminal viewers must render at the pane's own geometry (the
# runner attaches with ignore-size, so the viewer cannot reflow it). The
# bridge metadata carries the tmux socket + target; this endpoint maps them
# to cols/rows via tmux. Only Omnigent's own terminal sockets are accepted.
TMUX_SOCKET_RE = re.compile(r"/omnigent-terminal-[A-Za-z0-9_]+/tmux\.sock$")


def tmux_geometry(socket_path, target):
    if not TMUX_SOCKET_RE.search(socket_path) or not os.path.exists(socket_path):
        return None
    tmux = shutil.which("tmux")
    if not tmux:
        return None
    try:
        out = subprocess.run(
            [tmux, "-S", socket_path, "display-message", "-p", "-t", target,
             "#{pane_width} #{pane_height}"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        cols, _, rows = out.stdout.strip().partition(" ")
        return {"cols": int(cols), "rows": int(rows)}
    except Exception:
        return None


# Answering an in-pane prompt from the browser relays keystrokes: hex bytes
# per tmux send-keys -H (one byte per token), capped, Omnigent sockets only.
HEX_RE = re.compile(r"^(?:[0-9a-fA-F]{2})(?:\s+[0-9a-fA-F]{2}){0,95}$")


def tmux_send(socket_path, target, hex_str):
    if not TMUX_SOCKET_RE.search(socket_path) or not os.path.exists(socket_path):
        return False
    tmux = shutil.which("tmux")
    if not tmux or not target or not HEX_RE.match(hex_str or ""):
        return False
    try:
        out = subprocess.run(
            [tmux, "-S", socket_path, "send-keys", "-t", target, "-H", *hex_str.split()],
            capture_output=True, text=True, timeout=5,
        )
        return out.returncode == 0
    except Exception:
        return False


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

    def _tunnel_ws(self):
        """Raw WebSocket pass-through to the Omnigent server.

        Terminates the downstream handshake ourselves, then pumps frames
        verbatim in both directions. Frames are never parsed, so client
        masking is preserved transparently.
        """
        path = self.path
        if path.startswith("/api/"):
            path = path[len("/api"):]
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400, "missing Sec-WebSocket-Key")
            return
        try:
            up = socket.create_connection((OG_HOST, OG_PORT), timeout=10)
        except Exception as e:
            self.send_error(502, f"omnigent unreachable: {e}")
            return
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {OG_HOST}:{OG_PORT}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"X-Local-Server-Signature: {SIG}\r\n"
            "X-Forwarded-Email: douglas.spadotto@databricks.com\r\n\r\n"
        )
        up.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = up.recv(4096)
            if not chunk:
                break
            resp += chunk
        head, _, rest = resp.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n")[0]
        if b" 101 " not in status_line:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(head + rest)
            up.close()
            return
        accept = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode()).digest()
        ).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        down = self.connection
        if rest:
            down.sendall(rest)

        def pump(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:
                pass
            finally:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except Exception:
                    pass

        t1 = threading.Thread(target=pump, args=(down, up), daemon=True)
        t2 = threading.Thread(target=pump, args=(up, down), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        up.close()

    def do_GET(self):
        if self.path.startswith("/api/tmux-geometry?"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            geo = tmux_geometry(
                (q.get("socket") or [""])[0],
                (q.get("target") or [""])[0],
            )
            body = json.dumps(geo).encode() if geo else b"{}"
            self.send_response(200 if geo else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api" or self.path.startswith("/api/"):
            if (self.headers.get("Upgrade") or "").lower() == "websocket":
                self._tunnel_ws()
                return
            self._forward("GET")
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/tmux-send":
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                body = {}
            ok = tmux_send(
                str(body.get("socket") or ""),
                str(body.get("target") or ""),
                str(body.get("hex") or ""),
            )
            resp = b'{"sent":true}' if ok else b'{"sent":false}'
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        elif self.path.startswith("/api/"):
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
