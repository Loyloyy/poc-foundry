"""BuildConfig — non-secret knobs (config/pipeline.yaml) + host/secret knobs (env, gitignored .env).

Env wins over pipeline.yaml (design §5.8). Stdlib + optional pyyaml (tolerated-absent) so this imports
on the 3.10 dev box. No model names/hosts hardcoded (rule #1/#4) — those come from env.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PIPELINE_YAML = _REPO_ROOT / "config" / "pipeline.yaml"


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


def _yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        import yaml  # tolerated-absent
    except ImportError:
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _int_env(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    return int(v) if v.isdigit() else default


@dataclass
class BuildConfig:
    # paths / images / endpoints (host-specific → env)
    workspace_dir: Path           # PF_WORKSPACE_DIR — LOCAL disk; sandbox workspaces + uv cache
    builds_dir: Path              # where builds/<id>/ land
    sandbox_image: str
    proxy_image: str
    vllm_allow_host: str          # PF_VLLM_ALLOW_HOST — proxy's single private-host exception
    default_role: str             # PF_DEFAULT_ROLE — blank role triples fall back here
    llm_timeout_s: int

    # budgets / caps (pipeline.yaml, env-overridable)
    max_fix_attempts: int
    stuck_research_after: int
    max_iterations: int
    toolcalls_per_attempt: int

    # templates
    default_template: str

    @property
    def kata_runtime(self) -> str:
        return os.environ.get("PF_SANDBOX_RUNTIME", "kata")


def load_config(builds_dir: Path | str | None = None) -> BuildConfig:
    _load_dotenv(_REPO_ROOT / ".env")
    y = _yaml(_PIPELINE_YAML)
    budgets = y.get("budgets", {})
    templates = y.get("templates", {})

    ws = os.environ.get("PF_WORKSPACE_DIR", "").strip() or "/var/tmp/pf-workspaces"
    return BuildConfig(
        workspace_dir=Path(ws),
        builds_dir=Path(builds_dir) if builds_dir else _REPO_ROOT / "builds",
        sandbox_image=os.environ.get("PF_SANDBOX_IMAGE", "poc-foundry-sandbox"),
        proxy_image=os.environ.get("PF_PROXY_IMAGE", "poc-foundry-proxy"),
        vllm_allow_host=os.environ.get("PF_VLLM_ALLOW_HOST", "").strip(),
        default_role=os.environ.get("PF_DEFAULT_ROLE", "architect").strip() or "architect",
        llm_timeout_s=_int_env("PF_LLM_TIMEOUT_S", 300),
        max_fix_attempts=_int_env("PF_MAX_FIX_ATTEMPTS", int(budgets.get("max_fix_attempts", 3))),
        stuck_research_after=_int_env("PF_STUCK_RESEARCH_AFTER", int(budgets.get("stuck_research_after", 2))),
        max_iterations=_int_env("PF_MAX_ITERATIONS", int(budgets.get("max_iterations", 8))),
        toolcalls_per_attempt=_int_env("PF_TOOLCALLS_PER_ATTEMPT", int(budgets.get("toolcalls_per_attempt", 25))),
        default_template=str(templates.get("default", "gradio-chatbot")),
    )
