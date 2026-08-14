"""Shared docker-availability gate for the three functional test modules.

Three modules boot a throwaway container because the thing under test is absolute
paths and persistent state, not a pure function (the CUDA driver helper, the TLS
cert boot script, the provisioner self-test). They must SKIP where docker is
absent locally, but a skip in CI is the silent-pass this repo has a whole test
(test_harness_require_pass.py) devoted to forbidding.

The earlier shape — a module-level `raise RuntimeError` under CI — was worse than
the skip it replaced: pytest raises it during COLLECTION, which interrupts the
ENTIRE session (`Interrupted: N errors during collection`), so a runner without
docker lost every imagegen test, ~260 of them, and reported a stack trace instead
of a test name. It also fired on `CI=false` (`os.environ.get("CI")` is truthy for
the string "false") and ran `docker info` with no timeout, so a wedged daemon
hung the job.

Instead: each module carries ONE real test, `test_docker_is_available_under_ci`,
that fails (a named red, attributable in the summary) exactly when CI is set and
docker is missing — and skips its functional tests otherwise. A vanished docker
then costs three named failures, not a collection blackout.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest


def ci_is_set() -> bool:
    """True only for a genuine CI signal. `CI=false`/`0`/empty are NOT CI —
    the string "false" is truthy, which the old `os.environ.get("CI")` check got
    wrong."""
    return os.environ.get("CI", "").strip().lower() not in ("", "0", "false", "no")


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


DOCKER_OK = docker_available()

requires_docker = pytest.mark.skipif(
    not DOCKER_OK,
    reason="docker unavailable — the functional gate needs a throwaway container",
)


def _dockerless_allowed() -> bool:
    """A usable override. `unset CI` is not available to someone re-running a
    GitHub job, so a deliberate opt-out needs its own env var."""
    return os.environ.get("ALLOW_DOCKERLESS_TESTS", "").strip().lower() in ("1", "true", "yes")


def assert_docker_present_under_ci() -> None:
    """The anti-silent-skip assertion, as a per-module TEST rather than an
    import-time raise. Green when docker is present, when we are not in CI (a
    local skip is fine), or when the opt-out is set; a single named red when CI
    is set and docker is not."""
    assert DOCKER_OK or not ci_is_set() or _dockerless_allowed(), (
        "docker is unavailable but CI is set: these gates must not silently skip. "
        "Fix the runner, or set ALLOW_DOCKERLESS_TESTS=1 to opt out deliberately."
    )
