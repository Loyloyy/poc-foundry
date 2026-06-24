"""Security demonstration surfaces (M4 S2, design §5.2): the key-proxy (keeps the real model key out of
the sandbox) + Finding-0 analysis (prove no orchestrator secrets reach the VM). The live red-team beats
(egress containment + broker-invariant rejection) run over the broker; the testable analysis lives here."""
from poc_foundry.security.findings import SecretScan, scan_sandbox_env
from poc_foundry.security.keyproxy import (
    KeyProxyDenied,
    bearer_value,
    redact,
    swap_authorization,
)

__all__ = [
    "SecretScan",
    "scan_sandbox_env",
    "KeyProxyDenied",
    "bearer_value",
    "redact",
    "swap_authorization",
]
