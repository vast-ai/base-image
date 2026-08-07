"""configs/pytorch.json is the single source of truth for the pytorch matrices.

The lists were duplicated verbatim across build/extend/promote-pytorch.yml — 28
lines each — so a version bump had to land in three places and a miss was silent.
That is the same drift hazard configs/base-image.json removed (ADR 0019).

This file pins the extraction during the migration. While BOTH the table and the
inline arrays exist, the round-trip test proves they agree: edit one without the
other and it fails. Once every workflow reads the table, the round-trip assertions
become unreachable (the arrays are gone) and should be deleted with them — the
`test_no_workflow_still_hardcodes_the_lists` check below is what replaces them.
"""
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CONFIG = REPO / "configs/pytorch.json"
W = REPO / ".github/workflows"


@pytest.fixture(scope="module")
def table():
    return json.loads(CONFIG.read_text())


def _entries(text, arr):
    m = re.search(rf"^\s*{arr}=\(\n(.*?)^\s*\)\s*$", text, re.S | re.M)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else None


def _short(c):
    """cuda_short, as extend/promote spell it: cuda_ver up to the first '-'."""
    return c["cuda_ver"].split("-")[0]


# --- the table is well-formed ----------------------------------------------

def test_table_is_complete_and_ordered(table):
    assert table["configs"] and table["mini"] and table["multi"]
    assert table["default_python"] in {p for c in table["configs"] for p in c["python_versions"]}
    for c in table["configs"]:
        assert c["arches"], f"{c['key']} has no arches"
        assert c["python_versions"], f"{c['key']} has no pythons"
        assert re.fullmatch(r"\d+\.\d+\.\d+", c["torch"]), c["torch"]
        assert c["backend"].startswith(("cu", "rocm", "cpu")), c["backend"]


def test_every_config_key_is_unique_per_torch(table):
    """The (torch, key) pair names the artifact; a duplicate silently drops one."""
    seen = [(c["torch"], c["key"]) for c in table["configs"]]
    assert len(seen) == len(set(seen)), "duplicate (torch, key) in configs"


def test_the_backend_matches_the_cuda_base(table):
    """cu128 on a 12.6 base would install wheels the base's CUDA cannot run. The
    backend's minor must match the base image's."""
    for c in table["configs"]:
        want = c["backend"].removeprefix("cu")            # e.g. "128"
        got = _short(c).replace(".", "")[:len(want)]      # "12.8.1" -> "128"
        assert want == got, (
            f"{c['torch']}/{c['key']}: backend {c['backend']} against CUDA base "
            f"{c['cuda_ver']} — the wheel's CUDA minor must match the base's")


# --- round-trip: the table reproduces every workflow's list exactly ---------

def test_round_trip_build_configs(table):
    have = _entries((W / "build-pytorch.yml").read_text(), "ALL_CONFIGS")
    if have is None:
        pytest.skip("build-pytorch.yml now reads the table")
    want = [f"{c['torch']}|{c['key']}|{c['cuda_ver']}|{c['backend']}|"
            f"{','.join(c['arches'])}|{' '.join(c['python_versions'])}" for c in table["configs"]]
    assert have == want


def test_round_trip_build_mini(table):
    have = _entries((W / "build-pytorch.yml").read_text(), "ALL_MINI_CONFIGS")
    if have is None:
        pytest.skip("build-pytorch.yml now reads the table")
    want = [f"{c['torch']}|{c['key']}|{c['mini_base_tag']}|{c['backend']}|"
            f"{','.join(c['arches'])}|{' '.join(c['python_versions'])}" for c in table["mini"]]
    assert have == want


def test_round_trip_build_multi(table):
    have = _entries((W / "build-pytorch.yml").read_text(), "ALL_MULTI_CONFIGS")
    if have is None:
        pytest.skip("build-pytorch.yml now reads the table")
    want = [f"{c['key']}|{c['primary_torch']}|{c['primary_backend']}|{c['cuda_minor']}|"
            f"{','.join(c['arches'])}|{' '.join(c['python_versions'])}" for c in table["multi"]]
    assert have == want


def test_round_trip_extend_configs(table):
    have = _entries((W / "extend-pytorch.yml").read_text(), "ALL_CONFIGS")
    if have is None:
        pytest.skip("extend-pytorch.yml now reads the table")
    want = [f"{c['torch']}|{c['key']}|{_short(c)}|{','.join(c['arches'])}|"
            f"{' '.join(c['python_versions'])}" for c in table["configs"]]
    assert have == want


def test_round_trip_promote_configs(table):
    have = _entries((W / "promote-pytorch.yml").read_text(), "ALL_CONFIGS")
    if have is None:
        pytest.skip("promote-pytorch.yml now reads the table")
    want = [f"{c['torch']}|{c['key']}|{_short(c)}|{' '.join(c['python_versions'])}"
            for c in table["configs"]]
    assert have == want


def _minor(c):
    """cuda_minor, as extend/promote spell it for mini: cuda-12.9-mini -> 12.9."""
    return c["mini_base_tag"].removeprefix("cuda-").removesuffix("-mini")


def test_round_trip_extend_mini(table):
    have = _entries((W / "extend-pytorch.yml").read_text(), "ALL_MINI_CONFIGS")
    if have is None:
        pytest.skip("extend-pytorch.yml now reads the table")
    want = [f"{c['torch']}|{c['key']}|{c['backend']}|{_minor(c)}|"
            f"{','.join(c['arches'])}|{' '.join(c['python_versions'])}" for c in table["mini"]]
    assert have == want


def test_round_trip_promote_mini(table):
    have = _entries((W / "promote-pytorch.yml").read_text(), "ALL_MINI_CONFIGS")
    if have is None:
        pytest.skip("promote-pytorch.yml now reads the table")
    want = [f"{c['torch']}|{c['key']}|{c['backend']}|{_minor(c)}|"
            f"{' '.join(c['python_versions'])}" for c in table["mini"]]
    assert have == want


def test_round_trip_extend_multi(table):
    have = _entries((W / "extend-pytorch.yml").read_text(), "ALL_MULTI_CONFIGS")
    if have is None:
        pytest.skip("extend-pytorch.yml now reads the table")
    want = [f"{c['key']}|{c['primary_torch']}|{c['primary_backend']}|{c['cuda_minor']}|"
            f"{','.join(c['arches'])}|{' '.join(c['python_versions'])}" for c in table["multi"]]
    assert have == want


def test_round_trip_promote_multi(table):
    have = _entries((W / "promote-pytorch.yml").read_text(), "ALL_MULTI_CONFIGS")
    if have is None:
        pytest.skip("promote-pytorch.yml now reads the table")
    want = [f"{c['key']}|{c['primary_torch']}|{c['primary_backend']}|{c['cuda_minor']}|"
            f"{' '.join(c['python_versions'])}" for c in table["multi"]]
    assert have == want


def test_every_mini_base_tag_yields_a_cuda_minor(table):
    """extend/promote derive cuda_minor from mini_base_tag by stripping the affixes.
    A mini_base_tag that does not fit that shape would silently yield a garbage
    minor rather than failing."""
    import re as _re
    for c in table["mini"]:
        assert _re.fullmatch(r"cuda-\d+\.\d+-mini", c["mini_base_tag"]), (
            f"{c['key']}: mini_base_tag {c['mini_base_tag']!r} does not fit "
            f"cuda-<major>.<minor>-mini, so cuda_minor cannot be derived")


# --- the end state ---------------------------------------------------------

def test_no_workflow_still_hardcodes_the_lists():
    """Met 2026-08-07: all nine arrays across the three workflows now read the
    table. The round-trip tests above skip themselves now that the arrays are gone;
    test_wired_jq_reproduces_the_table below is what guards the wiring instead."""
    stale = []
    for name in ("build-pytorch.yml", "extend-pytorch.yml", "promote-pytorch.yml"):
        t = (W / name).read_text()
        if re.search(r"^\s*ALL_(CONFIGS|MINI_CONFIGS|MULTI_CONFIGS)=\(", t, re.M):
            stale.append(name)
    assert not stale, f"still hardcoding the pytorch matrices: {stale}"


# --- the wiring itself ------------------------------------------------------

def _minor2(c):
    return c["mini_base_tag"].removeprefix("cuda-").removesuffix("-mini")


def _short2(c):
    return c["cuda_ver"].split("-")[0]


EXPECTED = {
    ("build-pytorch.yml", "ALL_CONFIGS"): lambda T: [
        f"{c['torch']}|{c['key']}|{c['cuda_ver']}|{c['backend']}|"
        f"{','.join(c['arches'])}|{' '.join(c['python_versions'])}" for c in T["configs"]],
    ("build-pytorch.yml", "ALL_MINI_CONFIGS"): lambda T: [
        f"{c['torch']}|{c['key']}|{c['mini_base_tag']}|{c['backend']}|"
        f"{','.join(c['arches'])}|{' '.join(c['python_versions'])}" for c in T["mini"]],
    ("build-pytorch.yml", "ALL_MULTI_CONFIGS"): lambda T: [
        f"{c['key']}|{c['primary_torch']}|{c['primary_backend']}|{c['cuda_minor']}|"
        f"{','.join(c['arches'])}|{' '.join(c['python_versions'])}" for c in T["multi"]],
    ("extend-pytorch.yml", "ALL_CONFIGS"): lambda T: [
        f"{c['torch']}|{c['key']}|{_short2(c)}|{','.join(c['arches'])}|"
        f"{' '.join(c['python_versions'])}" for c in T["configs"]],
    ("extend-pytorch.yml", "ALL_MINI_CONFIGS"): lambda T: [
        f"{c['torch']}|{c['key']}|{c['backend']}|{_minor2(c)}|{','.join(c['arches'])}|"
        f"{' '.join(c['python_versions'])}" for c in T["mini"]],
    ("extend-pytorch.yml", "ALL_MULTI_CONFIGS"): lambda T: [
        f"{c['key']}|{c['primary_torch']}|{c['primary_backend']}|{c['cuda_minor']}|"
        f"{','.join(c['arches'])}|{' '.join(c['python_versions'])}" for c in T["multi"]],
    ("promote-pytorch.yml", "ALL_CONFIGS"): lambda T: [
        f"{c['torch']}|{c['key']}|{_short2(c)}|{' '.join(c['python_versions'])}"
        for c in T["configs"]],
    ("promote-pytorch.yml", "ALL_MINI_CONFIGS"): lambda T: [
        f"{c['torch']}|{c['key']}|{c['backend']}|{_minor2(c)}|"
        f"{' '.join(c['python_versions'])}" for c in T["mini"]],
    ("promote-pytorch.yml", "ALL_MULTI_CONFIGS"): lambda T: [
        f"{c['key']}|{c['primary_torch']}|{c['primary_backend']}|{c['cuda_minor']}|"
        f"{' '.join(c['python_versions'])}" for c in T["multi"]],
}


@pytest.mark.parametrize("fname,arr", sorted(EXPECTED))
def test_wired_jq_reproduces_the_table(table, fname, arr):
    """RUN the committed jq and compare its output to the table.

    The extraction was verified once against the arrays it replaced; this keeps it
    verified. A jq expression is easy to edit into something that still parses and
    silently drops a field — e.g. losing the arch join, which would build one arch
    instead of two with no error anywhere.
    """
    import shutil
    import subprocess
    if not shutil.which("jq"):
        pytest.skip("jq not available")
    text = (W / fname).read_text()
    m = re.search(rf"""mapfile -t {arr} < <\(jq -r '(.*?)' "\$GITHUB_WORKSPACE/configs/pytorch\.json"\)""", text)
    assert m, f"{fname}:{arr} is not wired to the table"
    got = subprocess.run(["jq", "-r", m.group(1), str(CONFIG)],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    assert got == EXPECTED[(fname, arr)](table)


@pytest.mark.parametrize("fname,arr", sorted(EXPECTED))
def test_wired_read_fails_closed_on_an_empty_table(fname, arr):
    """`mapfile < <(jq ...)` does not propagate jq's exit status and succeeds on
    empty input, so a missing or renamed table would yield an empty matrix and a
    GREEN run that built nothing."""
    text = (W / fname).read_text()
    guard = f'[ "${{#{arr}[@]}}" -gt 0 ]'
    assert guard in text, f"{fname}:{arr} has no non-empty guard after the mapfile"
