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
    value = "https://h/a.safetensors|/m/a.safetensors, #comment,https://h/b.safetensors|/m/"
    assert parse(script, value, str(REPO / "ROOT" / SPLITTER.lstrip("/"))) == [
        "https://h/a.safetensors|/m/a.safetensors", "https://h/b.safetensors|/m/"]


# parse_env_array as it was before ADR 0053, verbatim. The shipped parser must give the
# same entries for any value containing ";".
BEFORE = r"""
parse_env_array() {
    local env_var_name="$1"
    local env_value="${!env_var_name:-}"

    if [[ -n "$env_value" ]]; then
        local -a result=()
        IFS=';' read -ra entries <<< "$env_value"
        for entry in "${entries[@]}"; do
            entry=$(normalize_entry "$entry")
            # Skip empty entries and comments
            [[ -z "$entry" || "$entry" == \#* ]] && continue
            result+=("$entry")
        done
        # Return array elements, null-terminated
        if [[ ${#result[@]} -gt 0 ]]; then
            printf '%s\0' "${result[@]}"
        fi
    fi
}
"""

SEMICOLON_VALUES = [
    "https://h/f?ids=1,2|/m/f;https://h/g|/m/g",
    "a|/m/;\nb|/m/",
    "a|/m/;b|/m/\nc|/m/;d|/m/",
    "a\xa0;b\u2003|/m/",
    " ;#off|/m/; x , y |/m/;;",
    "\n;a",
]


def parse_before(script: Path, value: str) -> list[str]:
    text = script.read_text()
    normalize = re.search(r"^normalize_entry\(\) \{\n.*?^\}\n", text, re.S | re.M).group(0)
    out = subprocess.run(["bash", "-c", normalize + BEFORE + "\nparse_env_array V"], env={"V": value, "PATH": "/usr/bin:/bin"},
                         capture_output=True, check=True).stdout
    return out.decode().split("\0")[:-1]


@pytest.mark.parametrize("value", SEMICOLON_VALUES)
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_a_semicolon_value_splits_exactly_as_before(script, value):
    """Existing ";" lists keep their meaning, multi-line and odd whitespace included."""
    assert parse(script, value, str(REPO / "ROOT" / SPLITTER.lstrip("/"))) == parse_before(script, value)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_image_without_the_splitter_still_splits(script):
    """The scripts are fetched by URL, so they also run on images built before ADR 0053."""
    missing = "/nonexistent/envlist.py"
    assert parse(script, "https://h/a|/m/,https://h/b|/m/", missing) == ["https://h/a|/m/", "https://h/b|/m/"]
    assert parse(script, "https://h/f?ids=1,2|/m/f;https://h/g|/m/g", missing) == [
        "https://h/f?ids=1,2|/m/f", "https://h/g|/m/g"]


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_a_failing_splitter_does_not_empty_the_list(script, tmp_path):
    """A process substitution hides the splitter's exit status; the list must not vanish."""
    broken = tmp_path / "envlist.py"
    # It gets part of the way first: a partial list must not mix with the fallback's.
    broken.write_text("import sys\nsys.stdout.write('https://h/a|/m/\\0')\nraise SystemExit(1)\n")
    assert parse(script, "https://h/a|/m/,https://h/b|/m/", str(broken)) == ["https://h/a|/m/", "https://h/b|/m/"]
