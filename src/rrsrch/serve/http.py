"""Tiny stdlib HTTP server: POST /v1/answer, GET /v1/health.

No Flask/FastAPI dependency. Bind to localhost by default — put a tunnel or
reverse proxy in front when the site needs it.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from ..ledger import db
from ..reader.llm import LLM
from . import answer as answer_mod


class Handler(BaseHTTPRequestHandler):
    server_version = "rrsrch/0.1"

    def log_message(self, fmt, *args):  # quieter than BaseHTTPRequestHandler
        print(f"[rrsrch] {self.address_string()} {fmt % args}")

    def _cors(self):
        origin = os.environ.get("RRSRCH_CORS_ORIGIN", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, body: dict):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self._cors()
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/v1/health", "/health", "/"):
            try:
                with db.connect() as conn:
                    n = conn.execute("SELECT count(*) FROM claims").fetchone()[0]
                self._json(200, {"ok": True, "claims": n, "model": os.environ.get("RRSRCH_READER_MODEL")})
            except Exception as e:  # noqa: BLE001
                self._json(503, {"ok": False, "error": str(e)[:200]})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if path not in ("/v1/answer", "/answer"):
            self._json(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "body must be JSON"})
            return
        question = " ".join(str(payload.get("question") or "").split())
        if not question:
            self._json(400, {"ok": False, "error": "question required"})
            return
        if len(question) > 800:
            self._json(400, {"ok": False, "error": "question too long"})
            return
        try:
            llm = LLM.from_env()
            with db.connect() as conn:
                db.migrate(conn)
                body = answer_mod.answer(conn, llm, question)
            self._json(200, body)
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": str(e)[:400]})


def run(host: str = "127.0.0.1", port: int = 8787) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"rrsrch engine on http://{host}:{port}  (POST /v1/answer, GET /v1/health)")
    httpd.serve_forever()
