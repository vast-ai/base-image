"""The convert script judges an engine's virtualenv by the LOWEST pip it seeds into a supported Python.

Judging by the newest embedded wheel missed a release that gives one supported Python an older
pip than the rest (ADR 0058, amendment 2026-11-16).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

CONVERT = Path(__file__).resolve().parents[3] / "tools/convert-non-vast-image.sh"


def seed_pip_low(tmp_path: Path, table: str) -> str:
    embed = tmp_path / "pkgs/virtualenv/seed/wheels/embed"
    embed.mkdir(parents=True)
    for d in (embed, embed.parent, embed.parent.parent, embed.parent.parent.parent):
        (d / "__init__.py").touch()
    (embed / "__init__.py").write_text(f"BUNDLE_SUPPORT = {table}\n")
    engine = tmp_path / "python"          # stands in for the engine interpreter; -I would hide the fake package
    engine.write_text(f'#!/bin/bash\n[[ "$1" == -I ]] && shift\nPYTHONPATH={tmp_path}/pkgs exec python3 "$@"\n')
    engine.chmod(0o755)
    text = CONVERT.read_text()
    start = text.index("engine_seed_pip_low() {")
    fn = text[start:text.index("seed_pip=$(engine_seed_pip_low", start)]
    p = subprocess.run(["bash", "-euo", "pipefail", "-c", fn + "\nengine_seed_pip_low"], capture_output=True,
                       text=True, check=True, env={"VAST_ENGINE_PYTHON": str(engine), "PIP_FLOOR_MIN_PYTHON": "10",
                                                   "PATH": "/usr/bin:/bin"})
    return p.stdout.strip()


def test_a_supported_python_with_an_older_pip_than_the_rest_is_what_counts(tmp_path):
    table = '{"3.9": {"pip": "pip-25.0.1-py3-none-any.whl"}, "3.10": {"pip": "pip-26.2.1-py3-none-any.whl"}, ' \
            '"3.11": {"pip": "pip-26.3-py3-none-any.whl"}, "3.13": {"pip": "pip-26.3-py3-none-any.whl"}}'
    assert seed_pip_low(tmp_path, table) == "26.2.1"      # not 26.3 (the newest), not 25.0.1 (Python 3.9 only)
