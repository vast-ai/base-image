"""Linter tests: a regression net over the real repo + one mutant per invariant.

Run: cd tools/imagegen && PYTHONPATH=. python -m pytest -q
"""
import re
import shutil
from dataclasses import replace
from pathlib import Path

from imagegen.discover import Image, discover, find_repo_root
from imagegen.linter import lint_image, ERROR, EXCEPTIONS

VALID_DF = """\
ARG PYTORCH_BASE=vastai/pytorch:test
FROM ${PYTORCH_BASE}
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Test suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"
COPY ./ROOT /
RUN torch_versions_pre=$(pip list); \\
    uv pip install foo; \\
    torch_versions_post=$(pip list); \\
    [ "$torch_versions_pre" = "$torch_versions_post" ] || exit 1
RUN env-hash > /.env_hash
"""

VALID_CONF = """\
[program:foo]
command=/opt/supervisor-scripts/foo.sh
environment=PROC_NAME="%(program_name)s"
stdout_logfile=/dev/stdout
redirect_stderr=true
"""

VALID_SCRIPT = """\
#!/bin/bash
. "${utils}/logging.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh"
exec foo
"""


def make(tmp: Path, *, cls="pytorch-nested", df=VALID_DF, confs=None, scripts=None) -> Image:
    d = tmp / "img"
    (d / "ROOT/etc/supervisor/conf.d").mkdir(parents=True, exist_ok=True)
    (d / "ROOT/opt/supervisor-scripts").mkdir(parents=True, exist_ok=True)
    (d / "Dockerfile").write_text(df)
    for name, body in (confs or {"foo": VALID_CONF}).items():
        (d / "ROOT/etc/supervisor/conf.d" / f"{name}.conf").write_text(body)
    for name, body in (scripts or {"foo.sh": VALID_SCRIPT}).items():
        (d / "ROOT/opt/supervisor-scripts" / name).write_text(body)
    return Image(name="img", cls=cls, dir=d, dockerfile=d / "Dockerfile", text=df, root=d / "ROOT")


def errs(img: Image, repo: Path) -> set[str]:
    return {f.code for f in lint_image(img, repo) if f.severity == ERROR}


def test_valid_image_is_clean(tmp_path):
    assert errs(make(tmp_path), tmp_path) == set()


def test_L001_label_count(tmp_path):
    df = VALID_DF.replace('LABEL maintainer="Vast.ai Inc <contact@vast.ai>"\n', "")
    assert "L001" in errs(make(tmp_path, df=df), tmp_path)


def test_L002_env_hash(tmp_path):
    df = VALID_DF.replace("RUN env-hash > /.env_hash\n", "")
    assert "L002" in errs(make(tmp_path, df=df), tmp_path)


def test_L003_copy_root(tmp_path):
    df = VALID_DF.replace("COPY ./ROOT /\n", "")
    assert "L003" in errs(make(tmp_path, df=df), tmp_path)


def test_L004_from_class(tmp_path):
    df = VALID_DF.replace("vastai/pytorch:test", "somethingelse:test")
    assert "L004" in errs(make(tmp_path, df=df), tmp_path)


def test_L020_torch_guard(tmp_path):
    df = VALID_DF.replace("torch_versions_pre", "x").replace("torch_versions_post", "y")
    assert "L020" in errs(make(tmp_path, df=df), tmp_path)


def test_L021_no_auto_backend(tmp_path):
    df = VALID_DF.replace("uv pip install foo", "uv pip install foo --torch-backend auto")
    assert "L021" in errs(make(tmp_path, df=df), tmp_path)


def test_L010_program_name_mismatch(tmp_path):
    # conf file is foo.conf but section says [program:bar]
    bad = VALID_CONF.replace("[program:foo]", "[program:bar]")
    assert "L010" in errs(make(tmp_path, confs={"foo": bad}), tmp_path)


def test_L011_util_order_inversion(tmp_path):
    bad = '. "${utils}/exit_portal.sh"\n. "${utils}/logging.sh"\n'
    assert "L011" in errs(make(tmp_path, scripts={"foo.sh": bad}), tmp_path)


def test_regression_net_real_repo_is_clean():
    """The real repo must lint clean — proves the invariants are real, not aspirational."""
    repo = find_repo_root(Path(__file__).resolve().parent)
    images = discover(repo)
    assert images, "no images discovered — wrong repo root?"
    offenders = {
        f"{i.cls}/{i.name}": [f"{f.code}:{f.msg}" for f in lint_image(i, repo) if f.severity == ERROR]
        for i in images
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, f"existing images violate gated invariants: {offenders}"


# ---- mutation-against-real-files: prove the checks actually bite ----
# (The regression net alone is vacuous — it would pass if every check were a
#  no-op. These corrupt REAL images and assert the corresponding code fires.)

def _real(name: str):
    repo = find_repo_root(Path(__file__).resolve().parent)
    for img in discover(repo):
        if img.name == name:
            return repo, img
    raise AssertionError(f"image {name!r} not found in repo")


def test_mut_env_hash_neutralized():
    repo, img = _real("comfyui")
    mut = replace(img, text=img.text.replace("env-hash > /.env_hash", "true"))
    assert "L002" in errs(mut, repo)


def test_mut_label_removed():
    repo, img = _real("comfyui")
    mut = replace(img, text=re.sub(r"(?m)^LABEL maintainer=.*\n", "", img.text))
    assert "L001" in errs(mut, repo)


def test_mut_external_stage_order_reversed():
    repo, img = _real("vllm")
    a, b = "FROM ${VAST_BASE} AS vast_base_image", "FROM ${VLLM_BASE} AS vllm_build"
    assert a in img.text and b in img.text
    mut = replace(img, text=img.text.replace(a, "__A__").replace(b, a).replace("__A__", b))
    assert "L004" in errs(mut, repo)


def test_mut_torch_guard_weakened():
    repo, img = _real("comfyui")
    mut = replace(img, text=img.text.replace('[[ "$torch_versions_pre" = "$torch_versions_post" ]]', "true"))
    assert "L020" in errs(mut, repo)


def test_mut_auto_backend_injected():
    repo, img = _real("comfyui")
    mut = replace(img, text=img.text + "\nRUN uv pip install x --torch-backend auto\n")
    assert "L021" in errs(mut, repo)


def test_mut_auto_backend_comment_backdoor_does_not_hide():
    """The old `'sed' in line` backdoor: `--torch-backend auto # sed` must still fire."""
    repo, img = _real("comfyui")
    mut = replace(img, text=img.text + "\nRUN uv pip install x --torch-backend auto # sed\n")
    assert "L021" in errs(mut, repo)


def test_mut_conf_command_basename_mismatch(tmp_path):
    repo, img = _real("comfyui")
    dst = tmp_path / "comfyui"
    shutil.copytree(img.dir, dst)
    conf = sorted((dst / "ROOT/etc/supervisor/conf.d").glob("*.conf"))[0]
    text = conf.read_text()
    text2 = re.sub(r"command=/opt/supervisor-scripts/\S+\.sh",
                   "command=/opt/supervisor-scripts/wrong.sh", text, count=1)
    assert text2 != text
    conf.write_text(text2)
    mut = replace(img, dir=dst, root=dst / "ROOT", dockerfile=dst / "Dockerfile",
                  text=(dst / "Dockerfile").read_text())
    assert "L010" in errs(mut, repo)


def test_no_stale_exceptions():
    """Every EXCEPTION must still be triggered by its image (scoped to its msg)."""
    by_name = {i.name: i for i in discover(find_repo_root(Path(__file__).resolve().parent))}
    repo = find_repo_root(Path(__file__).resolve().parent)
    for (name, code), (reason, sub) in EXCEPTIONS.items():
        img = by_name.get(name)
        assert img, f"exception references missing image {name!r}"
        raw = lint_image(img, repo, apply_exceptions=False)
        assert any(f.code == code and sub in f.msg for f in raw), \
            f"stale exception {name}/{code}: no longer triggers (suppressing nothing)"


if __name__ == "__main__":
    # stdlib fallback runner (this environment has no pytest); CI uses pytest.
    import inspect, tempfile, traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            if inspect.signature(fn).parameters:
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            print("PASS", fn.__name__)
        except Exception:
            failed += 1
            print("FAIL", fn.__name__)
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
