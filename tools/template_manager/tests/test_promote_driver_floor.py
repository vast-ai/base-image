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


def test_a_cuda_config_gets_its_major_baseline():
    assert "FLOOR=13.0" in _floor_for("cuda-13.3.1-cudnn-devel-ubuntu24.04").stdout
    assert "FLOOR=12.0" in _floor_for("cuda-12.9.2-cudnn-devel-ubuntu24.04").stdout
    assert "FLOOR=11.8" in _floor_for("cuda-11.8.0-cudnn8-devel-ubuntu22.04").stdout


def test_a_stock_config_resolves_empty_not_an_error():
    """No -auto tag, so no floor is needed — but it must not abort the run."""
    r = _floor_for("stock-ubuntu24.04")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FLOOR=" in r.stdout and "FLOOR=1" not in r.stdout


def test_an_UNKNOWN_cuda_major_still_fails_closed():
    """The fail-closed arm is the point — a new major must not inherit 11.8."""
    r = _floor_for("cuda-14.0.0-cudnn-devel-ubuntu24.04")
    assert r.returncode != 0, "a CUDA major with no floor branch must abort"
    assert "has no driver floor" in (r.stdout + r.stderr)
