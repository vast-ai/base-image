"""Guard: every config in the table must resolve a driver floor in promote.

`resolve-digests` derives a per-config CUDA floor from the tag template and
FAILS CLOSED on anything it does not recognise — correct, because a new CUDA
major silently inheriting 11.8 would let QA rent a host whose driver cannot run
the image, and that presents as a broken image rather than a missing branch.

But `auto` is parsed off a `cuda-` prefix, so the two `stock-*` configs yield an
empty string and hit that fail-closed arm. They have no `-auto` tag at all
(`qa-set` already excludes them via `.auto_version != ""`), so they never needed
a floor — yet they aborted the entire promotion before a single QA cell ran.

Latent since the stock configs and the case statement arrived together in #239;
it fired the first time a promote ran with both on main. The whole point of the
fail-closed arm is to name the real cause instead of sending someone to debug a
build that is fine, and here it did the opposite. This test walks the real config
table against the real case statement so a new config cannot reintroduce it.
"""
import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PROMOTE = REPO / ".github" / "workflows" / "promote-base-image.yml"
CONFIGS = REPO / "configs" / "base-image.json"


def _case_block() -> str:
    """The literal `case "$auto" in … esac` from the workflow."""
    text = PROMOTE.read_text()
    m = re.search(r'case "\$auto" in.*?esac', text, re.S)
    assert m, "could not find the driver-floor case statement in promote-base-image.yml"
    return m.group(0)


def _floor_for(tag_template: str) -> subprocess.CompletedProcess:
    """Run the REAL case statement against a template, the way the workflow does."""
    script = (
        'auto=$(echo "$1" | sed -n \'s/^cuda-\\([0-9.]*\\)-.*/\\1/p\')\n'
        'key=test\n'
        + _case_block()
        + '\necho "FLOOR=${floor}"\n'
    )
    return subprocess.run(["bash", "-c", script, "_", tag_template],
                          capture_output=True, text=True)


def test_every_config_in_the_table_resolves_a_floor():
    configs = json.loads(CONFIGS.read_text())["configs"]
    assert configs, "config table is empty"
    failures = []
    for c in configs:
        r = _floor_for(c["tag_template"])
        if r.returncode != 0:
            failures.append(f'{c["key"]} ({c["tag_template"]}): {r.stdout}{r.stderr}'.strip())
    assert not failures, (
        "these configs abort resolve-digests before QA runs:\n  " + "\n  ".join(failures))


def test_a_cuda_config_gets_its_EXACT_minor():
    """Not a major baseline. Flooring 13.* at 13.0 meant the newest images were
    validated almost entirely on 580/590/595 drivers reaching 13.3 through
    forward compat — the native path went untested, which is the shape of gap the
    610 rename got through. Measured supply says the thinness argument for a
    baseline holds only for the newest minor (13.3: 21 offers) and costs driver
    coverage for the other nine."""
    assert "FLOOR=13.3" in _floor_for("cuda-13.3.1-cudnn-devel-ubuntu24.04").stdout
    assert "FLOOR=13.0" in _floor_for("cuda-13.0.3-cudnn-devel-ubuntu24.04").stdout
    assert "FLOOR=12.9" in _floor_for("cuda-12.9.2-cudnn-devel-ubuntu24.04").stdout
    assert "FLOOR=12.1" in _floor_for("cuda-12.1.1-cudnn8-devel-ubuntu22.04").stdout
    assert "FLOOR=11.8" in _floor_for("cuda-11.8.0-cudnn8-devel-ubuntu22.04").stdout


def test_no_config_is_floored_below_its_own_cuda_version():
    """The regression this replaces: a 13.3 image accepting a 13.0 host."""
    configs = json.loads(CONFIGS.read_text())["configs"]
    for c in configs:
        tpl = c["tag_template"]
        m = re.match(r"cuda-(\d+)\.(\d+)", tpl)
        if not m:
            continue
        out = _floor_for(tpl).stdout
        fm = re.search(r"FLOOR=([\d.]+)", out)
        assert fm, f"{tpl}: no floor emitted"
        assert fm.group(1) == f"{m.group(1)}.{m.group(2)}", (
            f"{tpl}: floor {fm.group(1)} does not match the image's own CUDA minor")


def test_a_stock_config_resolves_empty_not_an_error():
    """No -auto tag, so no floor is needed — but it must not abort the run."""
    r = _floor_for("stock-ubuntu24.04")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FLOOR=" in r.stdout and "FLOOR=1" not in r.stdout


def test_a_new_cuda_major_now_derives_instead_of_aborting():
    """Derivation removes the maintenance step the old case statement needed —
    a future cuda-14.0 floors itself at 14.0 rather than aborting the promote."""
    assert "FLOOR=14.0" in _floor_for("cuda-14.0.0-cudnn-devel-ubuntu24.04").stdout


def test_a_MALFORMED_version_still_fails_closed():
    """The fail-closed arm still matters. A bare major (`cuda-13-...`) parses as
    a version but yields no minor, so it cannot produce an honest floor — abort
    rather than emit something QA would rent against."""
    r = _floor_for("cuda-13-cudnn-devel-ubuntu24.04")
    assert r.returncode != 0, "a version with no minor must abort"
    assert "has no driver floor" in (r.stdout + r.stderr)


def test_a_NON_cuda_template_reads_as_no_auto_tag():
    """Not an error: a template with no parseable `cuda-X.Y` prefix yields an
    empty auto version, which is exactly what the stock-* configs are. It fails
    SAFE — qa-set excludes it and it has no auto tag to flip."""
    r = _floor_for("notaversion-ubuntu24.04")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FLOOR=" in r.stdout and "FLOOR=1" not in r.stdout
