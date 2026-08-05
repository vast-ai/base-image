"""install_teardown: a launched instance is torn down on ANY exit — the normal
path, a signal, and (the leak that stranded a real QA instance) emit_outcome()
config_error/interrupted, which sys.exit()s without reaching the normal cleanup.
atexit closes that gap."""
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import test_template as tt

TM = Path(__file__).resolve().parents[1]   # tools/template_manager


class _FakeAPI:
    def __init__(self):
        self.destroyed = []
        self.stopped = []

    def destroy_instance(self, iid):
        self.destroyed.append(iid)
        return {}

    def stop_instance(self, iid):
        self.stopped.append(iid)
        return {}


def _ctx(instance_id=None):
    return types.SimpleNamespace(panel=None, dead_reason="", instance_id=instance_id)


def _args(destroy=False, keep=False):
    return types.SimpleNamespace(destroy=destroy, keep=keep)


def _install(monkeypatch, api, ctx, args):
    reg = {"atexit": [], "signals": []}
    monkeypatch.setattr(tt.atexit, "register", lambda fn: reg["atexit"].append(fn))
    monkeypatch.setattr(tt.signal, "signal", lambda sig, fn: reg["signals"].append(sig))
    cleanup = tt.install_teardown(api, ctx, args, lambda *a, **k: None)
    return cleanup, reg


def test_registers_atexit_and_signals(monkeypatch):
    cleanup, reg = _install(monkeypatch, _FakeAPI(), _ctx(42), _args(destroy=True))
    assert cleanup in reg["atexit"]                    # atexit teardown is wired
    assert tt.signal.SIGINT in reg["signals"]
    assert tt.signal.SIGTERM in reg["signals"]


def test_gate_run_destroys_tracked_instance(monkeypatch):
    api, ctx = _FakeAPI(), _ctx(42)
    cleanup, _ = _install(monkeypatch, api, ctx, _args(destroy=True))
    cleanup()                                          # what atexit would invoke
    assert api.destroyed == [42]
    assert ctx.instance_id is None                     # cleared


def test_idempotent_no_double_destroy(monkeypatch):
    api, ctx = _FakeAPI(), _ctx(42)
    cleanup, _ = _install(monkeypatch, api, ctx, _args(destroy=True))
    cleanup(); cleanup(); cleanup()                    # normal path + atexit + ...
    assert api.destroyed == [42]                       # destroyed exactly once


def test_no_instance_is_noop(monkeypatch):
    api, ctx = _FakeAPI(), _ctx(None)
    cleanup, _ = _install(monkeypatch, api, ctx, _args(destroy=True))
    cleanup()
    assert api.destroyed == [] and api.stopped == []


def test_keep_flag_leaves_instance_running(monkeypatch):
    api, ctx = _FakeAPI(), _ctx(42)
    cleanup, _ = _install(monkeypatch, api, ctx, _args(destroy=False, keep=True))
    cleanup()
    assert api.destroyed == [] and api.stopped == []   # --keep respected
    assert ctx.instance_id == 42


def test_emit_outcome_after_launch_destroys_via_atexit(tmp_path):
    # End-to-end in a real subprocess: an instance is "launched" (tracked), then a
    # config_error emit_outcome() sys.exit()s — atexit must still destroy it.
    marker = tmp_path / "destroyed.json"
    script = textwrap.dedent(f"""
        import types, json
        import test_template as tt
        rec = []
        class API:
            def destroy_instance(self, iid):
                rec.append(iid); open({str(marker)!r}, "w").write(json.dumps(rec))
                return {{}}
            def stop_instance(self, iid):
                return {{}}
        ctx = types.SimpleNamespace(panel=None, dead_reason="", instance_id=777)
        args = types.SimpleNamespace(destroy=True, keep=False)
        tt.install_teardown(API(), ctx, args, lambda *a, **k: None)
        tt._RAW_MODE = True
        tt.emit_outcome("config_error", 4, reason="429 after launch")  # sys.exit(4)
    """)
    r = subprocess.run([sys.executable, "-c", script], cwd=str(TM),
                       env={**os.environ, "PYTHONPATH": str(TM)},
                       capture_output=True, text=True)
    assert r.returncode == 4, r.stderr                 # exited as config_error
    assert marker.exists(), f"instance NOT destroyed on config_error exit\n{r.stderr}"
    assert json.loads(marker.read_text()) == [777]     # destroyed via atexit
