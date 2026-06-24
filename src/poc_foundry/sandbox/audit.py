"""Append-only broker audit log (M4 S2, design §5.2 — the daemon-side rejection record; DECISIONS #22
follow-up). The broker daemon is the trust boundary (rule #8 enforcer) and the ONLY holder of
docker.sock; this records every rejected ``create*`` (+ provision/destroy lifecycle) **append-only** to
a file the daemon owns, so the record is durable and readable INDEPENDENT of the (possibly compromised)
orchestrator. The orchestrator merely *reads* it (via the ``audit`` RPC) to surface
``security.incidents[]`` — the file is the authoritative copy.

Stdlib-only (json + os) so it ``py_compile``s on the 3.10 box and the fakes exercise it without Docker.
NEVER record a secret: entries carry only harness/LLM-derived create-params (image/name/caps/targets)
and the invariant reason — no env values, no vLLM key (Finding-0)."""
from __future__ import annotations

import json
import os
import time


def append(path: str, entry: dict) -> None:
    """Append one JSON entry as a line (atomic-ish: one ``write`` of a newline-terminated record).
    Best-effort — an audit-write failure must never crash a build (the in-memory copy still holds it)."""
    if not path:
        return
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read(path: str, *, build_id: str | None = None) -> list[dict]:
    """Read the append-only log back (optionally filtered to one ``build_id``). Tolerates partial /
    malformed trailing lines (a crash mid-append never breaks the reader)."""
    if not path or not os.path.isfile(path):
        return []
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if build_id is None or e.get("build_id") == build_id:
                    out.append(e)
    except OSError:
        return out
    return out


def make_entry(build_id: str, event: str, method: str, *, reason: str = "", detail: dict | None = None) -> dict:
    """A serializable audit record. ``event`` ∈ {rejected, provision, destroy}; ``detail`` is the
    harness/LLM-derived create-params ONLY (image/name/caps/targets) — never env/secrets."""
    return {"ts": round(time.time(), 3), "build_id": build_id, "event": event,
            "method": method, "reason": reason[:500], "detail": detail or {}}
