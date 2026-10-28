"""Build-time bytecode (ADR 0060): the helper, the first-boot copy, and the instance test.

`bin/compile-bytecode` compiles every venv's site-packages and every app tree in the image's
final RUN; boot stage 36 copies app trees into $WORKSPACE keeping their timestamps, so the
timestamp-mode bytecode stays valid; `tests/base/47-bytecode.sh` checks it is valid where it
is imported. Each piece runs here against throwaway trees: real venvs made with the current
interpreter, and staged copies of the shipped scripts with their fixed paths pointed at a
temporary directory (each substitution is asserted, so a renamed path fails loudly instead of
silently touching the real /opt or /etc).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import struct
import subprocess
import sys
import venv
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HELPER = REPO / "ROOT/opt/instance-tools/bin/compile-bytecode"
STAGE36 = REPO / "ROOT/etc/vast_boot.d/36-sync-workspace.sh"
TESTS = REPO / "ROOT/opt/instance-tools/tests"


def _venv(path: Path) -> Path:
    venv.EnvBuilder(with_pip=False, symlinks=True).create(path)
    py = path / "bin" / "python"
    site = Path(subprocess.run([str(py), "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
                               capture_output=True, text=True, check=True).stdout.strip())
    return site


def _module(d: Path, name: str, body: str = "X = 1\n") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.py"
    p.write_text(body)
    return p


def _world(tmp: Path, *, bad_app_files: int = 0):
    venvs, apps = tmp / "venv", tmp / "opt/workspace-internal"
    site = _venv(venvs / "main")
    for k in range(20):
        _module(site / "pkg", f"m{k}")
    app = apps / "MyApp"
    for k in range(20):
        _module(app / "nodes", f"n{k}")
    for k in range(bad_app_files):
        _module(app / "templates", f"t{k}", "def broken(:\n")
    env = dict(os.environ, COMPILE_BYTECODE_VENVS=str(venvs), COMPILE_BYTECODE_APPS=str(apps),
               COMPILE_BYTECODE_VENV_SEARCH=str(tmp / "opt"), COMPILE_BYTECODE_MARKER=str(tmp / "etc/vast-bytecode.tsv"))
    env.pop("VAST_ENGINE_PYTHON", None)
    return env, site, app


def _run(env) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HELPER)], env=env, capture_output=True, text=True)


def _pyc_flags(py_interp: Path, src: Path) -> int:
    tag_pyc = subprocess.run([str(py_interp), "-c", "import importlib.util,sys;print(importlib.util.cache_from_source(sys.argv[1]))",
                              str(src)], capture_output=True, text=True, check=True).stdout.strip()
    with open(tag_pyc, "rb") as f:
        return struct.unpack("<I", f.read(8)[4:8])[0]


def test_compiles_every_venv_and_app_tree_and_records_them(tmp_path):
    env, site, app = _world(tmp_path)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    py = tmp_path / "venv/main/bin/python"
    for src in [site / "pkg/m0.py", app / "nodes/n0.py"]:
        assert _pyc_flags(py, src) == 0          # timestamp mode
    marker = (tmp_path / "etc/vast-bytecode.tsv").read_text().splitlines()
    assert f"{py}\t{site}" in marker and f"{py}\t{app}" in marker


def test_timestamp_mode_even_with_source_date_epoch(tmp_path):
    # SOURCE_DATE_EPOCH switches compileall's default to checked-hash, which reads every source
    # on every import; the helper must pin timestamp mode.
    env, site, _ = _world(tmp_path)
    env["SOURCE_DATE_EPOCH"] = "1700000000"
    assert _run(env).returncode == 0
    assert _pyc_flags(tmp_path / "venv/main/bin/python", site / "pkg/m3.py") == 0


def test_a_few_uncompilable_files_are_tolerated(tmp_path):
    env, _, _ = _world(tmp_path, bad_app_files=1)          # 1 of 21 app sources: 95.2%
    r = _run(env)
    assert r.returncode == 0, r.stderr


def test_too_little_compiled_fails_the_build(tmp_path):
    env, _, _ = _world(tmp_path, bad_app_files=5)          # 20 of 25: 80%
    r = _run(env)
    assert r.returncode == 1 and "too little compiled" in r.stderr


def test_on_an_external_image_the_mirror_is_compiled_not_the_engine(tmp_path):
    # The engine is imported through /venv/main; compiling its own site-packages too only doubles
    # the bytecode.
    env, site, _ = _world(tmp_path)
    engine_site = _venv(tmp_path / "engine")
    _module(engine_site / "engine_pkg", "e0")
    env["VAST_ENGINE_PYTHON"] = str(tmp_path / "engine/bin/python")
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert list(site.rglob("*.pyc"))
    assert not list(engine_site.rglob("*.pyc"))


def test_a_venv_outside_venv_is_found_by_its_pyvenv_cfg(tmp_path):
    # The portal's and the provisioner's venvs live under /opt, not /venv.
    env, _, _ = _world(tmp_path)
    tool_site = _venv(tmp_path / "opt/instance-tools/provisioner/venv")
    _module(tool_site / "prov", "p0")
    assert _run(env).returncode == 0
    assert list(tool_site.rglob("*.pyc"))
    assert str(tool_site) in (tmp_path / "etc/vast-bytecode.tsv").read_text()


def test_an_engine_venv_under_opt_is_not_compiled(tmp_path):
    # sglang's engine is itself a venv under /opt; it is imported through the /venv/main mirror.
    env, _, _ = _world(tmp_path)
    engine_site = _venv(tmp_path / "opt/sglang")
    _module(engine_site / "sglang", "s0")
    env["VAST_ENGINE_PYTHON"] = str(tmp_path / "opt/sglang/bin/python")
    assert _run(env).returncode == 0
    assert not list(engine_site.rglob("*.pyc"))


def _helper_module():
    import importlib.machinery, importlib.util
    loader = importlib.machinery.SourceFileLoader("compile_bytecode", str(HELPER))
    spec = importlib.util.spec_from_loader("compile_bytecode", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_app_trees_get_one_interpreter_per_python_version():
    # aio-studio runs each app in its own venv; with mixed versions every app needs each tag.
    cb = _helper_module()
    main, a, b, c = (Path(f"/venv/{n}/bin/python") for n in ("main", "a", "b", "c"))
    assert cb.app_interpreters({a: "3.11", main: "3.12", b: "3.12", c: "3.11"}, main) == [a, main]
    assert cb.app_interpreters({main: "3.12", b: "3.12"}, main) == [main]
    assert cb.app_interpreters({b: "3.12"}, None) == [b]


def test_a_single_python_version_compiles_each_app_tree_once(tmp_path):
    env, _, app = _world(tmp_path)
    _venv(tmp_path / "venv/comfyui")                       # a second venv, same version
    assert _run(env).returncode == 0
    marker = (tmp_path / "etc/vast-bytecode.tsv").read_text().splitlines()
    assert [l for l in marker if l.endswith(f"\t{app}")] == [f"{tmp_path / 'venv/main/bin/python'}\t{app}"]


# ---- boot stage 36: the first-boot copy keeps the sources' timestamps ----

def _stage36(tmp: Path) -> Path:
    text = STAGE36.read_text()
    assert "/opt/workspace-internal" in text
    staged = tmp / "36-sync-workspace.sh"
    staged.write_text(text.replace("/opt/workspace-internal", str(tmp / "opt/workspace-internal")))
    return staged


def test_first_boot_copy_keeps_compiled_bytecode_valid(tmp_path):
    env, _, app = _world(tmp_path)
    assert _run(env).returncode == 0
    old = 1_600_000_000
    for p in app.rglob("*.py"):
        os.utime(p, (old, old))
    assert _run(env).returncode == 0                       # recompile against the old mtimes
    ws = tmp_path / "workspace"
    r = subprocess.run(["/bin/bash", str(_stage36(tmp_path))], env=dict(os.environ, WORKSPACE=str(ws)),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    copied = ws / "MyApp/nodes/n0.py"
    assert int(copied.stat().st_mtime) == old


# ---- instance test 47: valid where imported; a copy that lost its timestamps fails ----

def _stage_test47(tmp: Path) -> Path:
    tdir = tmp / "tests"
    shutil.copytree(TESTS, tdir, ignore=shutil.ignore_patterns("*.d"))
    t = tdir / "base/47-bytecode.sh"
    text = t.read_text()
    assert "/etc/vast-bytecode.tsv" in text
    t.write_text(text.replace("/etc/vast-bytecode.tsv", str(tmp / "etc/vast-bytecode.tsv"))
                     .replace("/opt/workspace-internal", str(tmp / "opt/workspace-internal")))
    return t


def _test47(tmp: Path, ws: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/bash", str(_stage_test47(tmp))], capture_output=True, text=True,
                          env=dict(os.environ, WORKSPACE=str(ws), TEST_NAME="base/47-bytecode"))


def _copy_app(app: Path, ws: Path, *, keep_times: bool):
    args = ["cp", "-r", "--preserve=timestamps"] if keep_times else ["cp", "-r"]
    ws.mkdir(parents=True, exist_ok=True)
    subprocess.run(args + [str(app), str(ws)], check=True)


def test_instance_test_passes_on_a_timestamp_preserving_copy(tmp_path):
    env, _, app = _world(tmp_path)
    for p in app.rglob("*.py"):
        os.utime(p, (1_600_000_000, 1_600_000_000))
    assert _run(env).returncode == 0
    ws = tmp_path / "workspace"
    _copy_app(app, ws, keep_times=True)
    r = _test47(tmp_path, ws)
    assert r.returncode == 0, r.stdout + r.stderr


def test_instance_test_fails_when_the_copy_lost_its_timestamps(tmp_path):
    env, _, app = _world(tmp_path)
    for p in app.rglob("*.py"):
        os.utime(p, (1_600_000_000, 1_600_000_000))
    assert _run(env).returncode == 0
    ws = tmp_path / "workspace"
    _copy_app(app, ws, keep_times=False)
    r = _test47(tmp_path, ws)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "valid bytecode" in r.stdout + r.stderr


def test_instance_test_skips_on_an_image_without_the_marker(tmp_path):
    r = _test47(tmp_path, tmp_path / "workspace")
    assert r.returncode == 77


def test_instance_test_fails_when_its_probe_crashes(tmp_path):
    # A probe that prints nothing must not read as 0/0 sources and pass.
    broken = tmp_path / "broken-python"
    broken.write_text("#!/bin/bash\nexit 1\n")
    broken.chmod(0o755)
    tree = tmp_path / "site"
    _module(tree, "m0")
    marker = tmp_path / "etc/vast-bytecode.tsv"
    marker.parent.mkdir(parents=True)
    marker.write_text(f"{broken}\t{tree}\n")
    r = _test47(tmp_path, tmp_path / "workspace")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "probe failed" in r.stdout + r.stderr
