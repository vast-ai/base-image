"""Tests for tools/install-vllm-audio-extras.sh (L096, docs/invariants.md).

vLLM registers /v1/audio/transcriptions and /v1/audio/translations whatever is
installed, but keeps the decoding behind an optional `audio` extra, and the upstream
image has never installed it — so both routes answer 400 for every request. The script
installs that extra and then imports it.

What this pins, each from a way the script has already been wrong or could be:

  * the extra is read from vLLM's OWN metadata, so the members follow the engine
    version instead of a hardcoded list that is right for one tag and wrong for the
    next (v0.13 is librosa + soundfile, v0.28+ is av + scipy + soundfile + soxr);
  * every member is IMPORTED after installing, because installing is not resolving;
  * the interpreter is the engine's. The first build of this script died with
    `PackageNotFoundError: No package metadata was found for vllm`: these images put
    /opt/sys-venv/shim on PATH, so `python3` is the shim and knows nothing about vLLM.
    A wrong interpreter must fail with a message that names the cause.

The real metadata machinery is used throughout — a fake `vllm` dist-info on PYTHONPATH
rather than a stubbed parser — so the test exercises the same code path the build runs.
`uv` is stubbed to record its arguments, because installing is not what is under test.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "tools/install-vllm-audio-extras.sh"

# Members that are importable without installing anything, so the verify step can
# succeed on its own merits. The names are what matters, not what they do.
AUDIO_MEMBERS = 'json; extra == "audio"\nre; extra == "audio"\n'


def _fake_vllm(tmp_path: Path, requires: str) -> Path:
    """A site-packages dir holding a vllm dist-info with the given Requires-Dist lines."""
    site = tmp_path / "site"
    info = site / "vllm-0.29.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: vllm\nVersion: 0.29.0\n"
        "Requires-Dist: torch==2.9.0\n"
        "Requires-Dist: pandas; extra == \"bench\"\n"
        + "".join(f"Requires-Dist: {line}\n" for line in requires.splitlines() if line)
    )
    (site / "vllm").mkdir()
    (site / "vllm" / "__init__.py").write_text("")
    return site


def _stub_uv(tmp_path: Path) -> Path:
    """A `uv` on PATH that records its argv instead of installing."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    uv = bindir / "uv"
    uv.write_text('#!/bin/bash\necho "$@" >> "$UV_CALLS"\n')
    uv.chmod(0o755)
    return bindir


def _run(tmp_path: Path, requires: str = AUDIO_MEMBERS, py: str | None = None):
    site = _fake_vllm(tmp_path, requires)
    bindir = _stub_uv(tmp_path)
    calls = tmp_path / "uv_calls.txt"
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "PY": py or sys.executable,
        "PYTHONPATH": str(site),
        "UV_CALLS": str(calls),
        "HOME": str(tmp_path),
    }
    proc = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
    return proc, (calls.read_text() if calls.exists() else "")


def test_the_audio_extra_is_installed_and_imported(tmp_path):
    """The happy path: exactly the audio members are installed, then imported."""
    proc, calls = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "resolves" in proc.stdout
    assert "json" in calls and "re" in calls


def test_only_the_audio_extra_is_installed(tmp_path):
    """Core deps and other extras are not the engine's audio requirement."""
    _proc, calls = _run(tmp_path)
    assert "torch" not in calls, "a core dependency was reinstalled"
    assert "pandas" not in calls, "the bench extra was swept in"


def test_members_follow_the_engine_version(tmp_path):
    """v0.13's extra differs from v0.28's, and this one Dockerfile builds both."""
    old = 'librosa; extra == "audio"\nsoundfile; extra == "audio"\n'
    _proc, calls = _run(tmp_path, requires=old)
    assert "librosa" in calls and "soundfile" in calls
    assert "soxr" not in calls, "the list was hardcoded rather than read from metadata"


def test_a_member_that_does_not_import_fails_the_build(tmp_path):
    """Installing is not resolving: a wheel that unpacks but cannot load leaves the
    audio routes failing exactly as before (the L056 lesson)."""
    broken = AUDIO_MEMBERS + 'no_such_audio_backend_xyz; extra == "audio"\n'
    proc, _calls = _run(tmp_path, requires=broken)
    assert proc.returncode != 0
    assert "does not import" in proc.stderr


def test_an_interpreter_without_vllm_fails_with_the_reason(tmp_path):
    """The first build's failure: python3 is the shim, and the traceback it produced
    named neither the interpreter nor the fix."""
    site = _fake_vllm(tmp_path, AUDIO_MEMBERS)  # present, but NOT on this run's path
    bindir = _stub_uv(tmp_path)
    proc = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True,
        env={"PATH": f"{bindir}:/usr/bin:/bin", "PY": sys.executable,
             "HOME": str(tmp_path)},
    )
    assert proc.returncode == 1
    assert "cannot see vLLM's metadata" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert site.exists()


def test_an_engine_with_no_audio_extra_refuses(tmp_path):
    """Silence here would ship an image whose audio routes 400."""
    proc, _calls = _run(tmp_path, requires='pandas; extra == "bench"\n')
    assert proc.returncode == 1
    assert "no 'audio' extra" in proc.stderr


@pytest.mark.parametrize("image", ["external/vllm", "external/vllm-omni"])
def test_every_vllm_derived_image_runs_the_installer(image):
    """The tree side of L096, against the real Dockerfiles."""
    text = (REPO / image / "Dockerfile").read_text()
    assert "tools/install-vllm-audio-extras.sh" in text


def test_the_engine_interpreter_is_preferred_over_python3(tmp_path):
    """The default is the half that broke: with PY unset the script must reach for the
    engine's interpreter, not the `python3` the shim puts first on PATH."""
    site = _fake_vllm(tmp_path, AUDIO_MEMBERS)
    bindir = _stub_uv(tmp_path)

    marker = tmp_path / "engine_py_was_used"
    engine_py = tmp_path / "venv_main_python"
    engine_py.write_text(
        f'#!/bin/bash\ntouch "{marker}"\n'
        f'PYTHONPATH="{site}" exec "{sys.executable}" "$@"\n'
    )
    engine_py.chmod(0o755)

    # a python3 on PATH that cannot see vllm, as the shim cannot
    shim = bindir / "python3"
    shim.write_text(f'#!/bin/bash\nexec "{sys.executable}" "$@"\n')
    shim.chmod(0o755)

    proc = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True,
        env={"PATH": f"{bindir}:/usr/bin:/bin", "ENGINE_PY": str(engine_py),
             "UV_CALLS": str(tmp_path / "uv_calls.txt"), "HOME": str(tmp_path)},
    )
    assert proc.returncode == 0, proc.stderr
    assert marker.exists(), "the engine interpreter was not used"


@pytest.mark.parametrize("image", ["external/vllm", "external/vllm-omni"])
def test_the_default_interpreter_is_the_one_the_image_builds(image):
    """The ENGINE_PY seam above proves the script PREFERS what it is told; this pins
    what it defaults to, against the image rather than against itself. The Dockerfile
    rewrites the `vllm` launcher's shebang to that interpreter, so if the image's venv
    layout ever moves, this fails instead of the audio routes silently 400ing again."""
    script = SCRIPT.read_text()
    m = re.search(r'ENGINE_PY="\$\{ENGINE_PY:-([^}]+)\}"', script)
    assert m, "the installer no longer declares a default interpreter"
    default = m.group(1)

    dockerfile = (REPO / image / "Dockerfile").read_text()
    shebang = re.search(r"1s\|#!/usr/bin/python3\|#!(\S+)\|", dockerfile)
    assert shebang, f"{image} no longer pins the launcher's interpreter"
    assert default == shebang.group(1), (
        f"the installer defaults to {default} but {image} runs the engine under "
        f"{shebang.group(1)}"
    )
