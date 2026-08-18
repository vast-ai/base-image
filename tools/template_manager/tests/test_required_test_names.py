"""Every required test name resolves to a real, runnable test (ADR 0019/0021).

`INSTANCE_TEST_REQUIRE_PASS` is a list of strings in a YAML file, compared at
runtime against names the runner derives from the filesystem. Nothing checks
that the two agree until a cell is already running on a rented box.

The trap that motivated this file: derivative tests live in `pytorch.d/`, and
runner.sh names a test by its path relative to the tests dir minus `.sh` — so
the correct name is `pytorch.d/10-torch-core`, NOT `pytorch/10-torch-core`. The
`.d` is easy to drop and the mistake is invisible in review.

It fails CLOSED (the runner reports "missing from this image" and the cell
blocks), so it is not a safety hole — but it would be a systematic false red on
every cell of a 56-cell matrix, which under ADR 0021's whole-run block means no
pytorch promotion at all until someone noticed.

These tests derive the expected names from the runner's own rule rather than
restating the list, so a template and the files it names cannot drift apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
TEMPLATES = REPO / "templates"

# Where a test file may live. Mirrors runner.sh's discovery: base/ from the base
# overlay, plus any *.d/ directory a derivative drops in.
TEST_ROOTS = [REPO / "ROOT/opt/instance-tools/tests"] + sorted(
    (REPO / "derivatives").glob("*/ROOT/opt/instance-tools/tests"))


def runner_test_names() -> dict[str, Path]:
    """Every name the runner would produce, mapped to its file.

    runner.sh: `local_name="${test_path#"${TESTS_DIR}/"}"; local_name="${local_name%.sh}"`
    — i.e. the path relative to the tests dir, minus the extension. Reproduced
    here rather than assumed, so the `.d` convention is derived, not restated.
    """
    out: dict[str, Path] = {}
    for root in TEST_ROOTS:
        if not root.is_dir():
            continue
        for sub in sorted(root.iterdir()):
            if not sub.is_dir():
                continue
            if sub.name != "base" and not sub.name.endswith(".d"):
                continue
            for f in sorted(sub.glob("*.sh")):
                out[f"{sub.name}/{f.stem}"] = f
    return out


def gating_templates() -> list[tuple[str, list[str]]]:
    """(template path, required test names) for every template declaring some."""
    found = []
    for tpl in sorted(TEMPLATES.rglob("template.yml")):
        data = yaml.safe_load(tpl.read_text(encoding="utf-8"))
        for entry in (data if isinstance(data, list) else [data]):
            if not isinstance(entry, dict):
                continue
            env = entry.get("env") or {}
            declared = env.get("INSTANCE_TEST_REQUIRE_PASS", "") if isinstance(env, dict) else ""
            names = str(declared).replace(",", " ").split()
            if names:
                found.append((str(tpl.relative_to(REPO)), names))
    return found


def test_there_are_gating_templates_to_check():
    """Guard the guard: an empty list would make every test below vacuous."""
    assert gating_templates(), "no template declares INSTANCE_TEST_REQUIRE_PASS"


def test_the_runner_naming_rule_produces_dot_d_names():
    """Pins the specific thing that is easy to get wrong. If this ever stops
    holding, the templates need updating in the same commit."""
    names = runner_test_names()
    assert any(n.startswith("pytorch.d/") for n in names), (
        "no pytorch.d/* test names — either the derivative tests moved or the "
        "naming rule changed; the templates say pytorch.d/ and would now be wrong")
    assert "pytorch.d/10-torch-core" in names
    assert "pytorch/10-torch-core" not in names, (
        "the runner would NOT produce this name; a template using it fails closed")


@pytest.mark.parametrize("tpl,names", gating_templates(),
                         ids=[t for t, _ in gating_templates()])
def test_every_required_test_exists(tpl, names):
    """The whole point: a required name that no file answers to blocks the cell."""
    known = runner_test_names()
    missing = [n for n in names if n not in known]
    assert not missing, (
        f"{tpl} requires {missing}, which the runner will never report. "
        f"It fails closed, but every cell using this template goes red.")


@pytest.mark.parametrize("tpl,names", gating_templates(),
                         ids=[t for t, _ in gating_templates()])
def test_every_required_test_is_executable(tpl, names):
    """runner.sh discovers with `find -executable`. A required test whose mode
    bit is missing is never run, so it reports as absent — same red, and the
    cause is one no amount of reading the script would reveal."""
    known = runner_test_names()
    not_exec = [n for n in names if n in known and not (known[n].stat().st_mode & 0o111)]
    assert not not_exec, f"{tpl}: required but not executable: {not_exec}"


@pytest.mark.parametrize("tpl,names", gating_templates(),
                         ids=[t for t, _ in gating_templates()])
def test_no_required_test_skips_unconditionally_on_one_gpu(tpl, names):
    """A required test that always skips is a guaranteed red, the mirror image
    of a test that always passes (L059). The concrete case: every cell in ADR
    0021's matrix is single-GPU, so requiring the multi-GPU collectives matrix
    would fail all 56 — and under a whole-run block, stop every promotion."""
    known = runner_test_names()
    for n in names:
        if n not in known:
            continue
        text = known[n].read_text(encoding="utf-8", errors="replace")
        assert "need >= 2 GPUs" not in text, (
            f"{tpl} requires {n}, which skips itself below 2 GPUs. Every cell in "
            f"this matrix is single-GPU, so this would fail every one of them.")
