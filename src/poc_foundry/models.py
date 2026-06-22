"""Role → chat-model factory (design §5.4). NO LiteLLM, NO model names in code (rules #2/#4).

Roles: architect / coder / tester / critic / scribe. Each reads a `.env` triple
(`<ROLE>_MODEL/_API_BASE/_API_KEY`); a blank triple falls back to `PF_DEFAULT_ROLE` (single on-prem
model runs everything — the Stage-2 role-triple-fallback pattern). Any role flips to a frontier
endpoint by editing its triple.

`build_chat_model` lazily imports `langchain_openai` so importing this module stays light on the
3.10 dev box; `chat_text` is a stdlib-only raw call for the coder's tight loop (no langchain in the
inner loop). Secrets live only in this client code, never in agent context (Finding-0).
"""
from __future__ import annotations

import json as _json
import os
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    from poc_foundry.config import _load_dotenv as _ld
    _ld(_REPO_ROOT / ".env")


# sampling defaults keyed on the ORIGINAL role (task shapes temperature; endpoint may be shared)
_ROLE_TEMP = {"architect": 0.2, "coder": 0.1, "tester": 0.1, "critic": 0.0, "scribe": 0.3}


def resolve_role(role: str) -> tuple[str, str, str]:
    """(model, api_base, api_key) for a role; blank triple → PF_DEFAULT_ROLE fallback."""
    _load_dotenv()
    r = role.upper()
    model = os.environ.get(f"{r}_MODEL", "").strip()
    if not model or model.startswith("<"):
        r = os.environ.get("PF_DEFAULT_ROLE", "architect").strip().upper()
        model = os.environ.get(f"{r}_MODEL", "").strip()
    base = os.environ.get(f"{r}_API_BASE", "").strip()
    key = os.environ.get(f"{r}_API_KEY", "").strip() or "not-needed"
    if not model or not base or model.startswith("<") or base.startswith("<"):
        raise RuntimeError(f"role '{role}' not configured (set {r}_MODEL/_API_BASE in .env)")
    return model, base, key


def build_chat_model(role: str, *, temperature: float | None = None, max_tokens: int = 4000,
                     timeout: int | None = None):
    """A LangChain `ChatOpenAI` bound to the role's endpoint (lazy import). For structured calls
    (architect/critic/scribe). Use `.with_structured_output(Model)` for typed extraction."""
    from langchain_openai import ChatOpenAI  # lazy — heavy

    model, base, key = resolve_role(role)
    temp = _ROLE_TEMP.get(role.lower(), 0.2) if temperature is None else temperature
    to = timeout if timeout is not None else int(os.environ.get("PF_LLM_TIMEOUT_S", "300"))
    return ChatOpenAI(model=model, base_url=base, api_key=key, temperature=temp,
                      max_tokens=max_tokens, timeout=to)


def chat_text(role: str, prompt: str, system: str | None = None, *,
              max_tokens: int = 4000, temperature: float | None = None, timeout: int = 300) -> str:
    """Stdlib raw chat completion (no langchain) — for the coder's bounded loop. Returns the
    assistant text (falls back to a `reasoning` field if `content` is empty)."""
    model, base, key = resolve_role(role)
    temp = _ROLE_TEMP.get(role.lower(), 0.1) if temperature is None else temperature
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temp}
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=_json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = _json.loads(r.read())
    msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
    return msg.get("content") or msg.get("reasoning") or ""
