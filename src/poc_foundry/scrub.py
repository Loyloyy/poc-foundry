"""scrub.py — emitted-output hygiene scrubber (AGENTS rule #1; design §1.2 / §6 M2b).

PURE module (stdlib only → ``py_compile``-able on the 3.10 dev box). Given the run's sensitive
values — the vLLM host/IP, the served-model id, the API key, and the NFS / model / workspace paths,
read at EMIT time from the process env (``.env`` is already loaded by ``config``) and the gitignored
``build_env.json`` sidecar, **NEVER hardcoded** — it rewrites them to canonical placeholders in
emitted TEXT (``report.md``, ``00_INDEX.md``, ``PROGRESS.md``, the ``PoCBuildArtifact`` JSON,
``logs/*.log``, and the forensic crash traceback) so a shared build BUNDLE leaks no endpoint / id /
path. ``builds/`` is gitignored (not a public-repo risk) but a bundle handed to a human must be
clean — this closes the last open rule-#1 item.

The scrubber is CONSERVATIVE: it only rewrites the literal values it can SEE in the run's config (so
it never mangles unrelated prose), and it applies the longest value first (so a ``host:port`` form
wins over its bare-``host`` substring). Placeholders match the tracked-file convention
(``<vllm-host:port>`` / ``<served-model-id>`` / ``/path/...``).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# canonical placeholders (mirror the tracked-file convention, rule #1)
ENDPOINT = "<vllm-host:port>"
HOST = "<vllm-host>"
MODEL = "<served-model-id>"
KEY = "<redacted-key>"
PATH = "/path/..."
SERVICE = "<service-host>"

# emitted text files scrubbed in a build folder (logs/*.json|*.log + v*.json added dynamically)
_TEXT_FILES = ("report.md", "00_INDEX.md", "PROGRESS.md")

# never treat these as secrets (generic / public / sentinel values)
_SKIP = {"not-needed", "localhost", "127.0.0.1", "0.0.0.0", "postgres", "true", "false"}
_MIN_LEN = 4   # skip trivially short tokens (false-positive substring matches)


def _authority(url: str) -> str:
    """``http://10.0.0.5:8000/v1`` → ``10.0.0.5:8000`` (scheme + path stripped)."""
    s = url.strip()
    for scheme in ("http://", "https://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    return s.split("/", 1)[0]


def _bare_host(authority: str) -> str:
    """``10.0.0.5:8000`` → ``10.0.0.5`` (strip the port)."""
    return authority.split(":", 1)[0]


def _classify(key: str, value: str, add) -> None:
    """Map ONE ``KEY=value`` pair to its placeholder(s) via the key's suffix (case-insensitive). Used
    for both the process env and the flattened ``build_env.json`` leaves."""
    ku = key.upper()
    if ku.endswith("_MODEL") or ku == "MODEL":
        add(value, MODEL)
        if value.strip().startswith("/"):     # a served model id is sometimes an on-disk path
            add(value, PATH)
    elif ku.endswith("_API_BASE") or ku.endswith("_BASE_URL") or ku.endswith("_ENDPOINT") or ku == "API_BASE":
        auth = _authority(value)
        add(value, ENDPOINT)                    # the full URL as configured
        add(auth, ENDPOINT)                     # host:port
        add(_bare_host(auth), HOST)             # bare host / IP
    elif (ku.endswith("_API_KEY") or ku.endswith("_KEY") or ku.endswith("_TOKEN")
          or ku.endswith("_PASSWORD") or ku == "API_KEY"):
        add(value, KEY)
    elif ku.endswith("_ALLOW_HOST"):            # PF_VLLM_ALLOW_HOST — the single private vLLM exception
        add(value, ENDPOINT)
        add(_bare_host(value), HOST)
    elif ku.endswith("_HOST"):                  # sibling-service IPs (PF_SERVICE_<NAME>_HOST)
        add(value, SERVICE)
    elif "PATH" in ku or ku.endswith("_DIR") or ku.endswith("_ROOT") or "NFS" in ku or ku.endswith("_SOCKET"):
        add(value, PATH)


def _flatten(obj, out: dict, prefix: str = "") -> None:
    """Flatten a nested ``build_env.json`` into ``{KEY: "value"}`` leaves (scalars only)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(v, out, f"{prefix}_{k}" if prefix else str(k))
    elif isinstance(obj, (str, int, float)):
        out[prefix] = str(obj)


def collect_secrets(env: dict | None = None, build_env_path: str | Path | None = None) -> list[tuple[str, str]]:
    """The ordered (value, placeholder) substitution list for THIS run. Reads the process env (``.env``
    is already loaded by ``config``) plus the gitignored ``build_env.json`` sidecar (the design's
    real role→model-id map). Deduped and sorted longest-value-first so a ``host:port`` wins over its
    bare-``host`` substring. Returns ``[]`` when nothing sensitive is configured (a no-op scrub)."""
    if env is None:
        env = dict(os.environ)
    pairs: list[tuple[str, str]] = []

    def add(value, placeholder) -> None:
        v = (value or "").strip().strip('"').strip("'")
        if not v or v.startswith("<") or len(v) < _MIN_LEN or v.lower() in _SKIP:
            return
        pairs.append((v, placeholder))

    for k, v in env.items():
        _classify(str(k), str(v), add)

    if build_env_path is None:
        cand = _REPO_ROOT / "build_env.json"
        build_env_path = cand if cand.is_file() else None
    if build_env_path and Path(build_env_path).is_file():
        try:
            leaves: dict = {}
            _flatten(json.loads(Path(build_env_path).read_text()), leaves)
            for k, v in leaves.items():
                _classify(k, v, add)
        except Exception:  # noqa: BLE001 — a malformed sidecar must never crash the emit
            pass

    # dedupe (first placeholder wins per value), then longest value first
    seen: dict[str, str] = {}
    for value, placeholder in pairs:
        seen.setdefault(value, placeholder)
    return sorted(seen.items(), key=lambda kv: len(kv[0]), reverse=True)


def scrub_text(text: str, secrets: list[tuple[str, str]]) -> str:
    """Replace every configured sensitive value with its placeholder. ``secrets`` must already be
    longest-first (``collect_secrets`` guarantees this)."""
    for value, placeholder in secrets:
        if value in text:
            text = text.replace(value, placeholder)
    return text


def scrub_file(path: str | Path, secrets: list[tuple[str, str]]) -> bool:
    """Scrub one text file in place. Returns True if it changed. Binary / unreadable files are left
    untouched (never crashes the emit)."""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        orig = p.read_text()
    except (UnicodeDecodeError, OSError):
        return False
    new = scrub_text(orig, secrets)
    if new != orig:
        p.write_text(new)
        return True
    return False


def scrub_build_dir(build_dir: str | Path, secrets: list[tuple[str, str]] | None = None) -> list[str]:
    """Scrub every emitted TEXT file under ``build_dir`` (report/index/progress, the ``v*.json``
    artifact, and ``logs/*.log``). Replacing only secret substrings with quote-free placeholders keeps
    the artifact JSON valid. Returns the list of files actually changed. Best-effort: a no-secrets run
    or a missing folder is a clean no-op."""
    bd = Path(build_dir)
    if secrets is None:
        secrets = collect_secrets()
    if not secrets or not bd.is_dir():
        return []
    targets: list[Path] = [bd / n for n in _TEXT_FILES]
    targets += sorted(bd.glob("v*.json"))
    targets += sorted((bd / "logs").glob("*.log"))
    targets += sorted(bd.glob("iterations/*/*.md"))   # M2c: per-iteration lessons.md / research.md
    changed: list[str] = []
    for t in targets:
        if scrub_file(t, secrets):
            changed.append(str(t))
    return changed
