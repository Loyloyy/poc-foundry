#!/usr/bin/env python3
"""Private tool server — a vetted SIBLING service for the tool-calling pilot. It serves a PRIVATE
product catalogue the model cannot know (opaque SKUs + prices) over HTTP, and LOGS every lookup so a
test can verify the tool was genuinely invoked with the right argument.

Stdlib only (``http.server``) → no dependencies, a tiny image, fully offline + deterministic. The
opaque prices/SKUs are the point: a reply can only contain them if the code ACTUALLY called the tool —
a model-only or echo stub cannot fake them.

Endpoints:
  GET /health                 → {"status":"ok"}                 (readiness)
  GET /price?product=<name>   → {"product","sku","price_usd","found"}; logs the call
  GET /calls                  → {"calls":[{"product":...}, ...]}  (invocation audit for tests)
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# PRIVATE catalogue — the product NAMES are public (the chatbot/tests may know them), but the SKUs and
# prices are OPAQUE values the model has no way to produce without calling this tool.
CATALOG = {
    "lattice router x1": {"sku": "LR-X1-7741", "price_usd": 1347.88},
    "vortex sensor pad": {"sku": "VSP-3920", "price_usd": 289.45},
    "halcyon power cell": {"sku": "HPC-5582", "price_usd": 64.19},
    "meridian edge node": {"sku": "MEN-1163", "price_usd": 4519.00},
}

_CALLS: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 (http.server API)
        u = urlparse(self.path)
        if u.path == "/health":
            return self._send(200, {"status": "ok"})
        if u.path == "/calls":
            return self._send(200, {"calls": _CALLS})
        if u.path == "/price":
            product = (parse_qs(u.query).get("product", [""])[0] or "").strip().lower()
            _CALLS.append({"product": product})
            rec = CATALOG.get(product)
            if rec:
                return self._send(200, {"product": product, "sku": rec["sku"],
                                        "price_usd": rec["price_usd"], "found": True})
            return self._send(200, {"product": product, "found": False})
        return self._send(404, {"error": "not found"})

    def log_message(self, *a):  # silence default stderr logging
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
