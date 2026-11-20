"""The exemption matcher in tests/base/29-ca-trust.sh (ADR 0058, amendment 2026-11-16).

An exemption row has no expiry date, so what limits it is its pattern and its condition.
The dated list this replaced matched with a bare bash `[[ == ]]`, where `*` crosses `/`:
`*/botocore/cacert.pem` covered any environment and `/opt/nvidia/nsight-*/*` any file at
any depth. These tests run the real block from the shipped test with stubbed commands.

The time half of the `worker-bootstrap` condition (interpreter newer than /.env_hash) reads
a fixed path; the tests of it point that path and uv's Python directory at a temporary tree.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

TEST = Path(__file__).resolve().parents[3] / "ROOT/opt/instance-tools/tests/base/29-ca-trust.sh"

PRELUDE = r'''
TEST_NAME=base/29-ca-trust
test_fail() { echo "TEST_FAIL: $1"; exit 3; }
is_serverless() { [[ "${SERVERLESS:-}" == true ]]; }
uv() { echo "${UVPY_STUB:-/.uv/python_install}"; }
dpkg-query() { [[ "$2" == *"${DPKG_OWNS:-nothing}"* ]]; }
'''


def block(text: str) -> str:
    start = text.index("EXEMPTIONS='")
    end = text.index("# Debian/Ubuntu patch python3-certifi")
    return text[start:end]


def run(check: str, path: str, *, rows: str | None = None, env: dict | None = None, marker: str | None = None):
    body = block(TEST.read_text())
    if marker:
        assert "/.env_hash" in body
        body = body.replace("/.env_hash", marker)
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


def _uv_tree(tmp_path, shipped: str, arrived: str):
    """uv's Python directory with one interpreter older than the build marker and one newer."""
    uvpy, marker = tmp_path / "python_install", tmp_path / "env_hash"
    wheel = {}
    for name in (shipped, arrived):
        minor = ".".join(name.split("-")[1].split(".")[:2])
        wheel[name] = uvpy / name / f"lib/python{minor}/ensurepip/_bundled/pip-23.0.1-py3-none-any.whl"
        wheel[name].parent.mkdir(parents=True)
    marker.touch()
    os.utime(uvpy / shipped, (1_000_000, 1_000_000))
    os.utime(marker, (2_000_000, 2_000_000))
    os.utime(uvpy / arrived, (3_000_000, 3_000_000))
    return str(uvpy), str(marker), str(wheel[shipped]), str(wheel[arrived])


def test_the_worker_row_covers_any_python_that_arrived_after_the_build_and_none_the_image_shipped(tmp_path):
    """The bootstrap can change the version it asks for; an interpreter the image ships is always judged."""
    uvpy, marker, shipped, arrived = _uv_tree(tmp_path, "cpython-3.10.22-linux-x86_64-gnu",
                                              "cpython-3.12.9-linux-x86_64-gnu")
    env = {"SERVERLESS": "true", "UVPY_STUB": uvpy}
    out = run("pip-wheel", arrived, env=env, marker=marker)
    assert out[0].startswith("EXEMPT: base/29-ca-trust: worker-bootstrap pip-wheel ") and out[-1] == "rc=0 exempted=1"
    assert run("pip-wheel", shipped, env=env, marker=marker) == ["rc=1 exempted=0"]


def test_the_worker_row_never_applies_off_serverless(tmp_path):
    uvpy, marker, _shipped, arrived = _uv_tree(tmp_path, "cpython-3.10.22-linux-x86_64-gnu",
                                               "cpython-3.12.9-linux-x86_64-gnu")
    assert run("pip-wheel", arrived, env={"UVPY_STUB": uvpy}, marker=marker) == ["rc=1 exempted=0"]


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
