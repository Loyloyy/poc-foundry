"""Vendored Stage-2 research tools (search / fetch / GitHub / PyPI) for the S4 research-on-gaps rung.

Adapted from `ai-engineer-research` (Stage 2) — vendored with attribution, same stopgap discipline as
the vendored schema (DECISIONS #2); not contract-backed. PURE-ish: stdlib + LAZY `httpx`/`trafilatura`
so this module stays import-light + `py_compile`-able on the 3.10 box (the heavy deps live in the app
[runtime] extra, installed in Docker).

Posture (design §5.2):
  • SearXNG is the SHARED service-depot instance (env ``SEARX_URL``, on depot-net) — lateral traffic to
    trusted infra, NOT internet egress; it does not touch the per-build proxy/allowlist.
  • ``fetch`` gates result URLs against an APP-LEVEL ``research_hosts`` allowlist (gate + log, not
    proxy-enforced in M2c; the enforcing logging research-proxy is M4) and records provenance.
  • Fetched bytes are UNTRUSTED — ``scan_injection`` is a cheap deterministic TRIPWIRE (a detective
    control feeding security.incidents[], NOT immunity; defense-in-depth per rule #9).
Every tool is tolerated-absent: a missing dep / down service / blocked host returns an empty-ish
result, never raises.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

DEFAULT_TIMEOUT_S = 20

# Cheap deterministic injection markers (the tripwire). Lower-cased substring / regex match on fetched
# text. This is a smell test, not a classifier — the real wall is the downstream gates (§5.2).
_INJECTION_MARKERS = [
    r"ignore (all )?(previous|prior|above) (instructions|prompts)",
    r"disregard (the )?(previous|above|system)",
    r"\bsystem\s*:\s*you are",
    r"new instructions\s*:",
    r"you are now (a|an|in)\b",
    r"do not (tell|inform|mention to) the user",
    r"</?(system|assistant|tool_call)>",
    r"exfiltrat",
    r"reveal (your )?(system )?(prompt|instructions)",
]
_INJECTION_RE = re.compile("|".join(f"(?:{m})" for m in _INJECTION_MARKERS), re.IGNORECASE)


def scan_injection(text: str) -> list[str]:
    """Return the distinct injection-marker snippets found in ``text`` (empty = clean). Detective
    tripwire only — a clean result is NOT a safety guarantee."""
    if not text:
        return []
    hits = {m.group(0).strip().lower()[:60] for m in _INJECTION_RE.finditer(text)}
    return sorted(hits)


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _host_allowed(url: str, allow_hosts: list[str] | None) -> bool:
    """A host matches the allowlist if it equals or is a subdomain of an allowlisted host. An empty
    allowlist means 'no app-level gate' (the server-wide egress wall is the backstop)."""
    if not allow_hosts:
        return True
    h = host_of(url)
    return any(h == a or h.endswith("." + a) for a in allow_hosts)


def search(query: str, *, max_results: int = 5, searx_url: str | None = None,
           timeout: int = DEFAULT_TIMEOUT_S) -> list[dict]:
    """SearXNG JSON metasearch → ``[{title, url, content}]``. Tolerated-absent: no ``SEARX_URL`` /
    httpx / a down service → ``[]``."""
    base = (searx_url or os.environ.get("SEARX_URL", "")).strip().rstrip("/")
    if not base:
        return []
    try:
        import httpx  # lazy — heavy / not on the 3.10 box
        r = httpx.get(f"{base}/search", params={"q": query, "format": "json"},
                      timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        data = r.json()
    except Exception:  # noqa: BLE001 — tolerated-absent: search never crashes a build
        return []
    out = []
    for hit in (data.get("results") or [])[:max_results]:
        out.append({"title": (hit.get("title") or "").strip(),
                    "url": (hit.get("url") or "").strip(),
                    "content": (hit.get("content") or "").strip()})
    return out


def fetch(url: str, *, allow_hosts: list[str] | None = None, timeout: int = DEFAULT_TIMEOUT_S,
          max_chars: int = 6000) -> dict:
    """Fetch + extract readable text from one URL. Gates the host against ``allow_hosts`` (app-level),
    records provenance, and tripwire-scans the text. Returns
    ``{url, ok, blocked, text, injection, error}``. Never raises."""
    res = {"url": url, "ok": False, "blocked": False, "text": "", "injection": [], "error": ""}
    if not _host_allowed(url, allow_hosts):
        res["blocked"] = True
        res["error"] = f"host {host_of(url)!r} not in research_hosts allowlist"
        return res
    try:
        import httpx  # lazy
        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "poc-foundry-research/0.1"})
        r.raise_for_status()
        raw = r.text
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
        return res
    text = raw
    try:
        import trafilatura  # lazy
        extracted = trafilatura.extract(raw, include_comments=False, include_tables=False)
        if extracted:
            text = extracted
    except Exception:  # noqa: BLE001 — extraction is best-effort; fall back to raw
        pass
    text = text.strip()[:max_chars]
    res.update(ok=True, text=text, injection=scan_injection(text))
    return res


def pypi(package: str, *, timeout: int = DEFAULT_TIMEOUT_S) -> dict:
    """PyPI JSON metadata for a package (name/version/summary/home). Tolerated-absent → ``{}``."""
    name = re.sub(r"[^A-Za-z0-9._-]", "", package).strip()
    if not name:
        return {}
    try:
        import httpx  # lazy
        r = httpx.get(f"https://pypi.org/pypi/{name}/json", timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        info = (r.json().get("info") or {})
    except Exception:  # noqa: BLE001
        return {}
    return {"name": info.get("name", name), "version": info.get("version", ""),
            "summary": (info.get("summary") or "").strip(),
            "home_page": info.get("home_page") or info.get("project_url") or "",
            "url": f"https://pypi.org/project/{name}/"}


def github(query: str, *, max_results: int = 3, timeout: int = DEFAULT_TIMEOUT_S) -> list[dict]:
    """GitHub repository search (unauthenticated, low rate) → ``[{full_name, url, description}]``.
    Tolerated-absent → ``[]``."""
    try:
        import httpx  # lazy
        r = httpx.get("https://api.github.com/search/repositories",
                      params={"q": query, "per_page": max_results},
                      headers={"Accept": "application/vnd.github+json",
                               "User-Agent": "poc-foundry-research/0.1"},
                      timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        items = (r.json().get("items") or [])[:max_results]
    except Exception:  # noqa: BLE001
        return []
    return [{"full_name": it.get("full_name", ""), "url": it.get("html_url", ""),
             "description": (it.get("description") or "").strip()} for it in items]
