"""A config with "auto_tag": false never touches a cuda-X.Y.Z-auto tag (ADR 0054).

Ubuntu 26.04 configs share CUDA versions with 24.04 ones. The auto tag is keyed by
CUDA version alone, so without the opt-out the 26.04 config would race the 24.04
one for the same tag and customers on cuda-X.Y.Z-auto would silently move OS.
"""

from __future__ import annotations

import json
import re

import pytest

import wfexec
from wfexec import PROMOTE, REPO, auto_tag, build_registry, decisions, manifest, promote_env, requires_tools, run_step, step_script

pytestmark = pytest.mark.skipif(not requires_tools(), reason="jq not available")

GENERATE = ("generate-configs", "Generate filtered config list")


def _generate(tmp_path, workspace):
    wd = tmp_path / "gen"
    r = run_step(step_script(PROMOTE, *GENERATE), wd, {}, {"GITHUB_WORKSPACE": workspace, "FILTER": ""})
    m = re.search(r"all-configs<<ALL_CONFIGS_EOF\n(.*?)\nALL_CONFIGS_EOF", r["github_output"], re.S)
    return r, (json.loads(m.group(1)) if m else None)


def test_generate_configs_gives_opted_out_configs_no_auto_version(tmp_path):
    r, configs = _generate(tmp_path, REPO)
    assert r["code"] == 0, r["err"]
    auto = {c["key"]: c["auto_version"] for c in configs}
    assert auto["cuda-13.4-24"] == "13.4.2"
    assert auto["cuda-13.4-26"] == ""
    assert auto["stock-26"] == ""


def test_generate_configs_refuses_two_configs_on_one_auto_tag(tmp_path):
    ws = tmp_path / "ws"
    (ws / "configs").mkdir(parents=True)
    table = json.loads((REPO / "configs/base-image.json").read_text())
    for c in table["configs"]:
        c.pop("auto_tag", None)
    (ws / "configs/base-image.json").write_text(json.dumps(table))
    r, _ = _generate(tmp_path, ws)
    assert r["code"] != 0
    assert "13.4.2" in r["out"] + r["err"]


def test_the_dance_leaves_the_auto_tag_on_the_auto_tagged_config(tmp_path, monkeypatch):
    """Both configs promote their dated tags; only the 24.04 one moves cuda-13.3.1-auto."""
    opted_out = {"key": "cuda-13.3-26", "tag_template": "cuda-13.3.1-cudnn-devel-ubuntu26.04",
                 "default_python": "3.14", "auto_version": ""}
    monkeypatch.setattr(wfexec, "CONFIGS", wfexec.CONFIGS + [opted_out])
    targets = {"cuda-12.9-24": "sha256:new12", "cuda-13.3-24": "sha256:new13",
               "cuda-13.3-26": "sha256:new13-u26"}
    current = {"cuda-12.9-24": "sha256:old12", "cuda-13.3-24": "sha256:old13"}
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps(manifest(targets, current)))
    (wd / "decisions.json").write_text(json.dumps(decisions({k: "flip" for k in targets}, targets)))
    reg = build_registry(targets, {"12.9.2": "sha256:old12", "13.3.1": "sha256:old13"})
    r = run_step(step_script(PROMOTE, "promote", "Promote all images sequentially"), wd, reg, promote_env())
    assert r["code"] == 0, r["err"]
    assert r["registry"][auto_tag("13.3.1")] == "sha256:new13"
    dated = f"{wfexec.PROD}/base-image:cuda-13.3.1-cudnn-devel-ubuntu26.04-py314-{wfexec.DATE}"
    assert r["registry"][dated] == "sha256:new13-u26"
