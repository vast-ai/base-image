"""Functional gate for ROOT/opt/instance-tools/tests/base/13-provisioner-selftest.sh.

13 runs the shipped provisioner for real, at boot stage 70, on a customer's
instance — five stages before that customer's own provisioning runs at stage 75.
Its entire safety argument is one line: it builds the provisioner's environment
with `env -i` and an allowlist instead of unsetting the variables its author
could name.

That argument fails SILENTLY. A missed variable does not turn the test red; it
makes the test do something else — write the customer's provisioning log, run
their post_commands as root, validate real tokens against huggingface.co, or,
via PROVISIONER_FAILURE_ACTION, destroy the instance under test. An earlier
version of that file named six variables; the provisioner reads at least sixteen.

So this asserts on side effects in a container where every one of those
variables is set to something whose use would be visible, rather than trusting
the list to stay complete as the provisioner grows new readers.

Docker, because it needs the real provisioner venv, a real HTTP fixture, and a
throwaway / it can check afterwards for files that should not exist.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HARNESS = Path(__file__).resolve().parent / "harness/provisioner-selftest-harness.sh"
TOOLS = REPO / "ROOT/opt/instance-tools"

# Carries python3 + venv + pip, so the harness only adds wget/curl. The provisioner
# venv is built from the shipped requirements.txt, not a hand-listed set, so a
# dependency change is picked up here rather than diverging from the image.
IMAGE = "python:3.11-slim"
SETUP = "mkdir -p /opt && cp -r /src-instance-tools /opt/instance-tools && bash /harness.sh"


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(
        ["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


# A SKIP IS NOT A PASS, and in CI it must not even be available.
#
# This repo has a whole test (test_harness_require_pass.py) devoted to making
# "skipped != passed" enforceable in the instance suite, and then gates its own
# functional tests on a bare skipif whose CI guarantee lives in a comment. Three
# docker-backed gates now hinge on it. If docker ever stops being present on the
# runner — a runner image change, a self-hosted migration — these go quietly
# green having asserted nothing, which is precisely the shape they exist to stop.
# Locally a skip is still the right behaviour; under CI it is a red build.
_DOCKER_OK = _docker_available()
if not _DOCKER_OK and os.environ.get("CI"):
    raise RuntimeError(
        "docker is unavailable but CI is set: these gates must not silently skip. "
        "Fix the runner, or unset CI to skip them deliberately."
    )

requires_docker = pytest.mark.skipif(
    not _DOCKER_OK,
    reason="docker unavailable — cannot build the provisioner venv in a throwaway root",
)


@requires_docker
def test_selftest_leaks_nothing_from_a_hostile_environment():
    assert HARNESS.is_file(), HARNESS
    assert TOOLS.is_dir(), TOOLS
    args = ["docker", "run", "--rm",
            "-v", f"{HARNESS}:/harness.sh:ro",
            "-v", f"{TOOLS}:/src-instance-tools:ro"]

    for _ in (1, 2):                     # one retry: setup reaches PyPI + the archive
        proc = subprocess.run(args + [IMAGE, "bash", "-c", SETUP],
                              capture_output=True, text=True, timeout=900)
        output = proc.stdout + proc.stderr
        setup_failed = proc.returncode == 99 or "HARNESS SETUP FAILED" in output
        if not setup_failed:
            break
    if setup_failed:
        pytest.fail(f"container setup failed twice (not a code regression):\n{output}")

    assert proc.returncode == 0, output
    assert "ALL SCENARIOS OK" in output, output
