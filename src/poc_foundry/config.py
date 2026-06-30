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
    # M4 S2b key-proxy (design §5.2; DECISIONS #31) — OPT-IN. When `keyproxy_upstream` is set the broker
    # provisions a per-build key-proxy (the REAL model key stays orchestrator/daemon-side; the VM gets only
    # a per-build sacrificial token + the proxy's base_url). UNSET → normal builds are byte-for-byte
    # unchanged. The image is HARNESS-FIXED (rule #8) and added to the broker allowlist when enabled.
    keyproxy_image: str           # PF_KEYPROXY_IMAGE — default the app image (has the keyproxy module)
    keyproxy_upstream: str        # PF_KEYPROXY_UPSTREAM — the real model server ROOT (no /v1); "" = OFF
    default_role: str             # PF_DEFAULT_ROLE — blank role triples fall back here
    llm_timeout_s: int
    uv_cache_shared: bool         # PF_UV_CACHE_SHARED — reuse ONE uv-cache volume across builds
    #                               (TRUSTED-INPUT dev loop only; default OFF = isolated per-build)

    # budgets / caps (pipeline.yaml, env-overridable)
    max_fix_attempts: int
    stuck_research_after: int
    max_iterations: int
    toolcalls_per_attempt: int
    # LLM-call + wall-clock caps (M2b S2; primary = call counts, deterministic under load) — 0 disables
    max_llm_calls_per_iter: int
    max_llm_calls_per_run: int
    max_iter_wall_clock_s: int
    max_run_wall_clock_s: int
    max_research_results: int     # M2c S4: research-on-gaps fetch breadth per run
    recursion_limit: int          # M6: LangGraph node-visit ceiling — salvaged (not crashed) when hit

    # critic gate (pipeline.yaml `critic:`, env-overridable) — design §5.4/§5.8
    fix_limit_k: int
    respec_cap: int
    degraded_fix_limit_k: int
    replan_cap: int
    reauthor_cap: int   # M6: per-iteration buggy-test re-authors (stuck-after-research rung)

    # research-on-gaps (M2c S4) — app-level fetch allowlist (advisory; NOT the build-VM allowlist)
    research_hosts: list

    # templates
    default_template: str

    # vetted sibling services (pipeline.yaml `vetted_services`) — name → {image, pinned_tag, role,
    # ready_cmd, env}. The image+tag are HARNESS-FIXED here (rule #8): a template names which vetted
    # service it wants; the broker resolves image/tag from THIS list, never from artifact/model output.
    vetted_services: dict

    @property
    def kata_runtime(self) -> str:
        return os.environ.get("PF_SANDBOX_RUNTIME", "kata")

    @property
    def keyproxy_enabled(self) -> bool:
        """The key-proxy is provisioned only when an upstream model endpoint is configured (opt-in)."""
        return bool(self.keyproxy_upstream)

    def service_refs(self) -> set[str]:
        """`image:pinned_tag` for every fully-pinned vetted service — added to the broker allowlist."""
        out = set()
        for s in self.vetted_services.values():
            tag = str(s.get("pinned_tag", "") or "")
            if s.get("image") and tag and not tag.startswith("<"):
                out.add(f"{s['image']}:{tag}")
        return out


def load_config(builds_dir: Path | str | None = None) -> BuildConfig:
    _load_dotenv(_REPO_ROOT / ".env")
    y = _yaml(_PIPELINE_YAML)
    budgets = y.get("budgets", {})
    templates = y.get("templates", {})
    critic = y.get("critic", {})
    vetted = {s["name"]: s for s in (y.get("vetted_services", []) or []) if s.get("name")}
    research_hosts = list((y.get("egress_allowlist", {}) or {}).get("research_hosts", []) or [])

    ws = os.environ.get("PF_WORKSPACE_DIR", "").strip() or "/var/tmp/pf-workspaces"
    return BuildConfig(
        workspace_dir=Path(ws),
        builds_dir=Path(builds_dir) if builds_dir else _REPO_ROOT / "builds",
        sandbox_image=os.environ.get("PF_SANDBOX_IMAGE", "poc-foundry-sandbox"),
        proxy_image=os.environ.get("PF_PROXY_IMAGE", "poc-foundry-proxy"),
        vllm_allow_host=os.environ.get("PF_VLLM_ALLOW_HOST", "").strip(),
        keyproxy_image=os.environ.get("PF_KEYPROXY_IMAGE", "poc-foundry-app").strip() or "poc-foundry-app",
        keyproxy_upstream=os.environ.get("PF_KEYPROXY_UPSTREAM", "").strip(),
        default_role=os.environ.get("PF_DEFAULT_ROLE", "architect").strip() or "architect",
        llm_timeout_s=_int_env("PF_LLM_TIMEOUT_S", 300),
        uv_cache_shared=os.environ.get("PF_UV_CACHE_SHARED", "").strip().lower() in ("1", "true", "yes"),
        max_fix_attempts=_int_env("PF_MAX_FIX_ATTEMPTS", int(budgets.get("max_fix_attempts", 3))),
        stuck_research_after=_int_env("PF_STUCK_RESEARCH_AFTER", int(budgets.get("stuck_research_after", 2))),
        max_iterations=_int_env("PF_MAX_ITERATIONS", int(budgets.get("max_iterations", 8))),
        toolcalls_per_attempt=_int_env("PF_TOOLCALLS_PER_ATTEMPT", int(budgets.get("toolcalls_per_attempt", 25))),
        max_llm_calls_per_iter=_int_env("PF_MAX_LLM_CALLS_ITER", int(budgets.get("max_llm_calls_per_iter", 60))),
        max_llm_calls_per_run=_int_env("PF_MAX_LLM_CALLS_RUN", int(budgets.get("max_llm_calls_per_run", 400))),
        max_iter_wall_clock_s=_int_env("PF_MAX_ITER_WALL_CLOCK_S", int(budgets.get("max_iter_wall_clock_s", 1800))),
        max_run_wall_clock_s=_int_env("PF_MAX_RUN_WALL_CLOCK_S", int(budgets.get("max_run_wall_clock_s", 14400))),
        max_research_results=_int_env("PF_MAX_RESEARCH_RESULTS", int(budgets.get("max_research_results", 4))),
        recursion_limit=_int_env("PF_RECURSION_LIMIT", int(budgets.get("recursion_limit", 150))),
        fix_limit_k=_int_env("PF_FIX_LIMIT_K", int(critic.get("fix_limit_k", 3))),
        respec_cap=_int_env("PF_RESPEC_CAP", int(critic.get("respec_cap", 1))),
        degraded_fix_limit_k=_int_env("PF_DEGRADED_FIX_LIMIT_K", int(critic.get("degraded_fix_limit_k", 2))),
        replan_cap=_int_env("PF_REPLAN_CAP", int(critic.get("replan_cap", 1))),
        reauthor_cap=_int_env("PF_REAUTHOR_CAP", int(critic.get("reauthor_cap", 1))),
        research_hosts=research_hosts,
        default_template=str(templates.get("default", "gradio-chatbot")),
        vetted_services=vetted,
    )
