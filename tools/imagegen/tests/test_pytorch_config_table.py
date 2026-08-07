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


# --- the end state ---------------------------------------------------------

@pytest.mark.xfail(reason="migration in progress: workflows still carry inline arrays",
                   strict=False)
def test_no_workflow_still_hardcodes_the_lists():
    """The goal. Flips to passing as each workflow is wired to the table; drop the
    xfail once all three are done, and delete the round-trip tests above with the
    arrays they check."""
    stale = []
    for name in ("build-pytorch.yml", "extend-pytorch.yml", "promote-pytorch.yml"):
        t = (W / name).read_text()
        if re.search(r"^\s*ALL_(CONFIGS|MINI_CONFIGS|MULTI_CONFIGS)=\(", t, re.M):
            stale.append(name)
    assert not stale, f"still hardcoding the pytorch matrices: {stale}"
