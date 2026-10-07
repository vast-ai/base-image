"""sd-forge's provisioning scripts split HF_MODELS, CIVITAI_MODELS, WGET_DOWNLOADS and
EXTENSIONS with the provisioner's splitter, like every PROVISIONING_* var (ADR 0053).

The functions are cut from the shipped scripts, so the text under test is the text
that ships.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from imagegen.discover import find_repo_root

REPO = find_repo_root(Path(__file__).resolve().parent)
SCRIPTS = sorted((REPO / "derivatives/pytorch/derivatives/sd-forge/provisioning_scripts").glob("*.sh"))
SPLITTER = "/opt/instance-tools/lib/provisioner/envlist.py"


def parse(script: Path, value: str, splitter: str) -> list[str]:
    text = script.read_text()
    funcs = "\n".join(re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M).group(0)
                      for name in ("normalize_entry", "split_env_entries", "parse_env_array"))
    funcs = funcs.replace(SPLITTER, splitter)
    out = subprocess.run(["bash", "-c", funcs + '\nparse_env_array V'], env={"V": value, "PATH": "/usr/bin:/bin"},
                         capture_output=True, check=True).stdout
    return out.decode().split("\0")[:-1]


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_commas_separate_entries(script):
    value = "https://h/a.safetensors|/m/a.safetensors, #comment,https://h/b.safetensors|/m/;https://h/c|/m/"
    assert parse(script, value, str(REPO / "ROOT" / SPLITTER.lstrip("/"))) == [
        "https://h/a.safetensors|/m/a.safetensors", "https://h/b.safetensors|/m/", "https://h/c|/m/"]


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_image_without_the_splitter_still_splits(script):
    """The scripts are fetched by URL, so they also run on images built before ADR 0053."""
    assert parse(script, "https://h/a|/m/;https://h/b|/m/,https://h/c|/m/", "/nonexistent/envlist.py") == [
        "https://h/a|/m/", "https://h/b|/m/", "https://h/c|/m/"]


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_a_failing_splitter_does_not_empty_the_list(script, tmp_path):
    """A process substitution hides the splitter's exit status; the list must not vanish."""
    broken = tmp_path / "envlist.py"
    broken.write_text("raise SystemExit(1)\n")
    assert parse(script, "https://h/a|/m/,https://h/b|/m/", str(broken)) == ["https://h/a|/m/", "https://h/b|/m/"]
