"""Offline tests for the base-tag resolver + bump (ADR 0013). The network is never touched:
`fetch` is injected with a fixed tag list, so parse/select/bump are all deterministic."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from imagegen import basetag, bump as bumpmod   # noqa: E402

# A realistic slice of vastai/pytorch tags: two dates for the primary tuple, a second cuda
# tuple, plus decoys (other torch/py/variant, an arch-suffixed manifest, an `-auto` tag).
FAKE_TAGS = [
    "2.10.0-cu128-cuda-12.9-mini-py312-2026-06-15",
    "2.10.0-cu128-cuda-12.9-mini-py312-2026-08-01",       # newer, SAME tuple -> wins
    "2.10.0-cu130-cuda-13.2-mini-py312-2026-06-15",
    "2.10.0-cu130-cuda-13.2-mini-py312-2026-07-20",       # newer for the cuda-13.2 tuple
    "2.11.0-cu128-cuda-12.9-mini-py312-2026-09-09",       # different torch (decoy)
    "2.10.0-cu128-cuda-12.9-mini-py311-2026-09-09",       # different py (decoy)
    "2.10.0-cu128-cuda-12.9-py312-2026-09-09",            # full, non-mini (decoy)
    "2.10.0-cu128-cuda-12.9-mini-py312-2026-06-15-amd64",  # arch manifest -> ignored
    "cuda-12.9.2-auto",                                    # base-style auto tag -> ignored
]


def fake_fetch(repo="vastai/pytorch"):
    return list(FAKE_TAGS)


def test_parse_tag_valid_and_invalid():
    bt = basetag.parse_tag("2.10.0-cu128-cuda-12.9-mini-py312-2026-06-15")
    assert bt and bt.torch == "2.10.0" and bt.wheel == "128" and bt.cuda == "12.9"
    assert bt.mini is True and bt.py == "312" and bt.date == "2026-06-15"
    assert basetag.parse_tag("2.10.0-cu128-cuda-12.9-py312-2026-06-15").mini is False
    assert basetag.parse_tag("2.10.0-cu128-cuda-12.9-mini-py312-2026-06-15-amd64") is None
    assert basetag.parse_tag("cuda-12.9.2-auto") is None
    assert basetag.parse_tag("latest") is None


def test_select_latest_picks_newest_date():
    bt = basetag.select_latest(FAKE_TAGS, torch="2.10.0", cuda="12.9", py="312", mini=True)
    assert bt.date == "2026-08-01"                        # the newer of the two, not the older


def test_select_latest_respects_tuple():
    # cuda-13.2 tuple resolves independently
    assert basetag.select_latest(FAKE_TAGS, torch="2.10.0", cuda="13.2", py="312").date == "2026-07-20"
    # full (non-mini) is a different variant
    assert basetag.select_latest(FAKE_TAGS, torch="2.10.0", cuda="12.9", py="312", mini=False).date == "2026-09-09"


def test_select_latest_no_match_raises():
    try:
        basetag.select_latest(FAKE_TAGS, torch="9.9.9", cuda="12.9", py="312")
        assert False, "expected LookupError"
    except LookupError:
        pass


def test_resolve_prefixes_repo():
    out = basetag.resolve(torch="2.10.0", cuda="12.9", py="312", fetch=fake_fetch)
    assert out == "vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312-2026-08-01"


def _fake_pytorch_image(tmp_path: Path, name: str, dockerfile: str, workflow: str) -> Path:
    d = tmp_path / "derivatives" / "pytorch" / "derivatives" / name
    d.mkdir(parents=True)
    (d / "Dockerfile").write_text(dockerfile)
    (tmp_path / "external").mkdir(exist_ok=True)          # repo marker
    wf = tmp_path / ".github" / "workflows" / f"build-{name}.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text(workflow)
    return tmp_path


def test_bump_rewrites_dockerfile_and_workflow(tmp_path):
    df = "ARG PYTORCH_BASE=vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312-2026-06-15\nFROM ${PYTORCH_BASE}\n"
    wf = ("    matrix:\n      base_image:\n"
          "        - vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312-2026-06-15\n"
          "        - vastai/pytorch:2.10.0-cu130-cuda-13.2-mini-py312-2026-06-15\n")
    repo = _fake_pytorch_image(tmp_path, "myimg", df, wf)
    rc = bumpmod.bump("myimg", repo=repo, fetch=fake_fetch, log=lambda *a: None)
    assert rc == 0
    new_df = (repo / "derivatives/pytorch/derivatives/myimg/Dockerfile").read_text()
    new_wf = (repo / ".github/workflows/build-myimg.yml").read_text()
    assert "12.9-mini-py312-2026-08-01" in new_df          # Dockerfile ARG bumped
    assert "cuda-12.9-mini-py312-2026-08-01" in new_wf      # cuda-12.9 matrix row bumped
    assert "cuda-13.2-mini-py312-2026-07-20" in new_wf      # cuda-13.2 row -> its OWN newest
    assert "2026-06-15" not in new_df and "2026-06-15" not in new_wf


def test_bump_idempotent_when_already_newest(tmp_path):
    df = "ARG PYTORCH_BASE=vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312-2026-08-01\nFROM ${PYTORCH_BASE}\n"
    repo = _fake_pytorch_image(tmp_path, "myimg", df, "no pins here\n")
    msgs = []
    bumpmod.bump("myimg", repo=repo, fetch=fake_fetch, log=lambda *a: msgs.append(" ".join(map(str, a))))
    assert (repo / "derivatives/pytorch/derivatives/myimg/Dockerfile").read_text() == df   # unchanged
    assert any("nothing to bump" in m for m in msgs)


def test_bump_leaves_unparseable_pin_untouched(tmp_path):
    df = "ARG PYTORCH_BASE=vastai/pytorch:CHANGEME\nFROM ${PYTORCH_BASE}\n"     # scaffold placeholder
    repo = _fake_pytorch_image(tmp_path, "myimg", df, "none\n")
    bumpmod.bump("myimg", repo=repo, fetch=fake_fetch, log=lambda *a: None)
    assert "CHANGEME" in (repo / "derivatives/pytorch/derivatives/myimg/Dockerfile").read_text()


if __name__ == "__main__":
    from _stdlib_runner import run
    raise SystemExit(run(globals()))
