"""Container-backed verification sandbox (adapted from poc-builder-prework/harness/runtime.py).

The coder edits files orchestrator-side (host workspace dir); VERIFY always runs in the container,
never on the host (the build engine produces arbitrary code). docker (runc) by default;
`--runtime kata` via the runtime flag. Uses the poc-foundry-sandbox image (uv + pytest + ruff).
"""
from __future__ import annotations

import shlex
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path


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


def _runtime_flag(runtime: str) -> list[str]:
    return ["--runtime", "kata"] if runtime == "kata" else []


class Sandbox:
    """A throwaway container over a bind-mounted host workspace, kept alive; we `docker exec` for
    each verify so the coder's edits (made on the host dir) are seen immediately."""

    def __init__(self, host_dir: Path, image: str = "poc-foundry-sandbox",
                 runtime: str = "runc", allow_egress: bool = True):
        self.host_dir = host_dir.resolve()
        self.image = image
        self.runtime = runtime
        # Deps install (uv/pip) needs egress in the spike. In M1 this goes through the allowlist proxy
        # on an internal network; for the bake-off we allow egress (or set False to test offline).
        self.allow_egress = allow_egress
        self.name = f"pf-m0b-{uuid.uuid4().hex[:8]}"
        self._started = False

    def start(self) -> None:
        net = [] if self.allow_egress else ["--network", "none"]
        cmd = ["docker", "run", "-d", "--rm", "--name", self.name,
               *_runtime_flag(self.runtime), *net,
               "-v", f"{self.host_dir}:/work", "-w", "/work",
               self.image, "sleep", "infinity"]
        _run(cmd, check=True)
        self._started = True

    def stop(self) -> None:
        if self._started:
            _run(["docker", "rm", "-f", self.name], check=False)
            self._started = False

    def __enter__(self) -> "Sandbox":
        self.start(); return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def exec(self, shell_cmd: str, timeout_s: int = 300) -> ExecResult:
        return _run(["docker", "exec", self.name, "bash", "-lc", shell_cmd], check=False, timeout_s=timeout_s)

    def verify(self, test_file: str, timeout_s: int = 300) -> ExecResult:
        """Lint (ruff, non-gating note) + the gate: pytest the given test. Exit 0 == green."""
        reqs = "test -f requirements.txt && uv pip install --system -q -r requirements.txt; "
        return self.exec(f"{reqs}python -m pytest {shlex.quote(test_file)} -q", timeout_s=timeout_s)


def _run(cmd: list[str], check: bool, timeout_s: int = 300) -> ExecResult:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        return ExecResult(rc=124, stdout=e.stdout or "", stderr=f"timeout after {timeout_s}s")
    res = ExecResult(rc=p.returncode, stdout=p.stdout, stderr=p.stderr)
    if check and not res.ok:
        raise RuntimeError(f"command failed ({res.rc}): {shlex.join(cmd)}\n{res.stderr[:500]}")
    return res
