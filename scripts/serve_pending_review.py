#!/usr/bin/env python3
"""Expose one pending Xunlei review object to its official page, once.

The random localhost URL is printed; the review data itself is never logged.
Run only while the user is waiting at xunlei-cli's verification prompt.
"""

import argparse
import json
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ORIGIN = "https://i.xunlei.com"


def serve(path: Path) -> None:
    if not path.is_file():
        raise SystemExit("Pending review data is absent")
    if path.stat().st_mode & 0o077:
        raise SystemExit("Pending review data is not private (expected mode 0600)")
    nonce = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def _headers(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Access-Control-Allow-Origin", ORIGIN)
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Vary", "Origin")
            self.end_headers()

        def do_OPTIONS(self) -> None:
            if self.headers.get("Origin") != ORIGIN or self.path != f"/{nonce}":
                self.send_error(403)
                return
            self._headers(204)

        def do_GET(self) -> None:
            if self.headers.get("Origin") != ORIGIN or self.path != f"/{nonce}":
                self.send_error(403)
                return
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not all(data.get(key) for key in ("reviewurl", "creditkey", "deviceid")):
                    raise ValueError("Incomplete pending review data")
            except (OSError, ValueError, json.JSONDecodeError):
                self.send_error(500)
                return
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._headers(200)
            self.wfile.write(body)
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(f"http://127.0.0.1:{server.server_port}/{nonce}", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    serve(args.file)
