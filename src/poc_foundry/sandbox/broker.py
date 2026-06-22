"""In-process broker stub (M1) — drives host Docker to provision a per-build sandbox environment.

Per build the broker provisions (design §5.2, §5.5):
  • a Docker ``internal: true`` network (NO route out — the proxy is the sole exit);
  • a NORMAL bridge network for the proxy's egress leg;
  • the dual-homed allowlisting **egress proxy** (reached by **IP**, not name — Kata guests get no
    Docker name-DNS, DECISIONS #9);
  • a per-build uv-cache named volume (persists across this build's iterations).
Then it spins **fresh Kata sandbox VMs** on demand (``create`` → ``Sandbox`` → ``exec`` → destroy).

INVARIANT (security-load-bearing, AGENTS.md rule #8): ``create``/``create_service`` parameters are
harness-fixed and allowlisted — NEVER templated from artifact or model output. Only ``exec(cmd)``
carries LLM-derived content. This stub enforces that with explicit allowlist checks that RAISE
(`BrokerInvariantError`) on any violation.

M1 residual (risk-accepted, DECISIONS): the broker runs **in-process** and drives the host docker
socket via the ``docker`` CLI (promoted from ``scripts/m0b_bakeoff/sandbox.py`` — the validated
path). M2a moves it out-of-process behind a socket. All isolation language is defense-in-depth;
never "cannot be escaped" (rule #9).
"""
from __future__ import annotations

import re
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass

# ── allowlists for the create* invariant ─────────────────────────────────────
# Linux capabilities a create* call may request. M1 grants NONE (the template needs no extra caps);
# any cap outside this set RAISES. Widen only with a logged decision + a planning consult.
ALLOWED_CAPS: frozenset[str] = frozenset()

# A create* image / name / mount-target must look like a tame token (no shell metacharacters, no
# substitution that could smuggle LLM content into the harness-fixed docker argv). Belt-and-braces:
# argv is already passed as a list (no shell), but we reject anything suspicious anyway.
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_SAFE_ABS_PATH = re.compile(r"^/[A-Za-z0-9._/-]*$")


class BrokerError(RuntimeError):
    """A docker operation failed."""


class BrokerInvariantError(BrokerError):
    """A create*/exec call violated the harness-fixed allowlist invariant (rule #8)."""


@dataclass
class ExecResult:
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0

    @property
    def combined(self) -> str:
        return (self.stdout or "") + (self.stderr or "")


@dataclass(frozen=True)
class Mount:
    """A bind mount. ``source`` is a HOST path (sibling-container semantics — the orchestrator and
    the sandbox share the host's filesystem, so this is the path as the *host* sees it). Targets +
    sources are harness-constructed, never artifact/LLM-derived (the invariant)."""

    source: str
    target: str
    read_only: bool = False

    def to_arg(self) -> str:
        return f"{self.source}:{self.target}" + (":ro" if self.read_only else "")


def _run(cmd: list[str], *, check: bool, timeout_s: int = 300) -> ExecResult:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        return ExecResult(rc=124, stdout=e.stdout or "", stderr=f"timeout after {timeout_s}s")
    res = ExecResult(rc=p.returncode, stdout=p.stdout, stderr=p.stderr)
    if check and not res.ok:
        raise BrokerError(f"docker command failed ({res.rc}): {shlex.join(cmd)}\n{res.stderr[:600]}")
    return res


def _runtime_flag(runtime: str) -> list[str]:
    # Only `kata` and `runc` are valid here; default to runc for anything unexpected.
    return ["--runtime", "kata"] if runtime == "kata" else []


class Sandbox:
    """A handle to one running sandbox container. The orchestrator edits files on the host workspace
    dir (bind-mounted RW); ``exec`` runs verification INSIDE the container — never on the host (the
    container may run arbitrary build code). One VM is short-lived (fresh-per-iteration)."""

    def __init__(self, broker: "Broker", name: str):
        self._broker = broker
        self.name = name
        self._alive = True

    def exec(self, cmd: str, *, timeout_s: int = 600) -> ExecResult:
        """Run a shell command in the sandbox. ``cmd`` is the ONLY broker input that may carry
        LLM-derived content (rule #8); it runs inside the isolated VM, not on the host."""
        if not self._alive:
            raise BrokerError(f"sandbox {self.name} already destroyed")
        return _run(["docker", "exec", self.name, "bash", "-lc", cmd], check=False, timeout_s=timeout_s)

    def destroy(self) -> None:
        if self._alive:
            _run(["docker", "rm", "-f", self.name], check=False)
            self._alive = False
            self._broker._forget(self.name)


class Broker:
    """Per-build sandbox-environment manager (in-process M1 stub).

    Lifecycle::

        with Broker(build_id, cfg, allowed_images={cfg.sandbox_image}) as broker:
            broker.provision()                      # net + proxy + uv-cache vol
            sbx = broker.create(mounts=[...])       # a fresh kata VM
            sbx.exec("python -m pytest ...")
            sbx.destroy()                           # ... or let __exit__ reap everything
    """

    def __init__(self, build_id: str, cfg, *, allowed_images: set[str],
                 runtime: str | None = None, vllm_key: str = "not-needed"):
        self.build_id = build_id
        self.cfg = cfg
        self.allowed_images = set(allowed_images)
        self.runtime = runtime or getattr(cfg, "kata_runtime", "kata")
        self.vllm_key = vllm_key

        short = uuid.uuid4().hex[:8]
        self.net_internal = f"pf-{short}-int"
        self.net_egress = f"pf-{short}-egr"
        self.proxy_name = f"pf-{short}-proxy"
        # The networks/proxy/sandboxes are ALWAYS fresh-per-build (isolation, no leaks). The uv-cache
        # is the one thing that can be persisted: when PF_UV_CACHE_SHARED is set it reuses ONE stable
        # volume across builds (a TRUSTED-INPUT dev-loop speed-up — a shared cache is a cross-build
        # channel, so it is OFF by default and must NEVER be used with untrusted artifacts).
        self._uv_shared = bool(getattr(cfg, "uv_cache_shared", False))
        self.uv_volume = "pf-uvcache-shared" if self._uv_shared else f"pf-{short}-uvcache"
        self.proxy_url: str | None = None

        self._sandboxes: dict[str, Sandbox] = {}
        self._provisioned = False
        self.events: list[str] = []          # human-readable provisioning/egress trace

    # ── context manager ──────────────────────────────────────────────────────
    def __enter__(self) -> "Broker":
        return self

    def __exit__(self, *exc) -> None:
        self.destroy()

    def _log(self, msg: str) -> None:
        self.events.append(msg)

    def _forget(self, name: str) -> None:
        self._sandboxes.pop(name, None)

    # ── provisioning ─────────────────────────────────────────────────────────
    def provision(self) -> None:
        """Create the per-build network pair, the egress proxy (dual-homed), and the uv-cache
        volume. Idempotent within a build. Resolves the proxy's INTERNAL-network IP and records
        ``proxy_url = http://<ip>:3128`` (by IP — Kata guests can't resolve container names)."""
        if self._provisioned:
            return
        proxy_image = getattr(self.cfg, "proxy_image", "poc-foundry-proxy")
        if proxy_image not in self.allowed_images:
            raise BrokerInvariantError(f"proxy image {proxy_image!r} not in allowlist")

        # Atomic: if ANY step fails (e.g. the proxy doesn't come up after the networks/volume were
        # created), tear down the partial state so a failed provision never leaks docker resources.
        try:
            _run(["docker", "network", "create", "--internal", self.net_internal], check=True)
            _run(["docker", "network", "create", self.net_egress], check=True)
            _run(["docker", "volume", "create", self.uv_volume], check=True)

            vllm_host = getattr(self.cfg, "vllm_allow_host", "") or ""
            _run(["docker", "run", "-d", "--name", self.proxy_name, "--network", self.net_egress,
                  "-e", f"PF_VLLM_ALLOW_HOST={vllm_host}", proxy_image], check=True)
            _run(["docker", "network", "connect", self.net_internal, self.proxy_name], check=True)
            self.proxy_url = self._await_proxy()
        except Exception:
            self.destroy()
            raise

        self._provisioned = True
        self._log(f"provisioned net={self.net_internal}/{self.net_egress} proxy={self.proxy_url}")

    def _await_proxy(self, *, tries: int = 10) -> str:
        """Wait for the proxy to be Running, then return its internal-net IP as a proxy URL. Fails
        LOUD with squid logs if it never comes up (a squid config error aborts startup — the
        misleading downstream symptom is 'could not resolve proxy', DEV_NOTES squid saga)."""
        for _ in range(tries):
            state = _run(["docker", "inspect", "-f", "{{.State.Running}}", self.proxy_name], check=False)
            if state.stdout.strip() == "true":
                break
            time.sleep(1)
        else:
            logs = _run(["docker", "logs", self.proxy_name], check=False).combined[-800:]
            raise BrokerError(f"egress proxy {self.proxy_name} did not start (squid?):\n{logs}")

        ipfmt = '{{(index .NetworkSettings.Networks "%s").IPAddress}}' % self.net_internal
        ip = _run(["docker", "inspect", "-f", ipfmt, self.proxy_name], check=False).stdout.strip()
        if not ip:
            raise BrokerError(f"could not resolve proxy IP on {self.net_internal}")
        time.sleep(2)   # give squid a beat to bind :3128 after the container reports Running
        return f"http://{ip}:3128"

    # ── invariant guards ─────────────────────────────────────────────────────
    def _check_image(self, image: str) -> None:
        if image not in self.allowed_images:
            raise BrokerInvariantError(
                f"image {image!r} is not allowlisted (create* params are harness-fixed, rule #8)")

    def _check_caps(self, caps) -> None:
        bad = set(caps) - ALLOWED_CAPS
        if bad:
            raise BrokerInvariantError(f"capabilities {sorted(bad)} not in ALLOWED_CAPS (rule #8)")

    def _check_mounts(self, mounts) -> None:
        for m in mounts:
            if not (_SAFE_ABS_PATH.match(m.source) and _SAFE_ABS_PATH.match(m.target)):
                raise BrokerInvariantError(f"mount {m.source!r}->{m.target!r} is not a tame abs path (rule #8)")

    def _check_name(self, name: str) -> None:
        if not _SAFE_TOKEN.match(name):
            raise BrokerInvariantError(f"name {name!r} is not a tame token (rule #8)")

    # ── sandbox lifecycle ────────────────────────────────────────────────────
    def create(self, *, mounts: list[Mount], caps=(), name: str = "sbx",
               image: str | None = None, env_extra: dict | None = None) -> Sandbox:
        """Spin a FRESH sandbox VM (kata) on the internal network, egress only via the proxy (by
        IP). All params are harness-fixed/allowlisted (the invariant). Returns a live ``Sandbox``."""
        if not self._provisioned:
            raise BrokerError("call provision() before create()")
        image = image or getattr(self.cfg, "sandbox_image", "poc-foundry-sandbox")
        self._check_image(image)
        self._check_caps(caps)
        self._check_mounts(mounts)
        self._check_name(name)

        full = f"pf-{self.build_id[-12:]}-{name}-{uuid.uuid4().hex[:6]}"
        full = full.replace("--", "-")
        env = {
            # The VM's ONLY exit is the proxy, reached by IP (Kata has no Docker name-DNS).
            "HTTPS_PROXY": self.proxy_url, "HTTP_PROXY": self.proxy_url,
            "https_proxy": self.proxy_url, "http_proxy": self.proxy_url,
            "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1",
            # Only the sacrificial inference key is ever exposed to the sandbox (Finding-0).
            "PF_SANDBOX_VLLM_KEY": self.vllm_key,
        }
        env.update(env_extra or {})

        argv = ["docker", "run", "-d", "--rm", "--name", full, *_runtime_flag(self.runtime),
                "--network", self.net_internal]
        for k, v in env.items():
            argv += ["-e", f"{k}={v}"]
        for cap in caps:
            argv += ["--cap-add", cap]
        for m in mounts:
            argv += ["-v", m.to_arg()]
        argv += ["-v", f"{self.uv_volume}:/uv-cache", "-w", "/work", image, "sleep", "infinity"]

        _run(argv, check=True)
        sbx = Sandbox(self, full)
        self._sandboxes[full] = sbx
        self._log(f"created sandbox {full} (runtime={self.runtime})")
        return sbx

    def create_service(self, *, image: str, name: str, env: dict | None = None,
                       pinned_tag: str | None = None, ready_cmd: str | None = None) -> Sandbox:
        """Spin a sibling production service on the per-build internal net (design §5.6). Image MUST be
        allowlisted + tag-pinned (the invariant — image/tag come from the harness-fixed vetted list, not
        from artifact/model output). Reached BY IP from the sandbox (same Kata-DNS rule as the proxy).
        Runs under the DEFAULT runtime (a stock vendor image, not Kata) — siblings are infra, not the
        LLM-driven build env. If ``ready_cmd`` is given, block until it succeeds (e.g. ``pg_isready``)."""
        if not self._provisioned:
            raise BrokerError("call provision() before create_service()")
        ref = f"{image}:{pinned_tag}" if pinned_tag else image
        self._check_image(ref)
        self._check_name(name)
        full = f"pf-{self.build_id[-12:]}-svc-{name}-{uuid.uuid4().hex[:6]}".replace("--", "-")
        argv = ["docker", "run", "-d", "--rm", "--name", full, "--network", self.net_internal]
        for k, v in (env or {}).items():
            argv += ["-e", f"{k}={v}"]
        argv += [ref]
        _run(argv, check=True)
        sbx = Sandbox(self, full)
        self._sandboxes[full] = sbx
        self._await_service(full, ready_cmd)
        self._log(f"created service {full} ({ref})")
        return sbx

    def _await_service(self, name: str, ready_cmd: str | None, *, tries: int = 40) -> None:
        """Wait for a service container to be Running and (if given) for ``ready_cmd`` to succeed
        inside it. Fails LOUD with the container logs if it never comes up."""
        for _ in range(tries):
            if _run(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False).stdout.strip() == "true":
                break
            time.sleep(1)
        else:
            logs = _run(["docker", "logs", name], check=False).combined[-800:]
            raise BrokerError(f"service {name} never reached Running:\n{logs}")
        if not ready_cmd:
            time.sleep(1)
            return
        for _ in range(tries):
            if _run(["docker", "exec", name, "bash", "-lc", ready_cmd], check=False).ok:
                return
            time.sleep(1)
        logs = _run(["docker", "logs", name], check=False).combined[-800:]
        raise BrokerError(f"service {name} not ready ({ready_cmd!r}) in time:\n{logs}")

    def service_ip(self, sandbox: Sandbox) -> str:
        """Internal-net IP of a service container (siblings reach it by IP, not name — Kata DNS)."""
        ipfmt = '{{(index .NetworkSettings.Networks "%s").IPAddress}}' % self.net_internal
        return _run(["docker", "inspect", "-f", ipfmt, sandbox.name], check=False).stdout.strip()

    def proxy_log(self, *, tail: int = 200) -> str:
        """The proxy's CONNECT log (the detective security control; broker tees it to builds/logs)."""
        if not self.proxy_url:
            return ""
        return _run(["docker", "logs", self.proxy_name], check=False).combined[-tail * 200:]

    # ── teardown ─────────────────────────────────────────────────────────────
    def destroy(self) -> None:
        for sbx in list(self._sandboxes.values()):
            sbx.destroy()
        # Always attempt teardown of the named resources (rm/network rm/volume rm are idempotent —
        # no-ops if absent), so even a PARTIAL provision is fully cleaned. Order: sandboxes →
        # proxy → networks → volume (a network can't be removed while a container is attached).
        _run(["docker", "rm", "-f", self.proxy_name], check=False)
        _run(["docker", "network", "rm", self.net_internal, self.net_egress], check=False)
        if not self._uv_shared:                       # a shared uv-cache PERSISTS across builds (the speed-up)
            _run(["docker", "volume", "rm", self.uv_volume], check=False)
        if self._provisioned:
            self._log("destroyed build environment")
        self._provisioned = False
