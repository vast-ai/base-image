"""The bundle a delivered run sends (bundle.py, ADR 0068).

What must hold: the suites are the ones the image inherits, resolved by the linter's
own rule; the digest depends on the checks and on nothing else; and a bundle that
would run nothing, or that comes from uncommitted changes, says so before anything is
rented.
"""
from __future__ import annotations

import io
import subprocess
import tarfile

import pytest

import bundle
from fakebox import REPO, make_repo

PASS = '#!/bin/bash\nsource "$(dirname "$0")/../lib.sh"\ntest_pass ok\n'
CLEAN = lambda repo, paths: ("c0ffee", False)                       # noqa: E731


def test_the_digest_is_a_function_of_the_checks_alone(tmp_path):
    repo = make_repo(tmp_path, {"base/10-a.sh": PASS})
    first = bundle.build(repo, "vastai/base-image:v1", git_state=CLEAN)
    again = bundle.build(repo, "vastai/base-image:v2", git_state=CLEAN)
    assert first.digest == again.digest
    assert first.tar("run-1") == again.tar("run-1").replace(b"image=vastai/base-image:v2",
                                                            b"image=vastai/base-image:v1")
    # the run id reaches the archive without reaching the digest
    assert first.tar("run-1") != first.tar("run-2")

    (repo / "ROOT/opt/instance-tools/tests/base/10-a.sh").write_text(PASS + "# changed\n")
    assert bundle.build(repo, "vastai/base-image:v1", git_state=CLEAN).digest != first.digest


def test_the_archive_holds_exactly_the_listed_files_with_fixed_metadata(tmp_path):
    repo = make_repo(tmp_path, {"base/10-a.sh": PASS})
    b = bundle.build(repo, "vastai/base-image", git_state=CLEAN)
    with tarfile.open(fileobj=io.BytesIO(b.tar("r1"))) as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        assert sorted(m.name for m in members) == b.paths()
        assert {(m.mtime, m.uid, m.gid) for m in tar.getmembers()} == {(0, 0, 0)}
        modes = {m.name: m.mode for m in members}
    # the runner discovers executable files only
    assert modes["base/10-a.sh"] == 0o755 and modes["driver.sh"] == 0o755
    assert "runner.sh" not in modes and "README.md" not in modes


def test_pytorch_gets_base_and_its_own_suite():
    b = bundle.build(REPO, "vastai/pytorch:any-tag", git_state=CLEAN)
    assert b.suites == ["base", "pytorch.d"]
    assert any(p.startswith("exposure-allowlist/") for p in b.files)


def test_a_nested_image_inherits_pytorch_and_a_later_layer_lands_on_top(tmp_path):
    nested = "derivatives/pytorch/derivatives/app"
    repo = make_repo(tmp_path, {"base/10-a.sh": PASS, "base/20-b.sh": PASS}, images={
        "derivatives/pytorch": {"overlays": {"ROOT": {"pytorch.d/10-torch.sh": PASS}}},
        nested: {"dockerfile": "FROM vastai/pytorch:dated\n", "overlays": {
            "ROOT_BASE": {"app.d/10-x.sh": "# from the base stage\n"},
            "ROOT": {"app.d/10-x.sh": "# from the final stage\n",
                     "base/20-b.sh": "# patched by the image\n"}}},
        "external/other": {"overlays": {"ROOT": {"other.d/10-y.sh": PASS}}},
    })
    b = bundle.build(repo, "vastai/app:tag", git_state=CLEAN)
    assert b.suites == ["app.d", "base", "pytorch.d"]            # and not other.d
    assert b.files["app.d/10-x.sh"][0] == b"# from the final stage\n"
    assert b.files["base/20-b.sh"][0] == b"# patched by the image\n"
    assert b.files["base/10-a.sh"][0] == PASS.encode()


def test_a_base_suite_with_nothing_to_run_is_refused(tmp_path):
    repo = make_repo(tmp_path, {"base/10-a.sh": PASS})
    (repo / "ROOT/opt/instance-tools/tests/base/10-a.sh").chmod(0o644)
    with pytest.raises(bundle.BundleError, match="no executable check"):
        bundle.build(repo, "vastai/base-image", git_state=CLEAN)


def test_an_image_with_no_directory_is_refused_not_given_base_alone(tmp_path):
    repo = make_repo(tmp_path, {"base/10-a.sh": PASS})
    with pytest.raises(bundle.BundleError, match="cannot be resolved"):
        bundle.build(repo, "vastai/not-in-this-tree:tag", git_state=CLEAN)


def test_uncommitted_changes_to_a_check_mark_the_bundle_dirty(tmp_path):
    repo = make_repo(tmp_path, {"base/10-a.sh": PASS})
    driver = repo / "tools/template_manager/onbox/driver.sh"
    driver.parent.mkdir(parents=True)
    driver.write_bytes(bundle.DRIVER.read_bytes())

    def git(*args):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c",
                        "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
                       check=True, capture_output=True)
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "checks")

    clean = bundle.build(repo, "vastai/base-image", driver=driver)
    assert clean.dirty is False and len(clean.commit) == 40
    assert b"client_dirty=false" in clean.run_descriptor("r1")

    # outside the bundle's sources: not this bundle's business
    (repo / "notes.txt").write_text("scratch\n")
    assert bundle.build(repo, "vastai/base-image", driver=driver).dirty is False

    (repo / "ROOT/opt/instance-tools/tests/base/10-a.sh").write_text(PASS + "# edited\n")
    dirty = bundle.build(repo, "vastai/base-image", driver=driver)
    assert dirty.dirty is True and b"client_dirty=true" in dirty.run_descriptor("r1")

    # a driver from outside the checkout cannot be vouched for
    assert bundle.build(repo, "vastai/base-image").dirty == bundle.UNKNOWN
