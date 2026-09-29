"""venv_mirror on temporary trees (ADR 0048). Each case is a failure measured on a real image
while the design was reviewed; the integration proof is the image build and its QA cell."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import venv_mirror as vf


def _dist(site: Path, name: str, version: str, files: dict, *, record=True, meta_dir=None,
          entry_points: str = "") -> None:
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


def _venv(tmp_path: Path) -> Path:
    venv = tmp_path / "venv"
    site = venv / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
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
    assert (site / "torch/lib/libtorch.so").is_symlink()
    assert os.readlink(site / "torch/lib/libtorch.so") == str(engine / "torch/lib/libtorch.so")
    assert (site / "torch/lib").is_dir() and not (site / "torch/lib").is_symlink()
    assert _dir_links(site) == []
    assert vf.structural_problems(venv) == []


def test_a_project_the_venv_owns_is_not_linked_nor_filled(tmp_path, engine):
    """The venv's seed pip must win over the engine's, and the engine's leftover files (pyc)
    must not be linked into the venv's pip directory -- the mixed-tree bug."""
    venv = _venv(tmp_path)
    _dist(_site(venv), "pip", "26.0", {"pip/__init__.py": "venv pip"})
    _dist(engine, "pip", "24.0", {"pip/__init__.py": "engine pip"})
    (engine / "pip/__pycache__").mkdir()
    (engine / "pip/__pycache__/__init__.cpython-312.pyc").write_text("engine pyc")
    res = vf.build(venv, [engine])
    site = _site(venv)
    assert res.skipped["pip"] == "owned by the venv"
    assert not (site / "pip/__init__.py").is_symlink()
    assert not (site / "pip/__pycache__").exists()
    assert not (site / "pip-24.0.dist-info").exists()
    assert vf.structural_problems(venv) == []


def test_two_metadata_entries_for_one_project_leave_one_installed_copy(tmp_path, engine):
    """Debian ships cryptography-41.0.7.dist-info AND cryptography.egg-info in one site; the
    unchosen one leaked in and uv then saw two copies (measured on vllm v0.30.0)."""
    deb = tmp_path / "debian-site"
    deb.mkdir()
    _dist(deb, "cryptography", "41.0.7", {"cryptography/__init__.py": "C"}, record=False)
    egg = deb / "cryptography.egg-info"
    egg.mkdir()
    (egg / "PKG-INFO").write_text("Metadata-Version: 1.2\nName: cryptography\nVersion: 41.0.7\n")
    (egg / "top_level.txt").write_text("cryptography\n")
    venv = _venv(tmp_path)
    vf.build(venv, [engine, deb])
    metas = [m.name for m in _site(venv).iterdir() if m.name.startswith("cryptography")
             and m.name.endswith(vf.METADATA_SUFFIXES)]
    assert metas == ["cryptography-41.0.7.dist-info"]
    assert vf.structural_problems(venv) == []


def test_the_first_source_wins_and_the_loser_does_not_leak(tmp_path, engine):
    later = tmp_path / "later-site"
    later.mkdir()
    _dist(later, "numpy", "1.0", {"numpy/__init__.py": "old", "numpy/extra_old.py": "x"})
    venv = _venv(tmp_path)
    res = vf.build(venv, [engine, later])
    site = _site(venv)
    assert os.readlink(site / "numpy/__init__.py") == str(engine / "numpy/__init__.py")
    assert not (site / "numpy/extra_old.py").exists()
    assert "numpy" in res.skipped
    assert not (site / "numpy-1.0.dist-info").exists()


def test_a_debian_egg_info_without_a_file_list_is_linked_by_top_level(tmp_path, engine):
    deb = tmp_path / "debian-site"
    deb.mkdir()
    (deb / "apt").mkdir()
    (deb / "apt/__init__.py").write_text("A")
    (deb / "apt_pkg.cpython-312-x86_64-linux-gnu.so").write_text("S")
    egg = deb / "python_apt-2.7.7.egg-info"
    egg.mkdir()
    (egg / "PKG-INFO").write_text("Metadata-Version: 1.1\nName: python-apt\nVersion: 2.7.7\n")
    (egg / "top_level.txt").write_text("apt\napt_pkg\n")
    venv = _venv(tmp_path)
    vf.build(venv, [engine, deb])
    site = _site(venv)
    assert (site / "apt/__init__.py").is_symlink()
    assert (site / "apt_pkg.cpython-312-x86_64-linux-gnu.so").is_symlink()
    assert vf.is_mirrored(site / "python_apt-2.7.7.egg-info")


def test_namespace_directories_are_shared_by_two_projects(tmp_path, engine):
    _dist(engine, "nvidia-nccl-cu13", "2.30.7", {"nvidia/nccl/lib/libnccl.so.2": "n"})
    _dist(engine, "nvidia-cublas-cu13", "13.0", {"nvidia/cublas/lib/libcublas.so": "c"})
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    site = _site(venv)
    assert (site / "nvidia/nccl/lib/libnccl.so.2").is_symlink()
    assert (site / "nvidia/cublas/lib/libcublas.so").is_symlink()
    assert not (site / "nvidia").is_symlink()


def test_a_source_directory_symlink_is_followed_once(tmp_path, engine):
    (engine / "pkg").mkdir()
    (engine / "pkg/mod.py").write_text("m")
    os.symlink(engine / "pkg", engine / "pkg/loop")           # a cycle
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    site = _site(venv)
    assert (site / "pkg/mod.py").is_symlink()
    assert _dir_links(site) == []


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
    assert "torchrun" in res.scripts and "existing" not in res.scripts
    assert os.access(venv / "bin" / "cli", os.X_OK)


def test_the_manifest_records_what_was_mirrored(tmp_path, engine):
    venv = _venv(tmp_path)
    vf.build(venv, [engine], engine_python="/usr/bin/python3")
    data = json.loads((venv / vf.MANIFEST).read_text())
    assert data["engine_python"] == "/usr/bin/python3"
    assert data["projects"]["torch"]["version"] == "2.13.0"
    assert data["sources"] == [str(engine)]


def test_ownership_follows_the_metadata_file(tmp_path, engine):
    """A user who upgrades a mirrored project owns it afterwards: its metadata is real."""
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    site = _site(venv)
    assert "numpy" not in vf.owned_projects(site)
    for f in (site / "numpy-2.2.6.dist-info").iterdir():       # what an uninstall does
        f.unlink()
    (site / "numpy-2.2.6.dist-info").rmdir()
    _dist(site, "numpy", "2.3.0", {})                           # what an upgrade then writes
    assert "numpy" in vf.owned_projects(site)


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
    venv = _venv(tmp_path)
    assert any(vf.MANIFEST in p for p in vf.structural_problems(venv))


def _elf_with_runpath(path: Path, runpath: str) -> None:
    """A minimal ELF64 LE: one PT_LOAD covering the file, one PT_DYNAMIC with STRTAB+RUNPATH."""
    import struct
    strtab = b"\0" + runpath.encode() + b"\0"
    ehdr_size, ph_size = 64, 56
    dyn_off = ehdr_size + 2 * ph_size
    dyn = struct.pack("<qQqQqQ", 5, 0, 29, 1, 0, 0)            # STRTAB@vaddr, RUNPATH@1, NULL
    str_off = dyn_off + len(dyn)
    dyn = struct.pack("<qQqQqQ", 5, str_off, 29, 1, 0, 0)
    size = str_off + len(strtab)
    ehdr = b"\x7fELF" + bytes([2, 1, 1]) + b"\0" * 9
    ehdr += struct.pack("<HHIQQQIHHHHHH", 3, 62, 1, 0, ehdr_size, 0, 0, ehdr_size, ph_size, 2,
                        64, 0, 0)
    load = struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, size, size, 0x1000)
    dynph = struct.pack("<IIQQQQQQ", 2, 6, dyn_off, dyn_off, dyn_off, len(dyn), len(dyn), 8)
    path.write_bytes(ehdr + load + dynph + dyn + strtab)


def test_an_rpath_that_leaves_site_packages_is_reported(tmp_path, engine):
    """$ORIGIN resolves relative to the LINK: a library pointing outside site-packages loads
    from the engine's copy and fails from the mirror's."""
    outside = tmp_path / "prefix-lib"
    outside.mkdir()
    (engine / "ext").mkdir()
    _elf_with_runpath(engine / "ext" / "_c.so", f"$ORIGIN/../../prefix-lib")
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    assert vf.elf_origin_paths(engine / "ext" / "_c.so") == ["$ORIGIN/../../prefix-lib"]
    assert any("RPATH" in p for p in vf.origin_problems(venv))


def test_an_rpath_inside_site_packages_is_fine(tmp_path, engine):
    (engine / "ext").mkdir()
    _elf_with_runpath(engine / "ext" / "_c.so", "$ORIGIN/../torch/lib")
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    assert vf.origin_problems(venv) == []


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
                          "import mirrortest, os; print(mirrortest.VALUE, os.path.realpath(mirrortest.__file__))"],
                         capture_output=True, text=True, check=True).stdout.split()
    assert out == ["42", str(src / "mirrortest" / "__init__.py")]


def _fake_run(uv_output):
    """subprocess.run stand-in: the import probe succeeds from outside the venv; uv prints
    the given plan."""
    class R:
        def __init__(self, out, rc=0):
            self.stdout, self.stderr, self.returncode = out, "", rc

    def run(cmd, **kw):
        if cmd[0] == "uv":
            return R(uv_output)
        return R("/engine-site/torch/__init__.py\n")
    return run


def test_the_uv_check_fails_when_uv_would_reinstall_torch(tmp_path, engine, monkeypatch):
    """The shipped defect, as uv reports it."""
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    monkeypatch.setattr(vf.subprocess, "run",
                        _fake_run("Would install 1 package\n + torch==2.14.0\n"))
    assert any("torch==2.13.0" in p for p in vf.engine_problems(venv, ["torch"]))


def test_the_uv_check_ignores_the_upstream_repairing_its_own_pins(tmp_path, engine, monkeypatch):
    """sglang ships nvidia-nccl-cu13 2.30.7 under a torch that pins 2.29.7; uv plans the
    same change against the engine's own environment, so it is reported, not failed."""
    venv = _venv(tmp_path)
    vf.build(venv, [engine])
    monkeypatch.setattr(vf.subprocess, "run", _fake_run(
        "Would install 1 package\n - nvidia-nccl-cu13==2.30.7\n + nvidia-nccl-cu13==2.29.7\n"))
    assert vf.engine_problems(venv, ["torch"]) == []


def test_a_directory_the_venv_owns_is_never_filled(tmp_path, engine):
    """A venv-owned directory no engine project claims (here, the user's own package) must
    not gain the engine's stray files: that is the mixed-tree bug with no project to block."""
    venv = _venv(tmp_path)
    _dist(_site(venv), "mylib", "1.0", {"mylib/__init__.py": "mine"})
    (engine / "mylib" / "__pycache__").mkdir(parents=True)
    (engine / "mylib" / "__pycache__" / "stray.pyc").write_text("engine")
    vf.build(venv, [engine])
    assert not (_site(venv) / "mylib" / "__pycache__").exists()
