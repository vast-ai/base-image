"""Mutation tests for the runner's required-pass gate (ADR 0019).

The defect these pin: a test that self-skips is indistinguishable from one that
ran clean. `test_skip` exits 77, the runner records "skipped", and "skipped" does
not set `has_failure` — so the suite reports **passed**. The base GPU trio
(60-gpu-cuda / 61-cuda-compute / 62-gpu-libraries) all open with
`has_gpu || test_skip`, so an image whose CUDA userland never loads reports green.
That is ADR 0005's "laundering booted into QA'd", arriving through the skip door.

`INSTANCE_TEST_REQUIRE_PASS` names the tests a gating run demands actually passed.
These tests drive the REAL runner.sh + lib.sh against synthetic test trees, and
each mutation asserts the gate is what changes the verdict — the first test pins
the buggy behaviour with the flag unset, so a regression that silently disables
the gate turns this file red rather than passing vacuously.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HARNESS = REPO / "ROOT/opt/instance-tools/tests"


def _tree(tmp_path: Path, scripts: dict[str, str]) -> Path:
    """Materialise a runnable harness: real runner.sh + lib.sh, synthetic tests."""
    root = tmp_path / "tests"
    (root / "base").mkdir(parents=True)
    for name in ("runner.sh", "lib.sh"):
        shutil.copy(HARNESS / name, root / name)
    (root / "runner.sh").chmod(0o755)
    for rel, body in scripts.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        p.chmod(0o755)
    return root


def _run(root: Path, tmp_path: Path, env: dict[str, str] | None = None):
    results = tmp_path / "results.json"
    e = {
        **os.environ,
        "INSTANCE_TEST_RESULTS": str(results),
        "INSTANCE_TEST_LOG": str(tmp_path / "out.log"),
        **(env or {}),
    }
    proc = subprocess.run(
        ["bash", str(root / "runner.sh"), "--manual"],
        capture_output=True, text=True, env=e, timeout=180,
    )
    data = json.loads(results.read_text()) if results.exists() else {}
    return proc, data


def _state_of(data: dict, name: str) -> str | None:
    for t in data.get("tests", []):
        if t["name"] == name:
            return t["state"]
    return None


PASSING = '#!/bin/bash\nsource "$(dirname "$0")/../lib.sh"\ntest_pass "ok"\n'
SKIPPING = '#!/bin/bash\nsource "$(dirname "$0")/../lib.sh"\ntest_skip "no GPU detected"\n'


# --- the bug, pinned as it exists without the gate -------------------------

def test_baseline_a_skipped_test_reports_the_suite_green(tmp_path):
    """Without the flag, a skipped test leaves the suite PASSING.

    This is the pre-existing behaviour and is correct for customer instances —
    it is pinned so the mutations below prove the gate is what changes it.
    """
    root = _tree(tmp_path, {"base/60-gpu-cuda.sh": SKIPPING, "base/10-ok.sh": PASSING})
    proc, data = _run(root, tmp_path)
    assert proc.returncode == 0
    assert data["state"] == "passed"
    assert _state_of(data, "base/60-gpu-cuda") == "skipped"


# --- the mutations the gate must catch ------------------------------------

def test_required_test_that_skips_turns_the_suite_red(tmp_path):
    """THE mutation: same tree, gate on → the suite must fail."""
    root = _tree(tmp_path, {"base/60-gpu-cuda.sh": SKIPPING, "base/10-ok.sh": PASSING})
    proc, data = _run(root, tmp_path, {"INSTANCE_TEST_REQUIRE_PASS": "base/60-gpu-cuda"})
    assert proc.returncode == 1
    assert data["state"] == "failed"
    assert "REQUIRED-FAIL base/60-gpu-cuda" in proc.stdout


def test_required_test_missing_from_the_image_turns_the_suite_red(tmp_path):
    """A required test absent from the image is a fail, not a silent pass —
    catches an overlay that lost the file, which no per-test flag could see."""
    root = _tree(tmp_path, {"base/10-ok.sh": PASSING})
    proc, data = _run(root, tmp_path, {"INSTANCE_TEST_REQUIRE_PASS": "base/60-gpu-cuda"})
    assert proc.returncode == 1
    assert data["state"] == "failed"
    assert "missing from this image" in proc.stdout


def test_required_test_left_unreached_by_a_fatal_turns_the_suite_red(tmp_path):
    """An earlier test_fatal marks the remainder 'skipped'. The suite already
    fails on the fatal itself; assert the gate names the unreached requirement
    too, so the reason is legible rather than inferred."""
    fatal = '#!/bin/bash\nsource "$(dirname "$0")/../lib.sh"\ntest_fatal "provisioning died"\n'
    root = _tree(tmp_path, {"base/12-fatal.sh": fatal, "base/60-gpu-cuda.sh": PASSING})
    proc, data = _run(root, tmp_path, {"INSTANCE_TEST_REQUIRE_PASS": "base/60-gpu-cuda"})
    assert proc.returncode == 1
    assert data["state"] == "failed"
    assert "REQUIRED-FAIL base/60-gpu-cuda" in proc.stdout


def test_required_tests_that_all_pass_stay_green(tmp_path):
    """The gate must not fail a run that genuinely satisfied it."""
    root = _tree(tmp_path, {"base/60-gpu-cuda.sh": PASSING, "base/61-cuda-compute.sh": PASSING})
    proc, data = _run(
        root, tmp_path,
        {"INSTANCE_TEST_REQUIRE_PASS": "base/60-gpu-cuda base/61-cuda-compute"},
    )
    assert proc.returncode == 0
    assert data["state"] == "passed"


@pytest.mark.parametrize("sep", [" ", ",", ", "])
def test_separators_are_accepted(tmp_path, sep):
    """Templates are written by hand; accept comma and/or whitespace."""
    root = _tree(tmp_path, {"base/60-gpu-cuda.sh": SKIPPING, "base/61-cuda-compute.sh": PASSING})
    req = sep.join(["base/60-gpu-cuda", "base/61-cuda-compute"])
    proc, _ = _run(root, tmp_path, {"INSTANCE_TEST_REQUIRE_PASS": req})
    assert proc.returncode == 1, f"gate did not fire with separator {sep!r}"


def test_gate_output_does_not_impersonate_a_test_verdict(tmp_path):
    """The client attributes any line starting with the → verdict marker to the
    test named by the preceding 'Running:' header — which is cleared by the time
    the gate runs. A → line here would bump the failed counter while attaching to
    no test, so the gate must not emit one."""
    root = _tree(tmp_path, {"base/60-gpu-cuda.sh": SKIPPING})
    proc, _ = _run(root, tmp_path, {"INSTANCE_TEST_REQUIRE_PASS": "base/60-gpu-cuda"})
    gate_lines = [ln for ln in proc.stdout.splitlines() if "REQUIRED-FAIL" in ln]
    assert gate_lines, "gate produced no output"
    assert not any(ln.strip().startswith("→") for ln in gate_lines)


# --- the real GPU tests, on a GPU-less runner -----------------------------

@pytest.mark.parametrize("test_name", [
    "60-gpu-cuda", "61-cuda-compute", "62-gpu-libraries",
])
def test_real_gpu_suite_skips_here_and_the_gate_catches_it(tmp_path, test_name):
    """Corrupt-a-real-image check: copy the SHIPPED GPU test into the tree. CI
    runners have no GPU, so it self-skips — exactly the production failure mode
    (a box whose driver or libcuda is unavailable). The gate must turn that red."""
    src = HARNESS / "base" / f"{test_name}.sh"
    assert src.exists(), f"{src} missing — did the suite get renamed?"
    root = _tree(tmp_path, {f"base/{test_name}.sh": src.read_text()})

    proc_off, data_off = _run(root, tmp_path)
    assert data_off["state"] == "passed", "expected the skip-as-pass bug with the gate off"

    proc_on, data_on = _run(root, tmp_path, {"INSTANCE_TEST_REQUIRE_PASS": f"base/{test_name}"})
    assert proc_on.returncode == 1
    assert data_on["state"] == "failed"


# --- the per-test state bug the gate uncovered ----------------------------

def test_every_test_state_is_recorded_not_just_the_last(tmp_path):
    """Regression: results.json must report each test's real final state.

    `write_results` looped on a bare `i` while being called from inside the
    runner's own `for i in ...` loop. Bash has no implicit function scope, so
    each call left the caller's index pointing at the LAST test: every result
    was written to the final slot and every earlier entry stayed "running" even
    on a clean pass. Observed on a 2-test tree before the fix:
    base/10-first=running, base/20-second=passed.

    This also made the required-pass gate unusable, since it reads those states —
    which is how the bug surfaced. The client works around it by trusting the SSE
    stream over the JSON ("write-race issues"); it was never a race.
    """
    root = _tree(tmp_path, {
        "base/10-first.sh": PASSING,
        "base/20-second.sh": SKIPPING,
        "base/30-third.sh": PASSING,
    })
    proc, data = _run(root, tmp_path)
    assert proc.returncode == 0
    states = {t["name"]: t["state"] for t in data["tests"]}
    assert states == {
        "base/10-first": "passed",
        "base/20-second": "skipped",
        "base/30-third": "passed",
    }, f"per-test states are wrong: {states}"
    assert "running" not in states.values(), "a finished run still reports a test as running"
