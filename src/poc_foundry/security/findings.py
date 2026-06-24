"""Finding-0 analysis (M4 S2, design §5.2): prove the sandbox VM holds NONE of the orchestrator's real
secrets. The broker constructs the VM env explicitly (proxy address + sacrificial token + sibling-service
IPs) and never passes the orchestrator's environment through; this turns that into a CHECKED claim — dump
the VM's env, scan it against the run's known secrets, and assert nothing leaked.

Pure (stdlib + ``scrub.collect_secrets``) so the analysis is fakes-testable; the live ``env`` comes from a
sandbox ``exec("env")`` over the tunnel in the beat harness.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SecretScan:
    """Result of scanning a sandbox env for orchestrator secrets. ``leaked`` lists the PLACEHOLDERS of any
    secret found in the VM (never the raw value — honest reporting stays clean). ``ok`` ⇔ nothing leaked."""

    leaked: list[str] = field(default_factory=list)
    scanned_keys: int = 0
    secret_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.leaked


def scan_sandbox_env(env: dict, secrets=None) -> SecretScan:
    """Scan a sandbox VM's environment (``{VAR: value}``) for any of the orchestrator's real secrets
    (``scrub.collect_secrets`` → ``[(value, placeholder)]``). Returns a :class:`SecretScan`; an empty
    ``leaked`` is the Finding-0 pass — the real key/Langfuse secret/model paths never reached the VM.

    Reports the leaked secret's PLACEHOLDER, not its value (the scan output is itself shareable)."""
    if secrets is None:
        from poc_foundry import scrub
        secrets = scrub.collect_secrets()
    blob = "\n".join(f"{k}={v}" for k, v in (env or {}).items())
    leaked = []
    for value, placeholder in secrets:
        if value and value in blob and placeholder not in leaked:
            leaked.append(placeholder)
    return SecretScan(leaked=leaked, scanned_keys=len(env or {}), secret_count=len(secrets))
