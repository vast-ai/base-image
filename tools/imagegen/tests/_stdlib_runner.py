"""Shared stdlib fallback test runner (this environment has no pytest; CI uses pytest).

Both test_linter.py and test_generate.py drive their `if __name__ == "__main__"` block
through `run(globals())`. It supplies the pytest fixtures we actually use — `tmp_path`
(a temp dir) and `monkeypatch` (a minimal env/attr patcher with undo) — so multi-fixture
tests run instead of dying on a TypeError. Tests requesting any other fixture are SKIPped
(and counted) rather than silently mis-run.
"""
import inspect
import os
import tempfile
import traceback
from pathlib import Path

_SUPPORTED = ("tmp_path", "monkeypatch")


class MonkeyPatch:
    """Tiny stand-in for pytest's monkeypatch: setenv/delenv/setattr with undo."""

    def __init__(self):
        self._undo = []

    def setenv(self, name, value):
        self._undo.append(("env", name, os.environ.get(name)))
        os.environ[name] = value

    def delenv(self, name, raising=False):
        self._undo.append(("env", name, os.environ.get(name)))
        os.environ.pop(name, None)

    def setattr(self, target, name, value):
        self._undo.append(("attr", target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self):
        for entry in reversed(self._undo):
            if entry[0] == "env":
                _, name, prev = entry
                if prev is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = prev
            else:
                _, target, name, prev = entry
                setattr(target, name, prev)
        self._undo.clear()


def run(namespace):
    """Collect and run every `test_*` callable in `namespace`. Returns an exit code."""
    tests = [v for k, v in sorted(namespace.items()) if k.startswith("test_") and callable(v)]
    failed = skipped = 0
    for fn in tests:
        params = list(inspect.signature(fn).parameters)
        unsupported = [p for p in params if p not in _SUPPORTED]
        if unsupported:
            skipped += 1
            print("SKIP", fn.__name__, "(unsupported fixtures:", ", ".join(unsupported) + ")")
            continue
        mp = MonkeyPatch()
        try:
            with tempfile.TemporaryDirectory() as d:
                kwargs = {}
                if "tmp_path" in params:
                    kwargs["tmp_path"] = Path(d)
                if "monkeypatch" in params:
                    kwargs["monkeypatch"] = mp
                fn(**kwargs)
            print("PASS", fn.__name__)
        except Exception:
            failed += 1
            print("FAIL", fn.__name__)
            traceback.print_exc()
        finally:
            mp.undo()
    ran = len(tests) - skipped
    print(f"\n{ran - failed}/{ran} passed" + (f", {skipped} skipped" if skipped else ""))
    return 1 if failed else 0
