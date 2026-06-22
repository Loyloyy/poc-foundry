"""Sandbox broker (design §5.5) — the harness's interface to isolated build environments.

M1 ships an in-process Docker stub (``Broker``) behind the stable ``create/create_service/exec/
destroy`` interface; M2a moves it out-of-process. The broker enforces the create-param invariant
(rule #8): only ``Sandbox.exec(cmd)`` ever carries LLM-derived content.

Stdlib-only at import (subprocess + the ``docker`` CLI) — no heavy agent stack here.
"""
from .broker import (
    ALLOWED_CAPS,
    Broker,
    BrokerError,
    BrokerInvariantError,
    ExecResult,
    Mount,
    Sandbox,
)

__all__ = [
    "Broker",
    "Sandbox",
    "Mount",
    "ExecResult",
    "BrokerError",
    "BrokerInvariantError",
    "ALLOWED_CAPS",
]
