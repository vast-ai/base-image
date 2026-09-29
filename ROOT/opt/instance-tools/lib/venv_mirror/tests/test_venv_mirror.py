"""venv_mirror on temporary trees (ADR 0048). Each test guards one rule, and each rule was a
failure measured on a real image; the integration proof is the image build and its QA cell."""

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import venv_mirror as vf


def _dist(site: Path, name: str, version: str, files: dict, *, record=True, meta_dir=None,
          entry_points: str = "") -> Path:
    """Install a fake distribution into `site`: `files` maps relative path -> contents."""
    for rel, text in files.items():
        p = site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    meta = site / (meta_dir or f"{name.replace('-', '_')}-{version}.dist-info")
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    if entry_points:
        (meta / "entry_points.txt").write_text(entry_points)
    if record:
        rows = [f"{rel},," for rel in files] + [f"{meta.name}/METADATA,,", f"{meta.name}/RECORD,,"]
        (meta / "RECORD").write_text("\n".join(rows) + "\n")
    return meta


def _venv(tmp_path: Path) -> Path:
    venv = tmp_path / "venv"
    (venv / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\ninclude-system-site-packages = false\n")
    return venv


def _site(venv: Path) -> Path:
    return vf.venv_site(venv)


def _dir_links(root: Path):
    return [Path(d) / n for d, dirs, _ in os.walk(root) for n in dirs if (Path(d) / n).is_symlink()]


@pytest.fixture
def engine(tmp_path):
    src = tmp_path / "engine-site"
    src.mkdir()
    _dist(src, "torch", "2.13.0", {"torch/__init__.py": "T", "torch/lib/libtorch.so": "L"},
          entry_points="[console_scripts]\ntorchrun = torch.distributed.run:main\n")
    _dist(src, "numpy", "2.2.6", {"numpy/__init__.py": "N"})
    return src


def test_every_file_is_a_link_and_no_directory_is(tmp_path, engine):
    """uv installs and uninstalls per file path; a directory link sends those writes into the
    engine's tree (measured: dill 0.3.8 written through a link into the system copy)."""
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    site = _site(venv)
    assert os.readlink(site / "torch/lib/libtorch.so") == str(engine / "torch/lib/libtorch.so")
    assert (site / "torch/lib").is_dir() and _dir_links(site) == []
    assert vf.structural_problems(venv) == []


def test_a_project_the_venv_owns_is_not_linked_nor_filled(tmp_path, engine):
    """The venv's own pip wins over the engine's, and the engine's stray files (pyc) are not
    linked into the venv's pip directory."""
    venv = _venv(tmp_path)
    _dist(_site(venv), "pip", "26.0", {"pip/__init__.py": "venv pip"})
    _dist(engine, "pip", "24.0", {"pip/__init__.py": "engine pip"})
    (engine / "pip/__pycache__").mkdir()
    (engine / "pip/__pycache__/__init__.cpython-312.pyc").write_text("engine pyc")
    res = vf.build(venv, [engine])
    site = _site(venv)
    assert res.skipped["pip"] == "owned by the venv"
    assert not (site / "pip/__init__.py").is_symlink()
    assert not (site / "pip/__pycache__").exists() and not (site / "pip-24.0.dist-info").exists()


def test_a_directory_the_venv_owns_is_never_filled(tmp_path, engine):
    """A directory the venv already had gains nothing from the engine: neither a stray file
    nor one an engine project lists for it."""
    venv = _venv(tmp_path)
    _dist(_site(venv), "mylib", "1.0", {"mylib/__init__.py": "mine"})
    (engine / "mylib" / "__pycache__").mkdir(parents=True)
    (engine / "mylib" / "__pycache__" / "stray.pyc").write_text("engine")
    _dist(engine, "mylib-plugin", "1.0", {"mylib/plugin.py": "engine"})
    vf.build(venv, [engine])
    assert not (_site(venv) / "mylib" / "__pycache__").exists()
    assert not (_site(venv) / "mylib" / "plugin.py").exists()


def test_two_metadata_entries_for_one_project_leave_one_copy_and_its_code(tmp_path, engine):
    """Debian ships cryptography-41.0.7.dist-info (no RECORD, no top_level.txt) AND
    cryptography.egg-info for ONE tree. Linking both made uv see two copies; blocking the
    second entry's files instead left the package's code unlinked while it looked installed."""
    deb = tmp_path / "debian-site"
    deb.mkdir()
    _dist(deb, "cryptography", "41.0.7", {"cryptography/__init__.py": "C"}, record=False)
    egg = deb / "cryptography.egg-info"
    egg.mkdir()
    (egg / "PKG-INFO").write_text("Metadata-Version: 1.2\nName: cryptography\nVersion: 41.0.7\n")
    (egg / "top_level.txt").write_text("cryptography\n")
    venv = _venv(tmp_path)
    vf.build(venv, [engine, deb])
    site = _site(venv)
    metas = [m.name for m in site.iterdir() if m.name.startswith("cryptography")
             and m.name.endswith(vf.METADATA_SUFFIXES)]
    assert metas == ["cryptography-41.0.7.dist-info"]
    assert (site / "cryptography/__init__.py").is_symlink()
    assert vf.structural_problems(venv) == []


def test_a_shadowed_copy_leaks_nothing(tmp_path, engine):
    """A lower-priority copy of a project the first source provides adds none of its files,
    neither inside the winner's tree nor outside it."""
    later = tmp_path / "later-site"
    later.mkdir()
    _dist(later, "numpy", "1.0", {"numpy/__init__.py": "old", "numpy/extra_old.py": "x",
                                  "numpy_legacy.py": "y"})
    venv = _venv(tmp_path)
    res = vf.build(venv, [engine, later])
    site = _site(venv)
    assert os.readlink(site / "numpy/__init__.py") == str(engine / "numpy/__init__.py")
    assert not (site / "numpy/extra_old.py").exists() and not (site / "numpy_legacy.py").exists()
    assert "numpy" in res.skipped and not (site / "numpy-1.0.dist-info").exists()


def test_unlisted_files_stay_out_of_another_sources_package(tmp_path, engine):
    """Debian's metadata-less pkg_resources/ filled the pkg_resources/ made for /usr/local's
    setuptools; uninstalling setuptools then left an importable empty package (measured on
    vllm). Files a second source lists for its OWN project may still share a directory."""
    deb = tmp_path / "debian-site"
    (deb / "numpy").mkdir(parents=True)
    (deb / "numpy" / "orphan.py").write_text("debian")
    _dist(deb, "numpy-addon", "1.0", {"numpy/addon.py": "listed"})
    venv = _venv(tmp_path)
    vf.build(venv, [engine, deb])
    site = _site(venv)
    assert not (site / "numpy" / "orphan.py").exists()
    assert (site / "numpy" / "addon.py").is_symlink()


def test_a_record_is_written_for_debian_metadata_without_one(tmp_path, engine):
    """Without a RECORD pip refused to uninstall (`no RECORD file was found ... installed by
    debian`) and uv left the old metadata beside the new, so verify saw two copies."""
    deb = tmp_path / "debian-site"
    deb.mkdir()
    (deb / "distro").mkdir()
    (deb / "distro" / "__init__.py").write_text("d")
    meta = _dist(deb, "distro", "1.9.0", {}, record=False)
    (meta / "top_level.txt").write_text("distro\n")
    venv = _venv(tmp_path)
    vf.build(venv, [engine, deb])
    record = _site(venv) / "distro-1.9.0.dist-info" / "RECORD"
    assert record.is_file() and not record.is_symlink()
    rows = {r[0] for r in csv.reader(record.open())}
    assert {"distro/__init__.py", "distro-1.9.0.dist-info/METADATA",
            "distro-1.9.0.dist-info/RECORD"} <= rows


def test_a_source_directory_symlink_is_followed_once(tmp_path, engine):
    (engine / "pkg").mkdir()
    (engine / "pkg/mod.py").write_text("m")
    os.symlink(engine / "pkg", engine / "pkg/loop")           # a cycle
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    site = _site(venv)
    assert (site / "pkg/mod.py").is_symlink()
    assert not (site / "pkg/loop").exists() and _dir_links(site) == []


def test_launchers_run_the_engine_through_the_venv(tmp_path, engine):
    """sglang's launcher ran #!/opt/sglang/bin/python3, so nothing installed in /venv/main
    reached the engine. The mirror writes the engine's console scripts for the venv."""
    venv = _venv(tmp_path)
    (venv / "bin" / "existing").write_text("keep")
    _dist(engine, "tool", "1.0", {"tool/__init__.py": ""},
          entry_points="[console_scripts]\nexisting = tool:main\ncli = tool.app:App.run\n")
    res = vf.build(venv, [engine], venv_python="/venv/main/bin/python")
    text = (venv / "bin" / "cli").read_text()
    assert text.startswith("#!/venv/main/bin/python\n")
    assert "from tool.app import App" in text and "sys.exit(App.run())" in text
    assert (venv / "bin" / "existing").read_text() == "keep"
    assert "torchrun" in res.scripts and os.access(venv / "bin" / "cli", os.X_OK)


# --- verify: each check bites ------------------------------------------------------------

def test_verify_reports_a_directory_link(tmp_path, engine):
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    os.symlink(engine / "torch", _site(venv) / "torch_alias")
    assert any("directory symlink" in p for p in vf.structural_problems(venv))


def test_verify_reports_a_dangling_link(tmp_path, engine):
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    (engine / "numpy/__init__.py").unlink()
    assert any("dangling link" in p for p in vf.structural_problems(venv))


def test_verify_reports_two_copies_of_one_project(tmp_path, engine):
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    _dist(_site(venv), "numpy", "2.3.5", {})
    assert any("two installed copies of numpy" in p for p in vf.structural_problems(venv))


def test_verify_reports_inheritance_left_on(tmp_path, engine):
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    (venv / "pyvenv.cfg").write_text("include-system-site-packages = true\n")
    assert any("include-system-site-packages" in p for p in vf.structural_problems(venv))


def test_verify_reports_a_venv_the_mirror_did_not_build(tmp_path):
    assert any(vf.MANIFEST in p for p in vf.structural_problems(_venv(tmp_path)))


def test_build_verify_reports_a_project_replaced_during_the_build(tmp_path, engine):
    """The headline defect: a build step installing numpy 2.3.5 over the engine's 2.2.6. Every
    other check passes on that tree."""
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    site = _site(venv)
    for f in (site / "numpy-2.2.6.dist-info").iterdir():       # what an upgrade does
        f.unlink()
    (site / "numpy-2.2.6.dist-info").rmdir()
    _dist(site, "numpy", "2.3.5", {})
    assert vf.structural_problems(venv) == []
    assert any("numpy 2.3.5 was installed over the engine's 2.2.6" in p
               for p in vf.shadow_problems(venv))


def test_build_refuses_a_venv_on_another_interpreter(tmp_path, monkeypatch):
    from venv_mirror import __main__ as cli
    info = {"/engine/python": {"base": "/usr/bin/python3.12", "version": "3.12", "sources": []},
            str(tmp_path / "v/bin/python"): {"base": "/usr/bin/python3.11", "version": "3.11",
                                              "sources": []}}
    monkeypatch.setattr(cli, "engine_info", lambda py: info[py])
    assert cli.main(["build", "--venv", str(tmp_path / "v"), "--engine-python",
                     "/engine/python"]) == 1


def test_a_real_venv_python_imports_through_the_mirror(tmp_path):
    """End to end on this machine's interpreter: a package importable only through the mirror."""
    src = tmp_path / "engine-site"
    src.mkdir()
    _dist(src, "mirrortest", "1.0", {"mirrortest/__init__.py": "VALUE = 42\n"})
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    vf.build(venv, [src])
    out = subprocess.run([str(venv / "bin" / "python"), "-c",
                          "import mirrortest, os; print(mirrortest.VALUE, "
                          "os.path.realpath(mirrortest.__file__))"],
                         capture_output=True, text=True, check=True).stdout.split()
    assert out == ["42", str(src / "mirrortest" / "__init__.py")]


def _fake_run(uv_output):
    """subprocess.run stand-in: the import probe resolves outside the venv; uv prints a plan."""
    class R:
        def __init__(self, out):
            self.stdout, self.stderr, self.returncode = out, "", 0
    return lambda cmd, **kw: R(uv_output if cmd[0] == "uv" else "/engine-site/torch/__init__.py\n")


def test_the_uv_check_fails_when_uv_would_reinstall_torch(tmp_path, engine, monkeypatch):
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    monkeypatch.setattr(vf.subprocess, "run", _fake_run("Would install 1 package\n + torch==2.14.0\n"))
    assert any("torch==2.13.0" in p for p in vf.engine_problems(venv, ["torch"]))


def test_the_uv_check_ignores_the_upstream_repairing_its_own_pins(tmp_path, engine, monkeypatch):
    """sglang ships nvidia-nccl-cu13 2.30.7 under a torch that pins 2.29.7; uv plans that
    change against the engine's own environment too, so it is reported, not failed."""
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    monkeypatch.setattr(vf.subprocess, "run", _fake_run(
        "Would install 1 package\n - nvidia-nccl-cu13==2.30.7\n + nvidia-nccl-cu13==2.29.7\n"))
    assert vf.engine_problems(venv, ["torch"]) == []
