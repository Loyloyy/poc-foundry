"""Direct OpenAI-compatible chat (stdlib urllib) for the bespoke coder, role-driven from .env.

Mirrors the design's `build_chat_model(role)` contract WITHOUT langchain (the spike is stdlib-only):
resolves `<ROLE>_MODEL/_API_BASE/_API_KEY` with a `PF_DEFAULT_ROLE` fallback (single on-prem model
runs everything — the Stage-2 role-triple-fallback pattern). No model name in code (rule #4).
"""
from __future__ import annotations

import json as _json
import os
import urllib.request
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (no dependency); does not override already-set process env."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if " #" in val:
            val = val.split(" #", 1)[0]
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _resolve(role: str) -> tuple[str, str, str]:
    """(model, api_base, api_key) for a role, falling back to PF_DEFAULT_ROLE when the triple is blank."""
    role_u = role.upper()
    model = os.environ.get(f"{role_u}_MODEL", "").strip()
    if not model:
        fallback = os.environ.get("PF_DEFAULT_ROLE", "architect").strip().upper()
        role_u = fallback
        model = os.environ.get(f"{role_u}_MODEL", "").strip()
    base = os.environ.get(f"{role_u}_API_BASE", "").strip()
    key = os.environ.get(f"{role_u}_API_KEY", "").strip() or "x"
    if not model or not base or model.startswith("<") or base.startswith("<"):
        raise SystemExit(f"[model_client] role '{role}' not configured (set {role_u}_MODEL/_API_BASE in .env)")
    return model, base, key


def chat(role: str, prompt: str, system: str | None = None,
         max_tokens: int = 4000, temperature: float = 0.1, timeout: int = 300) -> str:
    model, base, key = _resolve(role)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=_json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = _json.loads(r.read())
    msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
    return msg.get("content") or msg.get("reasoning") or ""
