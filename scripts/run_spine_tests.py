#!/usr/bin/env python3
"""No-pytest runner for the fakes test modules — the 3.10 dev box has no pytest (rule #3), so this
provides a *tiny* pytest shim (``raises`` + a ``monkeypatch`` fixture + a ``tmp_path`` fixture) and
runs every ``test_*`` function it discovers. Mirrors ``scripts/run_contract_checks.py`` for the spine
+ the M2a gate fakes, so gate logic is provable locally before the server round-trip.

It is NOT a pytest replacement — it implements only the surface the fakes suites use (introspected
from the test signatures: ``monkeypatch``, ``tmp_path``; ``pytest.raises``; ``pytest.mark`` as a
no-op). In-container, real pytest runs the same files.

Usage::

    python3 scripts/run_spine_tests.py                       # default: the spine + the M2a gate suites
    python3 scripts/run_spine_tests.py tests/test_m1_spine.py
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
import tempfile
import traceback
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))


# ── the minimal pytest shim (registered before any test module imports `pytest`) ──────────────────
class _Raises:
    def __init__(self, expected):
        self.expected = expected

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError(f"DID NOT RAISE {self.expected!r}")
        return issubclass(exc_type, self.expected)   # swallow only the expected type


@contextmanager
def _raises(expected):
    with _Raises(expected) as r:
        yield r


def _build_pytest_module() -> ModuleType:
    m = ModuleType("pytest")
    m.raises = _raises
    # pytest.mark.<anything> → a no-op decorator (the fakes suites don't rely on mark behaviour).
    class _Mark:
        def __getattr__(self, _name):
            def deco(*a, **k):
                if len(a) == 1 and callable(a[0]) and not k:
                    return a[0]
                return lambda f: f
            return deco
    m.mark = _Mark()
    m.fixture = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
    m.skip = lambda *a, **k: (_ for _ in ()).throw(AssertionError("pytest.skip called"))
    return m


sys.modules.setdefault("pytest", _build_pytest_module())


# ── the `monkeypatch` fixture ─────────────────────────────────────────────────────────────────────
class _MonkeyPatch:
    def __init__(self):
        self._undo: list = []

    def setattr(self, target, name, value=None, raising=True):
        # support both setattr(obj, "name", value) and setattr("mod.path.attr", value)
        if value is None and isinstance(name, object) and not isinstance(name, str):
            target, name, value = self._resolve_dotted(target, name)
        old = getattr(target, name, _MISSING)
        self._undo.append(lambda: (setattr(target, name, old) if old is not _MISSING
                                   else delattr(target, name)))
        setattr(target, name, value)

    def setitem(self, dic, key, value):
        sentinel = object()
        old = dic.get(key, sentinel)
        self._undo.append(lambda: (dic.__setitem__(key, old) if old is not sentinel
                                   else dic.pop(key, None)))
        dic[key] = value

    def setenv(self, name, value):
        import os
        self.setitem(os.environ, name, str(value))

    def delenv(self, name, raising=False):
        import os
        if name in os.environ:
            self.setitem(os.environ, name, os.environ[name])  # record then remove
            del os.environ[name]

    @staticmethod
    def _resolve_dotted(dotted, value):
        modpath, _, attr = dotted.rpartition(".")
        import importlib
        return importlib.import_module(modpath), attr, value

    def undo(self):
        for fn in reversed(self._undo):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        self._undo.clear()


_MISSING = object()


# ── discovery + run ───────────────────────────────────────────────────────────────────────────────
def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_fixture(name: str, stack):
    if name == "monkeypatch":
        mp = _MonkeyPatch()
        stack.append(mp.undo)
        return mp
    if name == "tmp_path":
        d = tempfile.mkdtemp(prefix="pf-spine-")
        return Path(d)
    raise RuntimeError(f"unsupported fixture: {name!r}")


def run_file(path: Path) -> tuple[int, int]:
    mod = _load_module(path)
    fns = [(n, f) for n, f in vars(mod).items()
           if n.startswith("test_") and inspect.isfunction(f) and f.__module__ == mod.__name__]
    passed = failed = 0
    print(f"\n=== {path.name} ({len(fns)} tests) ===")
    for name, fn in fns:
        cleanup: list = []
        kwargs = {p: _make_fixture(p, cleanup) for p in inspect.signature(fn).parameters}
        try:
            fn(**kwargs)
            print(f"  PASS: {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL: {name}")
            traceback.print_exc()
            failed += 1
        finally:
            for c in cleanup:
                c()
    return passed, failed


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or [
        _REPO / "tests" / "test_m1_spine.py",
        _REPO / "tests" / "test_m2a_gates.py",
    ]
    tp = tf = 0
    for t in targets:
        if not t.exists():
            continue
        p, f = run_file(t)
        tp += p
        tf += f
    print(f"\nSUMMARY: {tp} passed, {tf} failed")
    return 1 if tf else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
