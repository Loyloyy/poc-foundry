"""Bake-off task registry. Each task = a fixture dir with SPEC.md + source file(s) + a failing test.

The 3 planted-break kinds (design §6 M0(b)) + 1 red-first feature task (the real M2 workload — a
repair-only probe gives a false 'go'). All small, stdlib, deterministic, with VISIBLE tests the coder
must make pass without editing.
"""
from __future__ import annotations

from pathlib import Path

TASKS_DIR = Path(__file__).resolve().parent / "tasks"

TASKS = {
    "fix_syntax":    {"folder": "fix_syntax",    "src": ["mathops.py"], "test": "test_mathops.py", "kind": "syntax"},
    "fix_wrong_api": {"folder": "fix_wrong_api", "src": ["cfg.py"],     "test": "test_cfg.py",     "kind": "wrong-api"},
    "fix_logic":     {"folder": "fix_logic",     "src": ["stats.py"],   "test": "test_stats.py",   "kind": "failing-test"},
    "feature_slug":  {"folder": "feature_slug",  "src": ["slug.py"],    "test": "test_slug.py",    "kind": "red-first-feature"},
}
