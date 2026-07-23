"""Linter tests: a regression net over the real repo + one mutant per invariant.

Run: cd tools/imagegen && PYTHONPATH=. python -m pytest -q
"""
import re
import shutil
from dataclasses import replace
from pathlib import Path

import imagegen.linter as L
from imagegen.discover import Image, discover, find_repo_root
from imagegen.dockerfile import parse, stages
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
        sp = (d / "ROOT/opt/supervisor-scripts" / name)
        sp.write_text(body)
        sp.chmod(0o755)   # supervisor scripts must be executable (L051)
    return Image(name="img", cls=cls, dir=d, dockerfile=d / "Dockerfile", text=df, root=d / "ROOT")


def errs(img: Image, repo: Path) -> set[str]:
    return {f.code for f in lint_image(img, repo) if f.severity == ERROR}


def has(img: Image, repo: Path, code: str, sub: str = "") -> bool:
    return any(f.code == code and sub in f.msg
               for f in lint_image(img, repo) if f.severity == ERROR)


def test_valid_image_is_clean(tmp_path):
    assert errs(make(tmp_path), tmp_path) == set()


def test_L005_floating_base_tag_fires(tmp_path):
    for bad in ("vastai/pytorch:latest", "vastai/pytorch"):   # latest / untagged
        df = f"ARG PYTORCH_BASE={bad}\nFROM ${{PYTORCH_BASE}}\nCOPY ./ROOT /\n"
        assert "L005" in errs(make(tmp_path, cls="pytorch-nested", df=df), tmp_path), bad


def test_L005_concrete_pin_is_clean(tmp_path):
    for good in ("vastai/pytorch:2.10.0-cu128-cuda-12.9-mini-py312-2026-06-15",
                 "vastai/pytorch@sha256:" + "a" * 64):      # a digest is the strongest pin
        df = f"ARG PYTORCH_BASE={good}\nFROM ${{PYTORCH_BASE}}\nCOPY ./ROOT /\n"
        assert "L005" not in errs(make(tmp_path, cls="pytorch-nested", df=df), tmp_path), good


def test_L005_changeme_is_l040_not_l005(tmp_path):
    # the scaffold placeholder is L040's job; L005 must not double-fire on it
    df = "ARG PYTORCH_BASE=vastai/pytorch:CHANGEME\nFROM ${PYTORCH_BASE}\nCOPY ./ROOT /\n"
    assert "L005" not in errs(make(tmp_path, cls="pytorch-nested", df=df), tmp_path)


def test_L005_not_applied_to_external(tmp_path):
    df = "FROM someupstream:latest AS vast_base_image\nFROM x\nCOPY ./ROOT /\n"
    assert "L005" not in errs(make(tmp_path, cls="external", df=df), tmp_path)


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


def test_L054_misspelled_vram_key_fires(tmp_path):
    img = make(tmp_path)
    _write_template(img, "name: QA\nimage: vastai/x\nextra_filters:\n"
                         "  compute_cap:\n    gte: 700\n  gpu_vram:\n    gte: 24000\n")
    assert "L054" in errs(img, tmp_path)


def test_L054_key_only_vram_floor_fires(tmp_path):
    img = make(tmp_path)
    _write_template(img, "name: QA\nimage: vastai/x\nextra_filters:\n"
                         "  compute_cap:\n    gte: 700\n  gpu_ram:\n    lte: 40000\n")  # no gte/gt/eq
    assert "L054" in errs(img, tmp_path)


def test_L054_valid_vram_floor_is_clean(tmp_path):
    img = make(tmp_path)
    _write_template(img, "name: QA\nimage: vastai/x\nextra_filters:\n"
                         "  compute_cap:\n    gte: 700\n  gpu_total_ram:\n    gte: 24000\n")
    assert "L054" not in errs(img, tmp_path)


def test_L054_absent_vram_floor_is_clean(tmp_path):
    # Presence is OPTIONAL — a multi-model host omits it (qa supplies the floor). Not an L054.
    img = make(tmp_path)
    _write_template(img, "name: QA\nimage: vastai/x\nextra_filters:\n  compute_cap:\n    gte: 700\n")
    assert "L054" not in errs(img, tmp_path)


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


def test_parser_dash_heredoc_space_terminator_not_early():
    """<<-EOF strips only leading *tabs*; a space-indented EOF must NOT close it early
    (else `echo real` would be misparsed as a top-level instruction)."""
    text = "FROM x\nRUN cat <<-EOF >/dev/null\n    EOF\necho real\n\tEOF\nCOPY ./ROOT /\n"
    assert [i.cmd for i in parse(text)] == ["FROM", "RUN", "COPY"]  # tab-indented EOF closes it


def test_parser_herestring_not_mistaken_for_heredoc():
    """A `<<<` here-string must NOT open a phantom heredoc that swallows following lines
    (regression: `<<word` matched inside `<<< word`, consuming the rest of the Dockerfile)."""
    text = "FROM x\nRUN read v <<< foo\nCOPY ./ROOT /\nRUN env-hash > /.env_hash\n"
    assert [i.cmd for i in parse(text)] == ["FROM", "RUN", "COPY", "RUN"]


def test_parser_from_platform_flag_skipped_in_stages():
    """`FROM --platform=... img AS alias` must yield (img, alias); the flag is skipped
    (regression: the flag was read as the ref and the alias lost, blinding L004/L005)."""
    assert stages(parse("FROM --platform=$BUILDPLATFORM vastai/pytorch:2026-06-10 AS base\n")) \
        == [("vastai/pytorch:2026-06-10", "base")]
    assert stages(parse("FROM --platform=linux/amd64 golang:1.23 AS b\nFROM alpine\n")) \
        == [("golang:1.23", "b"), ("alpine", None)]


def test_parser_python_heredoc_body_is_executed_code_L053(tmp_path):
    """A model download inside `RUN python <<EOF ... EOF` IS executed code, so L053 must
    scan the body (regression: `python` was absent from the stdin-exec interpreter set, so
    a `snapshot_download(...)` there baked weights past the gate)."""
    baked = VALID_DF.replace("RUN env-hash > /.env_hash\n",
        'RUN python3 <<PYEOF\nfrom huggingface_hub import snapshot_download\n'
        'snapshot_download("org/model")\nPYEOF\nRUN env-hash > /.env_hash\n')
    assert has(make(tmp_path, df=baked), tmp_path, "L053", "baked model weights")


def test_parser_data_heredoc_body_not_executed_code_L053(tmp_path):
    """Contrast: a heredoc fed to a NON-interpreter (`cat`) is data, not code — a
    model-download string there must NOT trip L053."""
    df = VALID_DF.replace("RUN env-hash > /.env_hash\n",
        'RUN cat <<DATA >/opt/note.txt\nsnapshot_download("org/model")\nDATA\n'
        'RUN env-hash > /.env_hash\n')
    assert not has(make(tmp_path, df=df), tmp_path, "L053")


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


# --- L041: no hardcoded staging namespace in a new image's committed files ----

def test_L041_flags_hardcoded_staging_namespace(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKERHUB_NAMESPACE_STAGING", "acmestaging")
    df = VALID_DF.replace("uv pip install foo", "uv pip install foo  # see acmestaging/tooling")
    assert "L041" in errs(make(tmp_path, df=df), tmp_path)


def test_L041_ignores_secret_reference(tmp_path, monkeypatch):
    # The secret-reference form (what scaffolds/workflows use) must NOT trip L041.
    monkeypatch.setenv("DOCKERHUB_NAMESPACE_STAGING", "acmestaging")
    df = VALID_DF + "# push to ${{ secrets.DOCKERHUB_NAMESPACE_STAGING }}/img\n"
    assert "L041" not in errs(make(tmp_path, df=df), tmp_path)


def test_L041_does_not_flag_prod_namespace(tmp_path, monkeypatch):
    # Prod namespace is the public product users pull; only staging is matched.
    monkeypatch.setenv("DOCKERHUB_NAMESPACE_STAGING", "acmestaging")
    assert "L041" not in errs(make(tmp_path), tmp_path)   # VALID_DF FROMs vastai/pytorch


def test_L041_warns_not_errors_when_env_unset(tmp_path, monkeypatch):
    # Unset -> the check can't run, but it must WARN (visible), never silently skip,
    # and never ERROR (which would false-gate a legitimate image).
    monkeypatch.delenv("DOCKERHUB_NAMESPACE_STAGING", raising=False)
    df = VALID_DF.replace("uv pip install foo", "uv pip install foo  # acmestaging/x")
    findings = lint_image(make(tmp_path, df=df), tmp_path)
    assert "L041" not in {f.code for f in findings if f.severity == ERROR}
    assert "L041" in {f.code for f in findings if f.severity == "WARN"}


def test_L041_grandfathers_staging_based_image(tmp_path, monkeypatch):
    # aio-studio legitimately builds FROM a staging-account base (invariants §2), so it
    # must not false-gate even with the namespace set.
    monkeypatch.setenv("DOCKERHUB_NAMESPACE_STAGING", "acmestaging")
    df = VALID_DF.replace("uv pip install foo", "uv pip install foo  # acmestaging/x")
    img = replace(make(tmp_path, df=df), name="aio-studio")
    assert "L041" not in errs(img, tmp_path)


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




def test_mut_baked_weights_L053(tmp_path):
    """L053 — a model download baked into a RUN trips the gate; a COMMENTED one does not
    (instruction-aware via code_text); the clean baseline Dockerfile is L053-clean."""
    assert not has(make(tmp_path), tmp_path, "L053")                        # clean baseline

    baked = VALID_DF.replace("    uv pip install foo; \\",
        "    uv pip install foo; \\\n    hf download org/model model.safetensors -d /opt/models; \\")
    assert has(make(tmp_path, df=baked), tmp_path, "L053", "baked model weights")

    wget = VALID_DF.replace("    uv pip install foo; \\",
        "    uv pip install foo; \\\n    wget -O /opt/models/m.gguf https://example/m.gguf; \\")
    assert has(make(tmp_path, df=wget), tmp_path, "L053")                   # weight file via wget

    commented = VALID_DF.replace("    uv pip install foo; \\",
        "    uv pip install foo; \\\n    # hf download org/model model.safetensors; \\")
    assert not has(make(tmp_path, df=commented), tmp_path, "L053")          # comment must not fire


_EXT_ENV_GOOD = ("FROM ${VAST_BASE} AS vast_base_image\nFROM ${FOO_BASE} AS foo_build\n"
                 "ENV TCLLIBPATH=/usr/lib/tcltk/default \\\n"
                 "    PATH=/opt/instance-tools/bin:/opt/sys-venv/shim:$PATH\nCOPY ./ROOT /\n")


def test_L055_external_missing_tcllibpath_fires(tmp_path):
    df = ("FROM ${VAST_BASE} AS vast_base_image\nFROM ${FOO_BASE} AS foo_build\n"
          "ENV PATH=/opt/instance-tools/bin:$PATH\nCOPY ./ROOT /\n")   # no TCLLIBPATH -> fires
    assert "L055" in errs(make(tmp_path, cls="external", df=df), tmp_path)


def test_L055_external_with_tcllibpath_is_clean(tmp_path):
    assert "L055" not in errs(make(tmp_path, cls="external", df=_EXT_ENV_GOOD), tmp_path)


def test_L055_external_wrong_tcllibpath_value_fires(tmp_path):
    # a set-but-wrong value still breaks unbuffer/Expect at boot, so it must NOT lint clean
    df = ("FROM ${VAST_BASE} AS vast_base_image\nFROM ${FOO_BASE} AS foo_build\n"
          "ENV TCLLIBPATH=/tmp\nCOPY ./ROOT /\n")
    assert "L055" in errs(make(tmp_path, cls="external", df=df), tmp_path)


def test_L055_shim_on_path_is_not_required(tmp_path):
    # vllm-omni case: TCLLIBPATH set but no /opt/sys-venv/shim on PATH (10-prep-env.sh adds it at
    # runtime) -> a working image, must stay clean. The shim is convention, not a gated invariant.
    df = ("FROM ${VAST_BASE} AS vast_base_image\nFROM ${FOO_BASE} AS foo_build\n"
          "ENV TCLLIBPATH=/usr/lib/tcltk/default\nENV PATH=/opt/instance-tools/bin:$PATH\nCOPY ./ROOT /\n")
    assert "L055" not in errs(make(tmp_path, cls="external", df=df), tmp_path)


def test_L055_not_applied_to_non_external(tmp_path):
    # pytorch-nested FROMs our base and inherits its ENV, so the rule does not apply
    df = "FROM ${PYTORCH_BASE}\nENV PATH=/opt/instance-tools/bin:$PATH\nCOPY ./ROOT /\n"
    assert "L055" not in errs(make(tmp_path, cls="pytorch-nested", df=df), tmp_path)


def _write_adr(tmp_path, body):
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True, exist_ok=True)
    (adr / "0099-x.md").write_text("# ADR 0099 — test\n\n" + body + "\n")
    return tmp_path


def test_L060_private_key_in_adr_fires(tmp_path):
    repo = _write_adr(tmp_path, "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc...\n-----END RSA PRIVATE KEY-----")
    assert "L060" in {f.code for f in L.lint_repo(repo)}


def test_L060_credential_assignment_fires(tmp_path):
    # a secret-named field set to a literal high-entropy value (mixed case + digits)
    repo = _write_adr(tmp_path, "config: api_key=aB3xK9pQ2rT5uV8wY1zC and then more prose")
    assert "L060" in {f.code for f in L.lint_repo(repo)}


def test_L060_prose_and_env_refs_are_clean(tmp_path):
    # words token/key/secret in prose, an ENV-var reference, and a placeholder must NOT fire
    repo = _write_adr(tmp_path,
        "The QA key is never passed via --env; the token is short-lived.\n"
        "Reference the VAST_API_KEY secret; set password: <REDACTED> and api_key=${SOME_ENV}.")
    assert "L060" not in {f.code for f in L.lint_repo(repo)}


def test_L060_baseline_adrs_are_clean():
    repo = find_repo_root(Path(__file__).resolve().parent)
    offenders = [f for f in L.lint_repo(repo) if f.code == "L060"]
    assert not offenders, f"real ADR carries a credential-shaped secret: {[f.path for f in offenders]}"


def test_L061_internal_ticket_id_fires(tmp_path):
    # build the ticket token at runtime so THIS test file carries no literal id
    # (the repo-wide scanner would otherwise flag itself)
    ticket = "CON" + "-" + "1585"
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.md").write_text(f"# notes\n\nThis was tracked in {ticket} originally.\n")
    assert "L061" in {f.code for f in L.lint_repo(tmp_path)}


def test_L061_public_refs_are_not_tickets(tmp_path):
    # CVE-/RFC-/version-style refs are public and must NOT fire — only the internal prefix set does
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.md").write_text("# notes\n\nCVE-2025-1234 and RFC-2119 are public refs.\n")
    assert "L061" not in {f.code for f in L.lint_repo(tmp_path)}


def test_L061_baseline_repo_is_clean():
    repo = find_repo_root(Path(__file__).resolve().parent)
    offenders = [f for f in L.lint_repo(repo) if f.code == "L061"]
    assert not offenders, f"internal ticket id in a public file: {[f.path for f in offenders]}"


def test_L056_real_unsloth_studio_asserts_cuda_backend():
    """The shipping unsloth-studio image force-builds CUDA llama.cpp and asserts the
    backend artifact, so L056 must NOT fire on it."""
    repo, img = _real("unsloth-studio")
    assert "unsloth studio setup" in img.text and "libggml-cuda.so" in img.text
    assert "L056" not in errs(img, repo)


def test_mut_llama_cuda_assert_removed():
    """Drop the CUDA-backend assertion from the real unsloth-studio Dockerfile: the
    GPU-less build would silently ship a CPU-only binary, so L056 must fire."""
    repo, img = _real("unsloth-studio")
    mut = replace(img, text=img.text.replace("libggml-cuda.so", "libggml-cpu.so"))
    assert "L056" in errs(mut, repo)


def test_L056_no_unsloth_setup_is_clean(tmp_path):
    """An image that never runs `unsloth studio setup` is out of scope for L056."""
    assert "L056" not in errs(make(tmp_path), tmp_path)


if __name__ == "__main__":
    from _stdlib_runner import run
    raise SystemExit(run(globals()))
