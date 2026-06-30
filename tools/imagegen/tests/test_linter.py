"""Linter tests: a regression net over the real repo + one mutant per invariant.

Run: cd tools/imagegen && PYTHONPATH=. python -m pytest -q
"""
import re
import shutil
from dataclasses import replace
from pathlib import Path

import imagegen.linter as L
from imagegen.discover import Image, discover, find_repo_root
from imagegen.dockerfile import parse
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


def has(img: Image, repo: Path, code: str, sub: str = "") -> bool:
    return any(f.code == code and sub in f.msg
               for f in lint_image(img, repo) if f.severity == ERROR)


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


def _write_template(img, body):
    tdir = img.dir / "templates" / "qa"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "template.yml").write_text(body)


def test_L050_template_missing_compute_cap_floor(tmp_path):
    img = make(tmp_path)
    _write_template(img, "name: QA\nimage: vastai/x\nextra_filters:\n  gpu_total_ram:\n    gte: 8192\n")
    assert "L050" in errs(img, tmp_path)


def test_L050_template_with_compute_cap_floor_is_clean(tmp_path):
    img = make(tmp_path)
    _write_template(img, "name: QA\nimage: vastai/x\nextra_filters:\n  compute_cap:\n    gte: 700\n")
    assert "L050" not in errs(img, tmp_path)


def test_L050_no_templates_dir_is_clean(tmp_path):
    # The rule is conditional: images without a templates/ dir are unaffected.
    assert "L050" not in errs(make(tmp_path), tmp_path)


def test_L050_null_floor_value_is_rejected(tmp_path):
    # A key-only floor ({gte: null}) lints clean under presence-only checks but the
    # tester can't parse it -> must fire L050.
    img = make(tmp_path)
    _write_template(img, "name: QA\nimage: vastai/x\nextra_filters:\n  compute_cap:\n    gte: null\n")
    assert "L050" in errs(img, tmp_path)


def test_L050_nonnumeric_floor_value_is_rejected(tmp_path):
    img = make(tmp_path)
    _write_template(img, "name: QA\nimage: vastai/x\nextra_filters:\n  compute_cap:\n    gte: high\n")
    assert "L050" in errs(img, tmp_path)


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


def test_mut_torch_guard_action_removed():
    """Keep the [[ pre = post ]] comparison but delete its `|| {...exit}` action.
    The unrelated REF-guard `exit 1` must NOT satisfy L020 (the prior cosmetic bug)."""
    repo, img = _real("comfyui")
    mut_text = re.sub(
        r'(\[\[ "\$torch_versions_pre" = "\$torch_versions_post" \]\])\s*\|\|\s*\{[^}]*\}',
        r"\1", img.text)
    assert mut_text != img.text and "torch_versions_pre" in mut_text  # comparison kept
    assert has(replace(img, text=mut_text), repo, "L020")


def test_mut_external_base_identity_decoy():
    """Wrong base + a decoy `vastai/base-image` elsewhere must still fail L004."""
    repo, img = _real("vllm")
    assert "ARG VAST_BASE=" in img.text
    t = re.sub(r"ARG VAST_BASE=\S+", "ARG VAST_BASE=evil/img:latest", img.text)
    t += "\nENV DECOY=vastai/base-image\n"
    assert has(replace(img, text=t), repo, "L004", "must resolve to vastai/base-image")


def test_mut_copy_root_removed():
    repo, img = _real("comfyui")
    mut = replace(img, text=re.sub(r"(?m)^\s*COPY \./ROOT/? /\s*$", "", img.text))
    assert "L003" in errs(mut, repo)


def test_mut_util_order_real(tmp_path):
    repo, img = _real("comfyui")
    dst = tmp_path / "comfyui"
    shutil.copytree(img.dir, dst)
    sdir = dst / "ROOT/opt/supervisor-scripts"
    target = next(s for s in sorted(sdir.glob("*.sh")) if "logging.sh" in s.read_text())
    target.write_text('. "${utils}/exit_portal.sh"\n' + target.read_text())  # inversion
    mut = replace(img, dir=dst, root=dst / "ROOT", dockerfile=dst / "Dockerfile",
                  text=(dst / "Dockerfile").read_text())
    assert "L011" in errs(mut, repo)


def test_L001_consolidated_label_not_false_fail(tmp_path):
    """One LABEL with 3 key=value pairs is legal Docker and must NOT trip L001."""
    df = VALID_DF.replace(
        'LABEL org.opencontainers.image.source="https://github.com/vastai/"\n'
        'LABEL org.opencontainers.image.description="Test suitable for Vast.ai."\n'
        'LABEL maintainer="Vast.ai Inc <contact@vast.ai>"\n',
        'LABEL org.opencontainers.image.source="https://github.com/vastai/" '
        'org.opencontainers.image.description="Test suitable for Vast.ai." '
        'maintainer="Vast.ai Inc <contact@vast.ai>"\n')
    assert "L001" not in errs(make(tmp_path, df=df), tmp_path)


def test_parser_heredoc_final_run(tmp_path):
    """env-hash via a heredoc final RUN must be recognised (no false L002, no leaked fake instrs)."""
    df = VALID_DF.replace("RUN env-hash > /.env_hash\n",
                          "RUN <<EOF\nenv-hash > /.env_hash\nEOF\n")
    assert errs(make(tmp_path, df=df), tmp_path) == set()


def test_parser_comment_in_continuation(tmp_path):
    """A # comment inside a \\ continuation must not corrupt the instruction stream."""
    df = VALID_DF.replace("COPY ./ROOT /\n",
                          "RUN echo a \\\n# a comment\n    && echo b\nCOPY ./ROOT /\n")
    assert errs(make(tmp_path, df=df), tmp_path) == set()


def test_mut_external_base_wrong_registry():
    """A look-alike on a different registry (canonical substring IN the ref) must fail L004."""
    repo, img = _real("vllm")
    t = re.sub(r"ARG VAST_BASE=\S+", "ARG VAST_BASE=evilregistry.io/vastai/base-image:latest", img.text)
    assert has(replace(img, text=t), repo, "L004", "must resolve to vastai/base-image")


def test_mut_external_base_shell_default_form():
    """`${VAST_BASE:-vastai/base-image}` with an evil ARG default must fail L004."""
    repo, img = _real("vllm")
    t = img.text.replace("FROM ${VAST_BASE} AS vast_base_image",
                         "FROM ${VAST_BASE:-vastai/base-image} AS vast_base_image")
    t = re.sub(r"ARG VAST_BASE=\S+", "ARG VAST_BASE=evil/img:latest", t)
    assert has(replace(img, text=t), repo, "L004", "must resolve to vastai/base-image")


def test_L020_accepts_negated_and_swapped(tmp_path):
    """Valid guard variants (!= with &&, swapped operands) must NOT false-fail L020."""
    for cmp in ('[[ "$torch_versions_pre" != "$torch_versions_post" ]] && exit 1',
                '[[ "$torch_versions_post" = "$torch_versions_pre" ]] || exit 1'):
        df = VALID_DF.replace(
            '[ "$torch_versions_pre" = "$torch_versions_post" ] || exit 1', cmp)
        assert "L020" not in errs(make(tmp_path, df=df), tmp_path), cmp


def test_L001_equals_in_value_not_miscounted(tmp_path):
    """A `word=` inside a quoted LABEL value must not inflate the pair count."""
    df = VALID_DF.replace(
        'LABEL org.opencontainers.image.description="Test suitable for Vast.ai."\n',
        'LABEL org.opencontainers.image.description="Test sigma=0.7 res=512 suitable for Vast.ai."\n')
    assert "L001" not in errs(make(tmp_path, df=df), tmp_path)


def test_parser_plain_heredoc_indented_terminator_not_early():
    """Plain <<EOF must NOT terminate on an indented EOF (Docker requires column 0)."""
    text = "FROM x\nRUN cat <<EOF >/dev/null\n    EOF\necho real\nEOF\nCOPY ./ROOT /\n"
    assert [i.cmd for i in parse(text)] == ["FROM", "RUN", "COPY"]  # `echo real` stayed inside


_GUARD_IN_DISCARDED_HEREDOC = """\
ARG PYTORCH_BASE=vastai/pytorch:test
FROM ${PYTORCH_BASE}
LABEL org.opencontainers.image.source="https://github.com/vastai/"
LABEL org.opencontainers.image.description="Test suitable for Vast.ai."
LABEL maintainer="Vast.ai Inc <contact@vast.ai>"
COPY ./ROOT /
RUN cat <<EOF >/dev/null
[[ "$torch_versions_pre" = "$torch_versions_post" ]] || exit 1
EOF
RUN uv pip install foo
RUN env-hash > /.env_hash
"""


def test_heredoc_data_guard_does_not_satisfy_L020(tmp_path):
    """A torch guard hidden in a discarded `cat <<EOF >/dev/null` body is not executed,
    so L020 must still fire (it isn't in the executed shell)."""
    assert "L020" in errs(make(tmp_path, df=_GUARD_IN_DISCARDED_HEREDOC), tmp_path)


def test_heredoc_data_env_hash_does_not_satisfy_L002(tmp_path):
    df = VALID_DF.replace(
        "RUN env-hash > /.env_hash\n",
        "RUN echo done\nRUN cat <<EOF >/dev/null\nenv-hash > /.env_hash\nEOF\n")
    assert "L002" in errs(make(tmp_path, df=df), tmp_path)


def test_heredoc_fed_to_shell_is_executed_L021(tmp_path):
    """`RUN bash <<EOF` executes its body, so a forbidden auto-backend there must fire L021."""
    df = VALID_DF.replace(
        "RUN env-hash > /.env_hash\n",
        "RUN bash <<EOF\nuv pip install x --torch-backend auto\nEOF\nRUN env-hash > /.env_hash\n")
    assert "L021" in errs(make(tmp_path, df=df), tmp_path)


def test_heredoc_fed_to_dot_stdin_is_executed_L021(tmp_path):
    """`. /dev/stdin <<EOF` also executes the body — the stealthier variant."""
    df = VALID_DF.replace(
        "RUN env-hash > /.env_hash\n",
        "RUN . /dev/stdin <<EOF\nuv pip install x --torch-backend auto\nEOF\nRUN env-hash > /.env_hash\n")
    assert "L021" in errs(make(tmp_path, df=df), tmp_path)


def test_rules_catalog_matches_emitted_codes():
    """ADR cond #2: the RULES catalog is authoritative — every code a check emits must
    be cataloged, and the catalog must not list codes no check emits."""
    src = Path(L.__file__).read_text()
    emitted = set(re.findall(r'Finding\("(L\d+)"', src))
    catalog = {code for code, _, _ in L.RULES}
    assert emitted == catalog, f"drift: emitted-not-cataloged={emitted - catalog}, cataloged-not-emitted={catalog - emitted}"


def test_lint_rules_doc_in_sync():
    """ADR cond #2: docs/lint-rules.md is generated from the linter; fail on drift."""
    repo = find_repo_root(Path(__file__).resolve().parent)
    doc = (repo / "docs" / "lint-rules.md").read_text()
    assert doc == L.rules_markdown(), "docs/lint-rules.md is stale — run `imagegen rules > docs/lint-rules.md`"


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
