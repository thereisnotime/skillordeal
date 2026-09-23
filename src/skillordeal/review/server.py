"""A tiny stdlib HTTP server for labeling findings. Binds to 127.0.0.1 only.

Routes (all /api/ routes need the session token in the X-Review-Token header):
  GET  /                              the review page
  GET  /api/meta                      trial, round, labeler, arenas, ground-truth issues
  GET  /api/findings                  findings joined with gt, judge and labels
  GET  /api/excerpt?finding_id=ID     code around the cited lines
  POST /api/labels                    {finding_id, verdict, issue_id?, note?}
"""

from __future__ import annotations

import hmac
import json
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import parse_qs, urlsplit

from skillordeal.review.data import ExcerptError, ReviewState

HOST = "127.0.0.1"
MAX_BODY = 64 * 1024


def new_token() -> str:
    return secrets.token_urlsafe(24)


def page_html() -> bytes:
    return files("skillordeal.review").joinpath("page.html").read_bytes()


def make_handler(state: ReviewState) -> type[BaseHTTPRequestHandler]:
    page = page_html()

    class Handler(BaseHTTPRequestHandler):
        server_version = "skillordeal-review"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:  # quiet by default
            pass

        # --- helpers -----------------------------------------------------------------

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; "
                "frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

        def _authorized(self) -> bool:
            got = self.headers.get("X-Review-Token", "")
            return bool(state.token) and hmac.compare_digest(got, state.token)

        # --- verbs -------------------------------------------------------------------

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            if url.path in ("/", "/index.html"):
                self._send(200, page, "text/html; charset=utf-8")
                return
            if not url.path.startswith("/api/"):
                self._json(404, {"error": "not found"})
                return
            if not self._authorized():
                self._json(403, {"error": "missing or wrong token"})
                return
            q = parse_qs(url.query)
            try:
                if url.path == "/api/meta":
                    self._json(200, state.meta())
                elif url.path == "/api/findings":
                    self._json(200, state.findings_payload())
                elif url.path == "/api/excerpt":
                    self._json(200, state.excerpt((q.get("finding_id") or [""])[0]))
                else:
                    self._json(404, {"error": "not found"})
            except KeyError as e:
                self._json(404, {"error": str(e.args[0] if e.args else e)})
            except ExcerptError as e:
                self._json(400, {"error": str(e)})
            except Exception as e:  # keep the server up; surface the reason in the UI
                self._json(500, {"error": f"{type(e).__name__}: {e}"})

        def do_POST(self) -> None:
            url = urlsplit(self.path)
            if url.path != "/api/labels":
                self._json(404, {"error": "not found"})
                return
            if not self._authorized():
                self._json(403, {"error": "missing or wrong token"})
                return
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self._json(415, {"error": "send application/json"})
                return
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                self._json(413, {"error": "bad body size"})
                return
            try:
                body = json.loads(self.rfile.read(n))
                if not isinstance(body, dict):
                    raise ValueError("body must be a JSON object")
                label = state.add_label(body)
            except KeyError as e:
                self._json(404, {"error": str(e.args[0] if e.args else e)})
                return
            except (ValueError, json.JSONDecodeError) as e:
                self._json(400, {"error": str(e)})
                return
            self._json(HTTPStatus.CREATED, label)

    return Handler


def make_server(state: ReviewState, port: int = 8765) -> ThreadingHTTPServer:
    if not state.token:
        state.token = new_token()
    srv = ThreadingHTTPServer((HOST, port), make_handler(state))
    srv.daemon_threads = True
    return srv
