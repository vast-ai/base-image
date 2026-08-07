"""configs/base-image.json is the single source of truth for the base matrices (ADR 0019).

The table used to live inline in build-, promote- AND extend-base-image.yml. A patch
bump had to be applied three times and a miss was silent — the 13.3.0 -> 13.3.1 bump
found exactly that: two copies were known about, the third was not.

These tests pin three properties:
  1. the workflows read the file rather than carrying their own copy;
  2. the table can still express every field each workflow needs;
  3. the auto-tag name remains DERIVABLE from tag_template, because that name is
     customer-facing and a second stored copy is how one silently stops updating.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CONFIG = REPO / "configs/base-image.json"
WORKFLOWS = {
    "build": REPO / ".github/workflows/build-base-image.yml",
    "promote": REPO / ".github/workflows/promote-base-image.yml",
    "extend": REPO / ".github/workflows/extend-base-image.yml",
}

# The auto tag is derived in promote with this expression; the test asserts the
# derivation still yields a version for every CUDA config. Kept in sync by
# test_promote_still_derives_the_auto_tag_with_this_expression below.
_AUTO_RE = re.compile(r"^cuda-([0-9.]+)-")


@pytest.fixture(scope="module")
def cfg():
    return json.loads(CONFIG.read_text())


def test_config_file_parses_and_is_non_empty(cfg):
    assert cfg["configs"], "no configs — every matrix would be empty"
    assert cfg["mini"], "no mini configs"
    assert cfg["python_versions"] and cfg["mini_python_versions"]


def test_every_config_has_the_fields_all_three_workflows_consume(cfg):
    for c in cfg["configs"]:
        for field in ("key", "base_image", "tag_template", "arches", "default_python"):
            assert c.get(field), f"{c.get('key')} missing {field}"
        assert isinstance(c["arches"], list) and c["arches"]
        assert c["default_python"] in cfg["python_versions"], (
            f"{c['key']} default_python {c['default_python']} is not built"
        )
    for m in cfg["mini"]:
        for field in ("key", "mini_tag", "cuda_versions"):
            assert m.get(field), f"{m.get('key')} missing {field}"


def test_keys_and_tag_templates_are_unique(cfg):
    """A duplicate key would silently build one config twice and, at promote,
    write the same prod tag from two sources."""
    keys = [c["key"] for c in cfg["configs"]]
    tags = [c["tag_template"] for c in cfg["configs"]]
    assert len(keys) == len(set(keys)), f"duplicate config keys: {keys}"
    assert len(tags) == len(set(tags)), f"duplicate tag_templates: {tags}"


def test_no_pipe_character_in_any_field(cfg):
    """The workflows read the table as pipe-delimited lines via jq, so a literal
    '|' in a value would silently shift every field after it."""
    for c in cfg["configs"] + cfg["mini"]:
        for k, v in c.items():
            vals = v if isinstance(v, list) else [v]
            for val in vals:
                assert "|" not in str(val), f"{c.get('key')}.{k} contains a pipe: {val!r}"


# --- the property that keeps the customer-facing auto tag honest -------------

def test_auto_tag_version_is_derivable_for_every_cuda_config(cfg):
    """Every cuda-* config's tag_template must yield a version.

    promote derives `cuda-<ver>-auto` from tag_template with a sed. A config whose
    template stopped matching would be skipped by that derivation with no error —
    its auto tag would simply stop being updated, which is invisible until a
    customer reports stale bits. This is also why the version is NOT stored as a
    field: one source of truth, derived at the point of use.
    """
    for c in cfg["configs"]:
        if not c["tag_template"].startswith("cuda-"):
            continue
        m = _AUTO_RE.match(c["tag_template"])
        assert m and m.group(1), (
            f"{c['key']}: tag_template {c['tag_template']!r} yields no auto-tag version"
        )


def test_stock_configs_yield_no_auto_tag(cfg):
    """stock-* have no CUDA version and therefore no auto tag; the promote loop
    `continue`s on them. Pinned so a rename never accidentally mints one."""
    for c in cfg["configs"]:
        if c["key"].startswith("stock"):
            assert not _AUTO_RE.match(c["tag_template"]), (
                f"{c['key']} would now mint an auto tag"
            )


def test_promote_still_derives_the_auto_tag_with_this_expression():
    """If promote's sed changes, the assertions above stop describing reality."""
    t = WORKFLOWS["promote"].read_text()
    assert r"sed -n 's/^cuda-\([0-9.]*\)-.*/\1/p'" in t, (
        "promote's auto-tag derivation changed — update _AUTO_RE to match"
    )


# --- the workflows must consume the table, not carry a copy ------------------

@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_workflow_reads_the_shared_table(name):
    t = WORKFLOWS[name].read_text()
    assert "configs/base-image.json" in t, f"{name} does not read the shared config table"


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_workflow_has_no_inline_config_array(name):
    """The drift this whole extraction exists to remove."""
    t = WORKFLOWS[name].read_text()
    for arr in ("CONFIGS=(", "ALL_CONFIGS=(", "MINI_CONFIGS=("):
        assert arr not in t, f"{name} still declares an inline {arr.rstrip('=(')} array"


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_matrix_job_checks_out_the_repo(name):
    """These jobs had no checkout — reading a file from the repo needs one, and
    without it the run fails at jq with a confusing 'no such file'."""
    t = WORKFLOWS[name].read_text()
    job = "generate-configs" if name == "promote" else "generate-matrix"
    after = t.split(f"\n  {job}:", 1)[1]
    # A job block runs until the next top-level job key: a line indented exactly
    # two spaces followed by a non-space. Splitting on a bare "\n  " would cut at
    # the first 4-space-indented line inside the block.
    block = re.split(r"\n  (?=\S)", after, maxsplit=1)[0]
    assert "actions/checkout" in block, f"{name}'s {job} job does not check out the repo"


def test_config_count_matches_the_documented_fleet(cfg):
    """A tripwire, not a rule: the fleet is 12 configs + 2 mini. Changing it is
    fine and expected — update this number deliberately so the change is noticed
    in review rather than slipping in."""
    assert len(cfg["configs"]) == 12
    assert len(cfg["mini"]) == 2


# --- the shared table must actually be load-bearing -------------------------

def test_python_versions_are_read_from_the_table_not_hardcoded():
    """`python_versions` was in the shared config but no workflow read it: five
    hardcoded arrays in build-base-image.yml were the real source. A field the
    table declares but nothing consumes is worse than no field — it reads as
    authoritative and isn't."""
    t = (REPO / ".github/workflows/build-base-image.yml").read_text()
    import re
    stale = re.findall(r'^\s*(?:MINI_)?PYTHON_VERSIONS=\(', t, re.M)
    assert not stale, f"{len(stale)} hardcoded python array(s) still shadow the shared table"
    assert t.count("jq -r '.python_versions[]'") == 3
    assert t.count("jq -r '.mini_python_versions[]'") == 2


def test_every_job_that_reads_the_table_checks_out():
    """`$GITHUB_WORKSPACE/configs/base-image.json` is empty without a checkout, and
    jq on a missing file yields an empty array — a silent no-op build, not an error."""
    import yaml
    for wf_name in ("build-base-image.yml", "promote-base-image.yml", "extend-base-image.yml"):
        wf = yaml.safe_load((REPO / ".github/workflows" / wf_name).read_text())
        for name, job in wf["jobs"].items():
            body = "\n".join(str(s.get("run", "")) for s in job.get("steps", []))
            if "configs/base-image.json" not in body:
                continue
            uses = " ".join(str(s.get("uses", "")) for s in job["steps"])
            assert "actions/checkout" in uses, f"{wf_name}:{name} reads the table without a checkout"


# --- adding a new CUDA version must not silently mis-floor QA ---------------

def test_every_cuda_major_in_the_table_has_a_driver_floor():
    """A new CUDA major added to configs/base-image.json needs a matching branch in
    promote's floor `case`. Without one it used to inherit 11.8, letting QA rent a
    host whose driver only supports CUDA 11.8 to test, say, a CUDA 14 image — which
    fails closed but presents as a broken IMAGE, not a missing config line.

    This test is the reason the runbook can be trusted: the step it tells you not
    to forget is enforced, not merely written down.
    """
    import json
    import re
    cfg = json.loads(CONFIG.read_text())
    majors = set()
    for c in cfg["configs"]:
        m = re.match(r"^cuda-(\d+)\.", c["tag_template"])
        if m:
            majors.add(m.group(1))
    assert majors, "no cuda configs found — fixture is wrong"

    wf = (REPO / ".github/workflows/promote-base-image.yml").read_text()
    block = wf.split('case "$auto" in', 1)[1].split("esac", 1)[0]
    mapped = set(re.findall(r"^\s*(\d+)\.\*\)", block, re.M))
    missing = majors - mapped
    assert not missing, (
        f"CUDA major(s) {sorted(missing)} are in the config table but have no "
        f"driver floor branch. Add '<major>.*) floor=X ;;' to promote-base-image.yml.")


def test_the_floor_case_fails_closed_on_an_unmapped_major():
    """The default branch must exit, not pick a floor. A silent default is how the
    above becomes invisible again."""
    import re
    wf = (REPO / ".github/workflows/promote-base-image.yml").read_text()
    block = wf.split('case "$auto" in', 1)[1].split("esac", 1)[0]
    # the catch-all is a bare `*)` at the start of a line — `11.*)` also contains
    # the substring, so anchor on the line rather than splitting on it
    m = re.search(r"^\s*\*\)(.*)$", block, re.S | re.M)
    assert m, "no catch-all branch found in the floor case statement"
    default = m.group(1)
    assert "exit 1" in default, "the unmapped-major branch does not fail"
    assert not re.search(r"^\s*floor=", default, re.M), (
        "the unmapped-major branch still assigns a floor — it must refuse instead")


def test_the_runbook_exists_and_names_checks_that_are_real():
    """A runbook is only trustworthy if the commands and tests it cites exist. This
    catches the ordinary rot of a procedure doc drifting from the code it describes
    — which matters more than usual here, because the runbook's whole claim is that
    its rules are enforced rather than remembered."""
    import re
    rb = REPO / "docs/runbooks/new-cuda-version.md"
    assert rb.exists(), "the new-CUDA runbook is missing"
    text = rb.read_text()

    # every test name it cites must exist somewhere in the suites
    cited = set(re.findall(r"`(test_[a-z0-9_]+)`", text))
    assert cited, "runbook cites no tests — it has become prose"
    have = ""
    for d in ("tools/imagegen/tests", "tools/template_manager/tests"):
        for f in (REPO / d).glob("test_*.py"):
            have += f.read_text()
    missing = sorted(n for n in cited if f"def {n}(" not in have)
    assert not missing, f"runbook cites tests that do not exist: {missing}"

    # every repo path it cites must exist
    paths = set(re.findall(r"`((?:configs|templates|tools|docs|derivatives|\.github)/[^`\s]+)`", text))
    absent = sorted(p for p in paths if not (REPO / p).exists())
    assert not absent, f"runbook cites paths that do not exist: {absent}"


def test_the_runbook_is_reachable_from_the_context_map():
    """An unlinked runbook is one nobody finds when it matters."""
    cm = (REPO / "docs/context-map.md").read_text()
    assert "runbooks/new-cuda-version" in cm, (
        "the runbook is not referenced from docs/context-map.md")


# --- L058: the disk floor must FILTER, not just size the request ------------

def _lint_base(tmp_path, mutate):
    """Run the linter over a scratch copy of the repo with base-qa mutated."""
    import shutil
    import subprocess
    import sys
    dst = tmp_path / "repo"
    if not dst.exists():
        shutil.copytree(REPO, dst, symlinks=True, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", "test", "pcl"))
    tpl = dst / "images/base/base-image/templates/base-qa/template.yml"
    if not tpl.exists():
        tpl = next(dst.glob("**/templates/base-qa/template.yml"))
    tpl.write_text(mutate(tpl.read_text()))
    r = subprocess.run([sys.executable, "-c",
                        "import sys;sys.argv=['imagegen','lint','--all'];"
                        "from imagegen.cli import main;sys.exit(main())"],
                       cwd=dst / "tools/imagegen", capture_output=True, text=True,
                       env={"PYTHONPATH": ".", "PATH": "/usr/bin:/bin"})
    return r.stdout + r.stderr


def test_L058_fires_when_the_disk_floor_is_removed(tmp_path):
    """The defect as found: recommended_disk_space set, no disk_space filter, so
    offers are never filtered on disk and a 9 GB box is selectable even though the
    client will request 64 GB of overlay and reject anything short of it."""
    out = _lint_base(tmp_path, lambda t: t.replace("  disk_space:\n    gte: 64\n", ""))
    assert "L058" in out, f"L058 did not fire on a missing disk floor:\n{out[-1500:]}"


def test_L058_fires_when_the_floor_is_below_the_request(tmp_path):
    """The subtle case: a floor exists, so the axis looks covered, but it still
    admits boxes that cannot satisfy what the client then requests."""
    out = _lint_base(tmp_path, lambda t: t.replace("  disk_space:\n    gte: 64",
                                                   "  disk_space:\n    gte: 20"))
    assert "L058" in out, f"L058 did not fire on an inadequate floor:\n{out[-1500:]}"
    assert "below" in out


def test_L058_is_satisfied_by_the_committed_template(tmp_path):
    """Guards against a rule that fires on everything."""
    out = _lint_base(tmp_path, lambda t: t)
    assert "L058" not in out, f"L058 fires on the committed template:\n{out[-1500:]}"
