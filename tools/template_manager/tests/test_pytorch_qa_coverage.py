"""The pytorch QA matrix covers every mini artifact the build produces.

ADR 0021 cond 7. The gated set is DERIVED from the config table, never
hand-maintained: a cell list written by hand is correct on the day it is written
and silently wrong at the next table edit, which is the exact drift the
config-table extraction was done to remove.

The assertion is EQUALITY, not containment. Containment would let a new mini
config or a new python enter the build without entering the gate — the artifact
would ship untested while the matrix still looked complete, because nothing
names what is missing.

These tests execute the workflow's own jq against the real table rather than
re-implementing the selection in Python. A reimplementation would be a second
copy of the rule, and the two would agree right up until the moment one of them
was wrong.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
TABLE = json.loads((REPO / "configs/pytorch.json").read_text())
WORKFLOW = REPO / ".github/workflows/promote-pytorch.yml"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not available")


def _plan_step() -> str:
    wf = yaml.safe_load(WORKFLOW.read_text())
    for s in wf["jobs"]["resolve-digests"]["steps"]:
        if s.get("id") == "plan":
            return s["run"]
    raise AssertionError("resolve-digests has no `plan` step")


def _jq_matrix_program() -> str:
    """The jq program the workflow uses to turn the manifest into a matrix.

    Extracted from the step rather than copied, so this test breaks if the
    selection rule changes — which is the point of testing it at all.
    """
    body = _plan_step()
    start = body.index("jq -c --arg dpy")
    prog = body[body.index("'", start) + 1:]
    return prog[:prog.index("' manifest.json")]


def _fake_manifest() -> dict:
    """A manifest as resolve-digests would build it if every staging tag existed.

    Digests are synthetic and unique per artifact; only their presence and
    distinctness matter to the selection.
    """
    m = {"configs": [], "mini": [], "multi": []}
    for c in TABLE["configs"]:
        m["configs"].append({
            "torch": c["torch"], "key": c["key"],
            "cuda_short": c["cuda_ver"].split("-")[0],
            "digests": {f"py{p}": f"sha256:cfg-{c['torch']}-{c['key']}-{p}"
                        for p in c["python_versions"]},
        })
    for c in TABLE["mini"]:
        minor = c["mini_base_tag"].removeprefix("cuda-").removesuffix("-mini")
        m["mini"].append({
            "torch": c["torch"], "key": c["key"], "backend": c["backend"],
            "cuda_minor": minor,
            "digests": {f"py{p}": f"sha256:mini-{c['torch']}-{c['key']}-{p}"
                        for p in c["python_versions"]},
        })
    for c in TABLE["multi"]:
        m["multi"].append({
            "key": c["key"], "cuda_minor": c["cuda_minor"],
            # a space-joined STRING, exactly as resolve-digests stores it —
            # not a list. The jq splits it; a list here would let a broken
            # program pass the test and fail in CI.
            "venvs": " ".join(c["venvs"]),
            "primary_torch": c["primary_torch"],
            "primary_backend": c["primary_backend"],
            "digests": {f"py{p}": f"sha256:multi-{c['key']}-{p}"
                        for p in c["python_versions"]},
        })
    return m


def _audio_versions() -> str:
    """Torch versions that ship torchaudio, derived exactly as the workflow does.

    Reproduced rather than hardcoded: the workflow reads it from
    torch-companions.json, so a companion change moves both together.
    """
    comp = json.loads((REPO / "derivatives/pytorch/torch-companions.json").read_text())
    return " ".join(v for v, e in comp.items() if "torchaudio" in e.get("packages", {}))


def build_matrix(tmp_path: Path, manifest: dict | None = None) -> list[dict]:
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps(manifest if manifest is not None else _fake_manifest()))
    out = subprocess.run(
        ["jq", "-c", "--arg", "dpy", f"py{TABLE['default_python']}",
         "--arg", "audio", _audio_versions(),
         _jq_matrix_program(), str(man)],
        capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


# --- the equality that ADR 0021 cond 7 requires ----------------------------

def test_every_mini_artifact_the_build_produces_is_gated(tmp_path):
    """THE assertion. Not containment: a mini artifact absent from the matrix
    ships untested, and nothing else in the system would name it."""
    cells = build_matrix(tmp_path)
    gated = {c["cell"] for c in cells if c["kind"] == "mini"}
    expected = {f"mini-{c['torch']}-{c['key']}-py{p}"
                for c in TABLE["mini"] for p in c["python_versions"]}
    assert gated == expected, (
        f"gate/build mismatch — ungated: {sorted(expected - gated)}; "
        f"gated but not built: {sorted(gated - expected)}")


def test_the_mini_count_is_every_python_of_every_config(tmp_path):
    """Guards against a selection that silently collapses to default python —
    the sampling shape ADR 0021 rejected, which would still satisfy a
    per-config check."""
    cells = build_matrix(tmp_path)
    want = sum(len(c["python_versions"]) for c in TABLE["mini"])
    got = len([c for c in cells if c["kind"] == "mini"])
    assert got == want, f"{got} mini cells for {want} mini artifacts"


def test_a_new_python_in_the_table_enters_the_gate(tmp_path):
    """The drift this exists to catch, simulated: adding a python to a mini
    config must produce a cell without anyone editing a cell list."""
    man = _fake_manifest()
    man["mini"][0]["digests"]["py315"] = "sha256:mini-new-python"
    cells = build_matrix(tmp_path, man)
    assert any(c["cell"].endswith("-py315") for c in cells if c["kind"] == "mini"), \
        "a new python appeared in the build and produced no QA cell"


def test_a_new_mini_config_enters_the_gate(tmp_path):
    man = _fake_manifest()
    man["mini"].append({"torch": "9.9.9", "key": "cu999-mini", "backend": "cu999",
                        "cuda_minor": "99.9",
                        "digests": {"py312": "sha256:mini-brand-new"}})
    cells = build_matrix(tmp_path, man)
    assert any("9.9.9" in c["cell"] for c in cells), \
        "a new mini config appeared in the build and produced no QA cell"


# --- the auto surface is the POINTER surface, not every config -------------

def test_the_auto_cells_are_one_per_backend_at_default_python(tmp_path):
    """8 -auto tags resolve to 3 distinct images. Gating all 17 configs would
    certify artifacts no pointer points at; gating fewer than one per backend
    would leave a pointer surface untested."""
    cells = build_matrix(tmp_path)
    auto = [c for c in cells if c["kind"] == "auto"]
    backends = {c["key"] for c in TABLE["configs"]}
    assert {c["cell"] for c in auto} == {f"auto-{b}" for b in backends}


def test_each_auto_cell_tests_the_newest_torch_for_its_backend(tmp_path):
    """The -auto tag resolves to the newest torch carrying that backend, so
    testing any older one would certify bits the tag does not serve. Version
    comparison must be NUMERIC — a lexical max makes 2.9.1 beat 2.12.0."""
    cells = {c["cell"]: c for c in build_matrix(tmp_path) if c["kind"] == "auto"}
    for backend in {c["key"] for c in TABLE["configs"]}:
        torches = [c["torch"] for c in TABLE["configs"] if c["key"] == backend]
        newest = max(torches, key=lambda v: [int(x) for x in v.split(".")])
        assert cells[f"auto-{backend}"]["describe"].startswith(newest + "-"), (
            f"{backend}: gated {cells[f'auto-{backend}']['describe']}, "
            f"but the auto tag serves torch {newest}")


def test_the_lexical_version_trap_is_actually_avoided(tmp_path):
    """Explicit regression for the above, DERIVED rather than pinned.

    A string max over e.g. ["2.9.1", "2.13.0"] picks "2.9.1", because "9" > "1"
    lexically. This finds every backend where the lexical and numeric answers
    actually DIFFER — i.e. where the bug would show — and asserts the gate took
    the numeric one.

    Written this way because the first version hardcoded "cu130 -> 2.12.0" and
    broke the moment torch 2.13.0 entered the table. A test that pins today's
    data rots into a false failure and trains people to edit tests until they
    pass. The invariant is "numeric beats lexical", not "cu130 is on 2.12.0".
    """
    cells = {c["cell"]: c for c in build_matrix(tmp_path) if c["kind"] == "auto"}
    numeric = lambda v: [int(x) for x in v.split(".")]

    trapped = []
    for backend in {c["key"] for c in TABLE["configs"]}:
        vers = [c["torch"] for c in TABLE["configs"] if c["key"] == backend]
        if max(vers) != max(vers, key=numeric):
            trapped.append(backend)
            assert cells[f"auto-{backend}"]["describe"].startswith(
                max(vers, key=numeric) + "-"), (
                f"{backend}: gated {cells[f'auto-{backend}']['describe']}, but "
                f"lexical max is {max(vers)} and numeric max is "
                f"{max(vers, key=numeric)} — the selection sorted as strings")

    assert trapped, (
        "no backend in the table currently distinguishes lexical from numeric "
        "ordering, so this test proves nothing. Add a version pair that does "
        "(e.g. 2.9.x alongside 2.1x.y), or delete this test — do not leave it "
        "passing vacuously.")


# --- multi -----------------------------------------------------------------

def test_the_multi_alias_is_gated_and_declares_its_venvs(tmp_path):
    """multi carries the family's only non-auto mutable tag and is the AIO
    studio base. Its venv list is what makes a MISSING venv detectable —
    05-venv-manifest.sh has nothing to compare against without it."""
    cells = [c for c in build_matrix(tmp_path) if c["kind"] == "multi"]
    assert cells, "no multi variant is gated"
    # EVERY variant, matched by key — not TABLE["multi"][0]. The single-variant
    # assumption held until 2026-08-11, when cu130 and cu132 variants were added;
    # indexing [0] would have silently checked one and ignored the rest.
    by_key = {m["key"]: m for m in TABLE["multi"]}
    assert len(cells) == len(by_key), (
        f"{len(cells)} multi cells for {len(by_key)} variants — a variant is ungated")
    for c in cells:
        key = next(k for k in by_key if c["cell"].startswith(f"multi-{k}-py"))
        assert c["venvs"].split() == by_key[key]["venvs"], f"{key}: venv list mismatch"
        assert len(c["venvs"].split()) > 1, f"{key}: one venv is not a multi image"


def test_single_venv_cells_still_declare_an_expectation(tmp_path):
    """A cell with an empty EXPECTED_TORCH_VENVS makes 05-venv-manifest.sh skip,
    turning a required test into a guaranteed red (or, worse, a silent gap)."""
    for c in build_matrix(tmp_path):
        assert c["venvs"].strip(), f"{c['cell']} declares no expected venvs"


# --- cells must be usable as identifiers -----------------------------------

def test_cell_names_are_unique(tmp_path):
    """Cell names become matrix keys, evidence artifact names and registry tags.
    A collision would silently overwrite one cell's evidence with another's, and
    qa-summary would report a verdict for an artifact that was never tested."""
    cells = [c["cell"] for c in build_matrix(tmp_path)]
    dupes = {c for c in cells if cells.count(c) > 1}
    assert not dupes, f"duplicate cell names: {sorted(dupes)}"


def test_cell_names_are_valid_registry_tags(tmp_path):
    """Each becomes `qa-<run_id>-<cell>`. A tag may hold [A-Za-z0-9_.-] and is
    limited to 128 chars; an invalid one fails at crane copy, after the plan is
    already built."""
    import re
    for c in build_matrix(tmp_path):
        tag = f"qa-99999999999-{c['cell']}"
        assert re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*", tag), f"invalid tag: {tag}"
        assert len(tag) <= 128, f"tag too long ({len(tag)}): {tag}"


def test_an_artifact_missing_from_staging_produces_no_cell(tmp_path):
    """Fail-safe direction: an empty digest means the staging tag does not
    exist, and renting a box to test nothing would burn money and then report a
    confusing red. The promote job fails on the missing source separately."""
    man = _fake_manifest()
    man["mini"][0]["digests"]["py310"] = ""
    cells = build_matrix(tmp_path, man)
    gone = f"mini-{TABLE['mini'][0]['torch']}-{TABLE['mini'][0]['key']}-py310"
    assert gone not in {c["cell"] for c in cells}


# --- the auto cells must track the POINTER MAP, not a proxy for it ---------
#
# The gated auto set is derived from the config table's backends. The thing that
# actually decides customer exposure is AUTO_TAG_MAP in the promote job, which
# maps each `cuda-X.Y.Z-auto` tag to a backend. Those two agree today but nothing
# made them agree — and a new `-auto` tag pointing at a backend the gate does not
# cover would put an untested image on a customer-facing pointer.
#
# Measured 2026-08-10: the table builds 4 backends (cu126, cu128, cu129, cu130);
# AUTO_TAG_MAP references 3. cu129 is promoted but carries no auto tag, so it is
# gated as ordinary coverage rather than as pointer surface. That direction is
# safe. The reverse — a mapped backend with no cell — is not.

def _auto_tag_map() -> dict[str, str]:
    """{auto tag: backend} as the promote job actually defines it."""
    import re
    wf = yaml.safe_load(WORKFLOW.read_text())
    body = "\n".join(s.get("run", "") for s in wf["jobs"]["promote"]["steps"])
    block = body[body.index("AUTO_TAG_MAP=("):]
    block = block[:block.index(")")]
    return dict(re.findall(r'"([^"|]+)\|([^"]+)"', block))


def test_the_auto_tag_map_is_readable():
    """Guard the guard: if the extraction breaks, the test below passes over an
    empty map and asserts nothing."""
    m = _auto_tag_map()
    assert len(m) >= 3, f"only parsed {m} from AUTO_TAG_MAP"
    assert all(t.endswith("-auto") for t in m), m


def test_every_backend_an_auto_tag_points_at_is_gated(tmp_path):
    """THE pointer-surface assertion. An auto tag whose backend has no cell would
    serve customers an image this gate never booted."""
    gated = {c["cell"].removeprefix("auto-")
             for c in build_matrix(tmp_path) if c["kind"] == "auto"}
    mapped = set(_auto_tag_map().values())
    assert mapped <= gated, (
        f"auto tags point at backend(s) with no QA cell: {sorted(mapped - gated)}. "
        f"Those tags would move to an image this run never tested.")


def test_backends_without_an_auto_tag_are_still_gated(tmp_path):
    """cu129 today. It is promoted as dated tags, so it ships; it simply has no
    pointer. Gating it is the deliberate choice — the same reasoning that put
    mini in scope, since 'no auto tag' is not 'no consumer'."""
    gated = {c["cell"].removeprefix("auto-")
             for c in build_matrix(tmp_path) if c["kind"] == "auto"}
    table_backends = {c["key"] for c in TABLE["configs"]}
    assert gated == table_backends, (
        f"gate covers {sorted(gated)} but the table builds {sorted(table_backends)}")


# --- the required list follows what the image SHIPS ------------------------
#
# The first real gate run (31497277275) blocked 20 healthy artifacts on
# `pytorch.d/30-torchaudio=skipped`. torchaudio does not exist upstream past
# torch 2.11, so those images correctly do not carry it and the test correctly
# self-skips — the gate was right and the required list was wrong.

def _audio(cell):
    return "pytorch.d/30-torchaudio" in cell["require_tests"]


def test_torchaudio_is_required_exactly_where_the_image_ships_it(tmp_path):
    comp = json.loads((REPO / "derivatives/pytorch/torch-companions.json").read_text())
    has = {v for v, e in comp.items() if "torchaudio" in e.get("packages", {})}
    assert has and set(comp) - has, (
        "every version either has or lacks torchaudio, so this test cannot "
        "distinguish a working rule from one that always answers the same way")

    for c in build_matrix(tmp_path):
        if c["kind"] == "multi":
            continue                       # covered below; a multi spans versions
        ver = c["describe"].split("-")[0]
        assert _audio(c) == (ver in has), (
            f"{c['cell']}: torchaudio required={_audio(c)} but torch {ver} "
            f"{'ships' if ver in has else 'does not ship'} it")


def test_a_multi_requires_torchaudio_if_any_venv_ships_it(tmp_path):
    """30-torchaudio iterates every venv and only skips when NONE has it, so
    the requirement is an OR across the variant's versions — not a property of
    its primary alone."""
    comp = json.loads((REPO / "derivatives/pytorch/torch-companions.json").read_text())
    has = {v for v, e in comp.items() if "torchaudio" in e.get("packages", {})}
    by_key = {m["key"]: m for m in TABLE["multi"]}
    for c in build_matrix(tmp_path):
        if c["kind"] != "multi":
            continue
        key = next(k for k in by_key if c["cell"].startswith(f"multi-{k}-py"))
        vers = [by_key[key]["primary_torch"] if v == "main" else v.removeprefix("torch-")
                for v in by_key[key]["venvs"]]
        assert _audio(c) == any(v in has for v in vers), (
            f"{key}: venvs {vers}, required={_audio(c)}")


def test_every_cell_requires_the_gpu_trio_and_the_core_pytorch_tests(tmp_path):
    """Whatever the conditional part does, the unconditional part must survive:
    a per-cell list is also an opportunity to accidentally require nothing."""
    must = ["base/60-gpu-cuda", "base/61-cuda-compute", "base/62-gpu-libraries",
            "pytorch.d/05-venv-manifest", "pytorch.d/10-torch-core",
            "pytorch.d/20-torchvision", "pytorch.d/40-nccl-init"]
    for c in build_matrix(tmp_path):
        for t in must:
            assert t in c["require_tests"], f"{c['cell']} does not require {t}"


def test_no_cell_requires_the_multi_gpu_collectives_matrix(tmp_path):
    """41-nccl-collectives skips below 2 GPUs and every cell is single-GPU, so
    requiring it would fail all of them."""
    for c in build_matrix(tmp_path):
        assert "41-nccl-collectives" not in c["require_tests"], c["cell"]


# --- the architecture ceiling (run 31591093625) ----------------------------
#
# cuda_max_good bounds the host DRIVER from below. Nothing bounded the GPU
# ARCHITECTURE from above, so cu126 cells rented Blackwell cards (sm_120) whose
# wheels contain no kernels for them: cudaErrorNoKernelImageForDevice, torch
# matmul and conv2d failing, reproducible across six different RTX 5070 offers.
# The image was fine — it was being tested on hardware it is never served,
# because a Blackwell host needs a 12.8+ driver and so is never matched by the
# 12.x auto tags that point at cu126.

BLACKWELL_LESS = {"cu126"}      # backends whose wheels stop at sm_90


def _backend_of(cell):
    if cell["kind"] == "auto":
        return cell["cell"].removeprefix("auto-")
    for b in ("cu126", "cu128", "cu129", "cu130", "cu132"):
        if f"-{b}-" in cell["describe"] or cell["describe"].startswith(f"{b}-"):
            return b
    return None


def test_cu126_cells_cap_the_gpu_architecture(tmp_path):
    for c in build_matrix(tmp_path):
        if _backend_of(c) in BLACKWELL_LESS:
            assert "compute_cap.lte=900" in c["cap"], (
                f"{c['cell']} can rent a Blackwell card its wheels have no "
                f"kernels for — the failure looks like a bad image and is not")


def test_blackwell_capable_backends_are_not_capped(tmp_path):
    """The cap costs offers, so it must not spread to backends that do not need
    it. cu128 was the first with Blackwell kernels."""
    for c in build_matrix(tmp_path):
        b = _backend_of(c)
        if b and b not in BLACKWELL_LESS:
            assert c["cap"] == "", f"{c['cell']} ({b}) is capped but supports Blackwell"


def test_the_cap_is_a_tightening_not_a_widening(tmp_path):
    """create.py's --set-filter is raise-only: `lte` lowers a ceiling, which
    tightens. A `gte` here would try to WIDEN and be rejected at rent time —
    after the plan is built."""
    for c in build_matrix(tmp_path):
        if c["cap"]:
            assert ".lte=" in c["cap"], f"{c['cell']}: cap {c['cap']!r} does not tighten"


def test_every_cell_declares_a_cap_field(tmp_path):
    """Absent would interpolate as the literal string 'null' into set_filters
    and fail create.py's parser at rent time."""
    for c in build_matrix(tmp_path):
        assert "cap" in c and isinstance(c["cap"], str), c["cell"]


def test_set_filters_concatenation_is_well_formed(tmp_path):
    """The workflow builds `cuda_max_good.gte=X${cap}` and the gate splits on
    whitespace. A missing leading space would glue the two filters together."""
    for c in build_matrix(tmp_path):
        spec = f"cuda_max_good.gte={c['floor']}{c['cap']}"
        parts = spec.split()
        assert all("=" in p and "." in p.split("=")[0] for p in parts), spec
        assert len(parts) == (2 if c["cap"] else 1), spec


# --- cu129 is retired ------------------------------------------------------

def test_cu129_is_gone(tmp_path):
    """Retired: no auto tag, no mini, no derivative pin — it served nothing, and
    its torchvision has no Blackwell kernels either. Its torch versions all
    remain on other backends."""
    assert "cu129" not in {c["key"] for c in TABLE["configs"]}
    assert all("cu129" not in c["cell"] for c in build_matrix(tmp_path))
