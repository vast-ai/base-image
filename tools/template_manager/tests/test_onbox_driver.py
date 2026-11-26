"""The on-box driver (onbox/driver.sh) against the baked runner it is derived from.

The launcher reads the driver's output with the parser it reads the runner's with, so
the two must print the same text for the same suite. The driver must also give the
checks supervisord's environment and nothing else, and say so when there is none.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fakebox import TESTS_ROOT, supervisord, write_tree

DRIVER = Path(__file__).resolve().parents[1] / "onbox/driver.sh"
LIB = 'source "$(dirname "$0")/../lib.sh"\n'
PASS = "#!/bin/bash\n" + LIB + "test_pass fine\n"
SKIP = "#!/bin/bash\n" + LIB + "test_skip 'not on this box'\n"
FAIL = "#!/bin/bash\n" + LIB + "echo detail line\ntest_fail broken\n"
FATAL = "#!/bin/bash\necho giving up\nexit 2\n"
SLOW = "#!/bin/bash\n# TEST_TIMEOUT=1\nsleep 20\n"

SUITES = {
    "all pass": ({"base/10-a.sh": PASS, "base/20-b.sh": SKIP, "x.d/10-c.sh": PASS}, {}),
    "base failure stops the derivative phase": (
        {"base/10-a.sh": PASS, "base/20-b.sh": FAIL, "base/30-c.sh": PASS,
         "x.d/10-d.sh": PASS, "x.d/20-e.sh": PASS}, {}),
    "fatal aborts the suite": (
        {"base/10-a.sh": PASS, "x.d/10-b.sh": FATAL, "x.d/20-c.sh": PASS}, {}),
    "timeout": ({"base/10-a.sh": PASS, "x.d/10-slow.sh": SLOW, "x.d/20-c.sh": PASS}, {}),
    "required-pass gate": (
        {"base/10-a.sh": PASS, "base/20-b.sh": SKIP},
        {"INSTANCE_TEST_REQUIRE_PASS": "base/10-a,base/20-b base/99-absent"}),
}


@pytest.fixture
def env(tmp_path):
    return {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "LANG": "C.UTF-8"}


@pytest.fixture
def supervisor(tmp_path, env):
    started = []

    def start(extra=None):
        proc, pidfile = supervisord(tmp_path, {**env, **(extra or {})})
        started.append(proc)
        return pidfile
    yield start
    for proc in started:
        proc.kill()
        proc.wait()


def _bundle(tmp_path, scripts):
    bundle = tmp_path / "box" / "run1" / "bundle"
    bundle.mkdir(parents=True)
    shutil.copy(DRIVER, bundle / "driver.sh")
    shutil.copy(TESTS_ROOT / "lib.sh", bundle / "lib.sh")
    write_tree(bundle, scripts)
    return bundle


def _normalise(text):
    """Durations differ run to run; nothing else may."""
    return re.sub(r"\b\d+s\)", "Ns)", text)


@pytest.mark.parametrize("name", SUITES)
def test_the_driver_prints_what_the_runner_prints(name, tmp_path, env, supervisor):
    scripts, extra = SUITES[name]

    baked = tmp_path / "baked"
    baked.mkdir()
    for f in ("runner.sh", "lib.sh"):
        shutil.copy(TESTS_ROOT / f, baked / f)
    write_tree(baked, scripts)
    runner = subprocess.run(
        ["bash", str(baked / "runner.sh"), "--manual"], capture_output=True, text=True,
        timeout=120, cwd="/",
        env={**env, **extra, "INSTANCE_TEST_RESULTS": str(tmp_path / "baked.json"),
             "INSTANCE_TEST_LOG": str(tmp_path / "baked.log")})

    bundle = _bundle(tmp_path, scripts)
    driver = subprocess.run(
        ["bash", str(bundle / "driver.sh"), "--manual", "--supervisord-pidfile",
         str(supervisor(extra))], capture_output=True, text=True, timeout=120, env=env)

    # the one line a removed part printed
    manual = "Running in manual mode (no HTTP server, no webhook, no instance stop)\n"
    assert runner.stdout.startswith(manual)
    assert "Running:" in driver.stdout, driver.stderr
    assert _normalise(driver.stdout) == _normalise(runner.stdout[len(manual):])
    assert driver.returncode == runner.returncode

    out = bundle.parent / "manual"
    assert _normalise((out / "output.log").read_text()) == _normalise(
        (tmp_path / "baked.log").read_text())
    mine, theirs = (json.loads(p.read_text()) for p in (out / "results.json",
                                                        tmp_path / "baked.json"))
    assert [(t["name"], t["state"]) for t in mine["tests"]] == [
        (t["name"], t["state"]) for t in theirs["tests"]]
    assert mine["state"] == theirs["state"]
    assert (out / "done").read_text() == f"run1 {mine['state']}\n"


def test_checks_get_exactly_supervisords_environment(tmp_path, env, supervisor):
    dump = tmp_path / "seen.env"
    bundle = _bundle(tmp_path, {"base/10-env.sh": f"#!/bin/bash\nenv -0 > {dump}\n"})
    given = {**env, "FROM_BOOT": "two words", "MULTI": "a\nb", "odd.name": "kept",
             "_": "/usr/bin/supervisord"}
    pidfile = supervisor(given)
    subprocess.run(
        ["bash", str(bundle / "driver.sh"), "--manual", "--supervisord-pidfile", str(pidfile)],
        capture_output=True, text=True, timeout=60, check=True,
        # what SSH gave the driver: none of it may reach a check
        env={**env, "PATH": os.environ["PATH"], "SSH_ONLY": "leaked", "FROM_BOOT": "stale"})

    seen = dict(e.split("=", 1) for e in dump.read_bytes().decode().split("\0") if e)
    # set by the shells in between, as they are under the baked runner
    for shell_owned in ("_", "PWD", "OLDPWD", "SHLVL", "TEST_NAME"):
        seen.pop(shell_owned, None)
    expected = {k: v for k, v in given.items() if k != "_"}
    assert seen == expected


def test_no_supervisord_is_an_environment_error_and_nothing_runs(tmp_path, env):
    ran = tmp_path / "ran"
    bundle = _bundle(tmp_path, {"base/10-a.sh": f"#!/bin/bash\ntouch {ran}\n"})
    stale = tmp_path / "supervisord.pid"
    stale.write_text(f"{os.getpid()}\n")           # alive, and not supervisord
    r = subprocess.run(
        ["bash", str(bundle / "driver.sh"), "--manual", "--supervisord-pidfile", str(stale),
         "--supervisord-wait", "1"], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode != 0 and not ran.exists()
    assert (bundle.parent / "manual/done").read_text() == "run1 environment\n"
    assert not (bundle.parent / "manual/started").exists()


def test_only_runs_the_named_tests_and_refuses_a_name_that_matches_nothing(tmp_path, env,
                                                                           supervisor):
    bundle = _bundle(tmp_path, {"base/10-a.sh": PASS, "base/20-b.sh": FAIL,
                                "x.d/10-c.sh": PASS})
    args = ["bash", str(bundle / "driver.sh"), "--supervisord-pidfile", str(supervisor())]
    r = subprocess.run([*args, "--only", "x.d/10-c,base/10-a"], capture_output=True,
                       text=True, timeout=60, env=env)
    assert re.findall(r"Running: (\S+)", r.stdout) == ["base/10-a", "x.d/10-c"]
    assert r.returncode == 0

    r = subprocess.run([*args, "--only", "base/10"], capture_output=True, text=True,
                       timeout=60, env=env)
    assert r.returncode == 1 and "no test named base/10" in r.stdout
    assert "Running:" not in r.stdout


def test_a_process_a_check_leaves_behind_does_not_keep_the_lock(tmp_path, env, supervisor):
    left = tmp_path / "left.pid"
    bundle = _bundle(tmp_path, {
        "base/10-a.sh": f"#!/bin/bash\nsleep 60 >/dev/null 2>&1 &\necho $! > {left}\n"})
    try:
        subprocess.run(["bash", str(bundle / "driver.sh"), "--manual", "--supervisord-pidfile",
                        str(supervisor())], capture_output=True, timeout=60, env=env, check=True)
        lock = bundle.parent.parent / "lock"
        assert lock.exists()
        assert subprocess.run(["flock", "-n", str(lock), "true"]).returncode == 0
    finally:
        os.kill(int(left.read_text()), 9)
