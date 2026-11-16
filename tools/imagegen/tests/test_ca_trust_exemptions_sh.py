"""The exemption matcher in tests/base/29-ca-trust.sh (ADR 0058, amendment 2026-11-16).

An exemption row has no expiry date, so what limits it is its pattern and its condition.
The dated list this replaced matched with a bare bash `[[ == ]]`, where `*` crosses `/`:
`*/botocore/cacert.pem` covered any environment and `/opt/nvidia/nsight-*/*` any file at
any depth. These tests run the real block from the shipped test with stubbed commands.

The time half of the `worker-bootstrap` condition (interpreter newer than /.env_hash) reads
a fixed path and is not reachable here; it was exercised in a container built from the base
image and is checked on every serverless QA run.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

TEST = Path(__file__).resolve().parents[3] / "ROOT/opt/instance-tools/tests/base/29-ca-trust.sh"

PRELUDE = r'''
TEST_NAME=base/29-ca-trust
test_fail() { echo "TEST_FAIL: $1"; exit 3; }
is_serverless() { [[ "${SERVERLESS:-}" == true ]]; }
uv() { echo /.uv/python_install; }
dpkg-query() { [[ "$2" == *"${DPKG_OWNS:-nothing}"* ]]; }
'''


def block(text: str) -> str:
    start = text.index("EXEMPTIONS='")
    end = text.index("# Debian/Ubuntu patch python3-certifi")
    return text[start:end]


def run(check: str, path: str, *, rows: str | None = None, env: dict | None = None):
    body = block(TEST.read_text())
    if rows is not None:
        body = "EXEMPTIONS='" + rows + "'" + body[body.index("'\n", len("EXEMPTIONS='")) + 1:]
    script = PRELUDE + body + f'\nexempt {check} "{path}"; echo "rc=$? exempted=$exempted"\n'
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", **(env or {})})
    return p.stdout.strip().splitlines()


NSIGHT = "/opt/nvidia/nsight-compute/2026.1.1/host/target-linux-x64/python/lib/python3.12"


def test_a_dpkg_owned_nsight_file_is_exempt_printed_and_counted():
    path = f"{NSIGHT}/site-packages/pip/_vendor/certifi/cacert.pem"
    out = run("bundle", path, env={"DPKG_OWNS": "nsight-compute"})
    assert out[0].startswith(f"EXEMPT: base/29-ca-trust: nsight-private-python bundle {path} [")
    assert out[-1] == "rc=0 exempted=1"


def test_a_file_dpkg_does_not_own_under_the_same_tree_is_not_exempt():
    """Something copied under /opt/nvidia after the vendor package was installed."""
    assert run("pip-wheel", f"{NSIGHT}/ensurepip/_bundled/pip-24.0-py3-none-any.whl") == ["rc=1 exempted=0"]


def test_a_row_does_not_cover_a_check_it_does_not_name():
    assert run("node", f"{NSIGHT}/bin/node", env={"DPKG_OWNS": "nsight"}) == ["rc=1 exempted=0"]


@pytest.mark.parametrize("path", [
    "/opt/nvidia/other-tool/python/cacert.pem",          # not an nsight-* directory
    "/srv/opt/nvidia/nsight-compute/x/cacert.pem",       # anchored at the root
    "/opt/nvidia/nsight-compute",                        # the directory itself, nothing below it
])
def test_the_pattern_is_anchored_and_needs_something_below(path):
    assert run("bundle", path, env={"DPKG_OWNS": "/"}) == ["rc=1 exempted=0"]


def test_a_star_does_not_cross_a_directory():
    rows = "one|bundle|/venv/*/botocore/cacert.pem|dpkg-owned|test row"
    owns = {"DPKG_OWNS": "/venv"}
    assert run("bundle", "/venv/main/botocore/cacert.pem", rows=rows, env=owns)[-1] == "rc=0 exempted=1"
    assert run("bundle", "/venv/main/lib/python3.12/site-packages/botocore/cacert.pem", rows=rows, env=owns) \
        == ["rc=1 exempted=0"]


def test_the_worker_row_never_applies_off_serverless_or_to_another_python():
    py310 = "/.uv/python_install/cpython-3.10.22-linux-x86_64-gnu/lib/python3.10/ensurepip/_bundled/pip-23.0.1-py3-none-any.whl"
    assert run("pip-wheel", py310) == ["rc=1 exempted=0"]                       # not serverless
    py311 = py310.replace("3.10.22", "3.11.9")
    assert run("pip-wheel", py311, env={"SERVERLESS": "true"}) == ["rc=1 exempted=0"]


@pytest.mark.parametrize("rows, why", [
    ("x|bundle|/opt/**/python/**|dpkg-owned|r", "is not accepted"),          # ** mid-pattern
    ("x|bundle|relative/path/*|dpkg-owned|r", "is not accepted"),
    ("x|bundle|/opt/x/**||r", "malformed exemption row"),                    # wildcard, no condition
    ("x|bundle|/opt/x/y|trust-me|r", "unknown condition"),
    ("x|everything|/opt/x/y|dpkg-owned|r", "unknown check"),
    ("x|bundle|/opt/x/y|dpkg-owned|r|2027-01-01", "malformed exemption row"),  # a sixth field
])
def test_a_row_the_test_cannot_stand_behind_fails_the_test(rows, why):
    out = run("bundle", "/opt/x/y", rows=rows)
    assert len(out) == 1 and out[0].startswith("TEST_FAIL: ") and why in out[0]


def test_the_worker_row_needs_the_interpreter_directly_under_uvs_python_directory():
    """A directory named like an interpreter, deeper in the tree or somewhere else, is not one."""
    env = {"SERVERLESS": "true"}
    elsewhere = "/srv/cpython-3.10.22-linux-x86_64-gnu/lib/python3.10/ensurepip/_bundled/pip-23.0.1-py3-none-any.whl"
    assert run("pip-wheel", elsewhere, env=env) == ["rc=1 exempted=0"]
