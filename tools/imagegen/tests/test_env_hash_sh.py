"""env-hash covers uv's managed interpreters (ADR 0058, amendment 2026-11-16).

The environment sync swaps /.uv for the workspace copy stored under the image's hash. The hash
used to cover the venvs and apt only, so an image that added an interpreter under /.uv kept the
same hash, and an instance reusing a volume from the older build got the older /.uv without it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ENV_HASH = Path(__file__).resolve().parents[3] / "ROOT/opt/instance-tools/bin/env-hash"


def uv_hash(root: Path) -> str:
    text = ENV_HASH.read_text()
    fn = text[text.index("get_uv_hash() {"):text.index("get_venv_hash() {")]
    p = subprocess.run(["bash", "-euo", "pipefail", "-c", fn + f'\nget_uv_hash "{root}"'],
                       capture_output=True, text=True, check=True)
    return p.stdout


def interpreter(root: Path, name: str, minor: str, pip: str):
    (root / name / "lib" / f"python{minor}" / "site-packages" / f"pip-{pip}.dist-info").mkdir(parents=True)


def test_adding_an_interpreter_or_upgrading_its_pip_changes_the_hash(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for root in (a, b, c):
        interpreter(root, "cpython-3.11.17-linux-x86_64-gnu", "3.11", "26.3")
    interpreter(b, "cpython-3.10.22-linux-x86_64-gnu", "3.10", "26.3")
    interpreter(c, "cpython-3.10.22-linux-x86_64-gnu", "3.10", "23.0.1")
    hashes = {uv_hash(a), uv_hash(b), uv_hash(c)}
    assert len(hashes) == 3 and all(len(h) == 12 for h in hashes)


def test_the_minor_version_link_and_the_build_path_do_not_change_it(tmp_path):
    """Two builds of the same content must agree, wherever the directory sits."""
    a, b = tmp_path / "a", tmp_path / "elsewhere" / "b"
    for root in (a, b):
        interpreter(root, "cpython-3.10.22-linux-x86_64-gnu", "3.10", "26.3")
    (b / "cpython-3.10-linux-x86_64-gnu").symlink_to(b / "cpython-3.10.22-linux-x86_64-gnu")
    assert uv_hash(a) == uv_hash(b)


def test_an_image_with_no_uv_python_directory_keeps_its_old_hash(tmp_path):
    assert uv_hash(tmp_path / "absent") == ""


def test_the_result_is_part_of_the_environment_hash():
    text = ENV_HASH.read_text()
    assert "UV_HASH=$(get_uv_hash)" in text and "${VENV_HASHES}${UV_HASH}" in text
