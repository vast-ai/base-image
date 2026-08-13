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
from imagegen.linter import lint_image, ERROR, EXCEPTIONS, RULES

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


def test_mut_llama_cuda_substring_backdoor_does_not_hide():
    """A bare mention of the filename (e.g. `echo libggml-cuda.so`) must NOT satisfy
    L056 — only a real `-f` existence assertion counts. Downgrade the real assertion
    to an echo and confirm the rule still fires."""
    repo, img = _real("unsloth-studio")
    mut = replace(img, text=re.sub(
        r"(?:test|\[\[?)\s+-f\s+\S*libggml-cuda\.so",
        "echo libggml-cuda.so", img.text))
    assert "L056" in errs(mut, repo)


if __name__ == "__main__":
    from _stdlib_runner import run
    raise SystemExit(run(globals()))


# ---- L057: a gating QA template must demand its tests actually ran (ADR 0019) ----

_TRIO = "base/60-gpu-cuda base/61-cuda-compute base/62-gpu-libraries"
_FLOORS = "extra_filters:\n  compute_cap:\n    gte: 750\n"


def _base_qa(img, env_line):
    _write_template(img, f"name: Base QA\nimage: vastai/base-image\n{env_line}{_FLOORS}")


def test_L057_base_qa_template_without_require_pass_fires(tmp_path):
    """THE mutation: strip the declaration and the rule must fire. Without it a
    self-skipping GPU test reports the suite green and the gate certifies an
    image it never exercised."""
    img = make(tmp_path, cls="base")
    _base_qa(img, "")
    assert has(img, tmp_path, "L057", "no env.INSTANCE_TEST_REQUIRE_PASS")


def test_L057_partial_require_pass_fires_and_names_the_gap(tmp_path):
    """Declaring only some of the trio is the subtler regression — the template
    looks configured while the omitted test can still skip green."""
    img = make(tmp_path, cls="base")
    _base_qa(img, "env:\n  INSTANCE_TEST_REQUIRE_PASS: base/60-gpu-cuda\n")
    assert has(img, tmp_path, "L057", "base/61-cuda-compute")


def test_L057_full_trio_is_clean(tmp_path):
    img = make(tmp_path, cls="base")
    _base_qa(img, f"env:\n  INSTANCE_TEST_REQUIRE_PASS: {_TRIO}\n")
    assert "L057" not in errs(img, tmp_path)


def test_L057_accepts_comma_separated(tmp_path):
    img = make(tmp_path, cls="base")
    _base_qa(img, "env:\n  INSTANCE_TEST_REQUIRE_PASS: "
                  "base/60-gpu-cuda,base/61-cuda-compute,base/62-gpu-libraries\n")
    assert "L057" not in errs(img, tmp_path)


def test_L057_is_scoped_to_base_for_now(tmp_path):
    """comfyui-qa/vllm-qa have the same hole, but widening the rule to them must
    re-validate two live, currently-passing gates — not something a linter rule
    should do silently. Scope is deliberate; this pins it."""
    img = make(tmp_path, cls="pytorch-nested")
    _write_template(img, f"name: QA\nimage: vastai/x\n{_FLOORS}")
    assert "L057" not in errs(img, tmp_path)


def test_L050_L054_now_apply_to_base(tmp_path):
    """Base was exempt from the template rules until it had a template of its own
    (ADR 0019). A floor-less base QA template must fire L050."""
    img = make(tmp_path, cls="base")
    _write_template(img, "name: Base QA\nimage: vastai/base-image\n"
                         f"env:\n  INSTANCE_TEST_REQUIRE_PASS: {_TRIO}\n"
                         "extra_filters:\n  gpu_ram:\n    gte: 8192\n")
    assert "L050" in errs(img, tmp_path)


# ---- L059: a REQUIRED test must be able to fail (ADR 0019) ----------------
#
# L057 makes the template name the tests that must pass. This is the next hole
# down, and it was a live defect when the rule was written: base/62-gpu-libraries
# was named in base-qa's INSTANCE_TEST_REQUIRE_PASS and contained no failure path
# at all — every check was `echo WARN` — so it reported `passed` on every box and
# the gate's third required test asserted nothing beyond `has_gpu`.

def _write_test(repo: Path, name: str, body: str) -> None:
    p = repo / "ROOT/opt/instance-tools/tests" / f"{name}.sh"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


_CAN_FAIL = 'source lib.sh\nhas_gpu || test_skip "no gpu"\n' \
            'if ! thing; then\n    fail_later "thing" "broke"\nfi\nreport_failures\ntest_pass "ok"\n'
_CANNOT_FAIL = 'source lib.sh\nhas_gpu || test_skip "no gpu"\n' \
               'if ! thing; then\n    echo "  WARN: thing broke"\nfi\ntest_pass "ok"\n'


def _qa_requiring(img, repo, name, body):
    _write_test(repo, name, body)
    _base_qa(img, f"env:\n  INSTANCE_TEST_REQUIRE_PASS: {name}\n")


def test_L059_a_required_test_that_cannot_fail_fires(tmp_path):
    """THE mutation, and a reproduction of the real defect: every check is a
    warning, so the script always reaches test_pass."""
    img = make(tmp_path, cls="base")
    _qa_requiring(img, tmp_path, "base/62-gpu-libraries", _CANNOT_FAIL)
    assert has(img, tmp_path, "L059", "cannot fail")


def test_L059_a_required_test_with_a_failure_path_is_clean(tmp_path):
    img = make(tmp_path, cls="base")
    _qa_requiring(img, tmp_path, "base/62-gpu-libraries", _CAN_FAIL)
    assert "L059" not in errs(img, tmp_path)


def test_L059_test_fail_also_counts(tmp_path):
    """Both spellings are real failure paths: fail_later defers to
    report_failures, test_fail exits immediately."""
    img = make(tmp_path, cls="base")
    _qa_requiring(img, tmp_path, "base/60-gpu-cuda",
                  'source lib.sh\nif ! thing; then\n    test_fail "broke"\nfi\ntest_pass "ok"\n')
    assert "L059" not in errs(img, tmp_path)


def test_L059_a_comment_mentioning_fail_later_does_not_count(tmp_path):
    """The exact trap the real file set. base/62-gpu-libraries carried

        # FAILURES and fail_later/report_failures come from lib.sh

    and no call — so a rule matching the substring would have been satisfied by
    the comment describing machinery the file never used, and reported the
    defect as clean. Same shape as L056's 'a bare mention does not count'."""
    img = make(tmp_path, cls="base")
    _qa_requiring(img, tmp_path, "base/62-gpu-libraries",
                  "source lib.sh\n"
                  "# FAILURES and fail_later/report_failures come from lib.sh\n"
                  '# we could test_fail here one day\n'
                  'echo "  WARN: thing broke"\ntest_pass "ok"\n')
    assert has(img, tmp_path, "L059", "cannot fail")


def test_L059_a_trailing_comment_does_not_mask_a_real_call(tmp_path):
    """The inverse error: stripping comments must not eat a real call that
    happens to have one after it."""
    img = make(tmp_path, cls="base")
    _qa_requiring(img, tmp_path, "base/60-gpu-cuda",
                  'source lib.sh\nfail_later "broke"   # deferred, see report_failures\n'
                  "report_failures\ntest_pass \"ok\"\n")
    assert "L059" not in errs(img, tmp_path)


def test_L059_ignores_a_required_test_absent_from_the_base_overlay(tmp_path):
    """A derivative's own test (e.g. pytorch/10-torch-core) does not live in
    ROOT/. The runner fails closed on a genuinely missing required test
    ('missing from this image'), which is the right layer for that."""
    img = make(tmp_path, cls="base")
    _base_qa(img, "env:\n  INSTANCE_TEST_REQUIRE_PASS: pytorch/10-torch-core\n")
    assert "L059" not in errs(img, tmp_path)


def test_L059_is_scoped_to_base(tmp_path):
    """Follows L057's precedent: comfyui-qa and vllm-qa are live gates and
    turning them red from a linter rule is a separate, re-validated change."""
    img = make(tmp_path, cls="pytorch-nested")
    _qa_requiring(img, tmp_path, "base/62-gpu-libraries", _CANNOT_FAIL)
    assert "L059" not in errs(img, tmp_path)


# ---- L065: a shipped instance test must be executable --------------------
#
# runner.sh collects with `find … -executable` and the Dockerfile ships the
# overlay with a bare COPY, so a 0644 test is not skipped and not reported
# missing — it emits no line at all. base/11 and base/12 shipped 0644 from their
# first commit and had never run once; nothing in the suite, the runner or the
# verdict could see it.

def test_L065_a_non_executable_test_fires(tmp_path):
    """THE mutation, and a reproduction of the real defect."""
    img = make(tmp_path, cls="base")
    p = tmp_path / "ROOT/opt/instance-tools/tests/base/99-thing.sh"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("source lib.sh\ntest_pass ok\n")
    p.chmod(0o644)
    assert has(img, tmp_path, "L065", "never run")


def test_L065_an_executable_test_is_clean(tmp_path):
    img = make(tmp_path, cls="base")
    p = tmp_path / "ROOT/opt/instance-tools/tests/base/99-thing.sh"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("source lib.sh\ntest_pass ok\n")
    p.chmod(0o755)
    assert "L065" not in errs(img, tmp_path)


def test_L065_lib_sh_is_exempt_because_it_is_sourced_not_executed(tmp_path):
    """lib.sh is sourced by every test, so its mode is irrelevant and demanding +x
    would be a false positive. (The shipped file happens to be 0755; the exemption
    is about how it is USED, not about what mode it currently carries.) runner.sh
    sits in the same directory and is deliberately NOT exempt — it is executed,
    and losing +x on it disables the whole suite."""
    img = make(tmp_path, cls="base")
    p = tmp_path / "ROOT/opt/instance-tools/tests/lib.sh"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("test_pass() { exit 0; }\n")
    p.chmod(0o644)
    assert "L065" not in errs(img, tmp_path)


def test_mut_L065_the_real_suite_chmodded_back_fires(tmp_path):
    """Mutation against a COPY of the real tree, not the tree itself.

    The first version chmodded the real `12-provisioning.sh` and restored it in a
    `finally`. A SIGKILL or a cancelled CI job between the two would leave that
    file at 0644 — reintroducing precisely the defect this rule exists to catch,
    via the test that guards it.
    """
    repo, img = _real("base-image")
    work = tmp_path / "repo"
    shutil.copytree(repo / "ROOT", work / "ROOT")
    (work / "Dockerfile").write_text((repo / "Dockerfile").read_text())
    mut = replace(img, dir=work, root=work / "ROOT")
    (work / "ROOT/opt/instance-tools/tests/base/12-provisioning.sh").chmod(0o644)
    assert "L065" in errs(mut, work)


def test_L065_runner_sh_is_not_exempt(tmp_path):
    """The file whose losing +x disables the entire suite must be gated. The first
    version of the rule exempted the whole tests root and left it out."""
    img = make(tmp_path, cls="base")
    p = tmp_path / "ROOT/opt/instance-tools/tests/runner.sh"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/bash\necho runner\n")
    p.chmod(0o644)
    assert has(img, tmp_path, "L065", "never run")


# ---- L062: a deferred failure must actually be reported -------------------
#
# `fail_later` only RECORDS a failure; `report_failures` is what turns the
# record into a failing test. Without it a test prints `FAIL: ...` and then
# exits 0 via test_pass — a visible failure in the log and a green suite.
#
# Found the honest way: adding the CUDA-libpath check to base/60-gpu-cuda with
# fail_later produced exactly that. Reading the change did not catch it; running
# it did.

def _write_base_test(repo, name, body):
    p = repo / "ROOT/opt/instance-tools/tests" / f"{name}.sh"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def test_L062_fail_later_without_report_failures_fires(tmp_path):
    """THE mutation, and a reproduction of the real defect."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nif ! thing; then\n    fail_later "x" "broke"\nfi\ntest_pass "ok"\n')
    # The ORDERING branch now reports it, with a more precise message: the
    # fixture's test_pass is reached while a failure is pending.
    assert has(img, tmp_path, "L062", "deferred failure is pending")


def test_L062_with_report_failures_is_clean(tmp_path):
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nif ! thing; then\n    fail_later "x" "broke"\nfi\n'
                     'report_failures\ntest_pass "ok"\n')
    assert "L062" not in errs(img, tmp_path)


def test_L062_a_test_using_only_test_fail_is_clean(tmp_path):
    """test_fail exits immediately, so it needs no report step. The rule must
    not demand report_failures from tests that never defer."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nif ! thing; then\n    test_fail "broke"\nfi\ntest_pass "ok"\n')
    assert "L062" not in errs(img, tmp_path)


def test_L062_a_mention_in_a_comment_does_not_satisfy_the_rule(tmp_path):
    """Same trap as L056/L059: the call must be real, not described."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nfail_later "x" "broke"\n'
                     '# report_failures would go here one day\ntest_pass "ok"\n')
    assert has(img, tmp_path, "L062", "deferred failure is pending")


def test_L062_fail_later_with_no_exit_at_all_still_fires(tmp_path):
    """The presence-only branch, which the ordering walk does not reach: a file
    that defers a failure and simply ends, never calling report_failures."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nfail_later "x" "broke"\necho done\n')
    assert has(img, tmp_path, "L062", "never report_failures")


def test_L062_http_check_counts_as_deferring(tmp_path):
    """http_check calls fail_later internally, so a test using only http_check
    has the identical bug. Gating on the literal name let this through."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nhttp_check "http://x" "y"\ntest_pass "ok"\n')
    assert has(img, tmp_path, "L062", "deferred failure is pending")


def test_L062_report_before_every_exit_is_clean(tmp_path):
    """Two exit paths, each preceded by a report — the shape 60-gpu-cuda now has."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nfail_later "x" "broke"\n'
                     'if thing; then\n    report_failures\n    test_pass "early"\nfi\n'
                     'report_failures\ntest_pass "late"\n')
    assert "L062" not in errs(img, tmp_path)


# The walk is a control-flow approximation. These pin down both directions of
# that approximation, because the linear version got BOTH wrong: it let a
# conditional report clear a pending failure (certifying the rule's own bug) and
# it flagged an exit in the arm that never deferred (a defect report on correct
# code). Every fixture below was checked against real bash before being asserted.

def test_L062_report_inside_an_untaken_branch_does_not_clear(tmp_path):
    """`fail_later` … `if x; then report_failures; fi` … `test_pass` exits 0 with
    a printed FAIL whenever the condition is false. A linear walk called it clean."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nfail_later "x" "broke"\n'
                     'if [[ "$MODE" == strict ]]; then\n    report_failures\nfi\n'
                     'test_pass "ok"\n')
    assert has(img, tmp_path, "L062", "deferred failure is pending")


def test_L062_guarded_report_does_not_clear(tmp_path):
    """Same hole, one-line spelling: `[[ -n "$Q" ]] && report_failures`."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nfail_later "x" "broke"\n'
                     '[[ -n "$Q" ]] && report_failures\ntest_pass "ok"\n')
    assert has(img, tmp_path, "L062", "deferred failure is pending")


def test_L062_exit_in_the_arm_that_never_deferred_is_clean(tmp_path):
    """The false positive: the `else` arm cannot have a failure pending, because
    the `then` arm — the only thing that defers — did not run."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nif [[ -n "$PORT" ]]; then\n'
                     '    http_check "http://x:$PORT" 200 "svc"\n    report_failures\n'
                     'else\n    test_pass "no service configured"\nfi\n'
                     'report_failures\ntest_pass "ok"\n')
    assert "L062" not in errs(img, tmp_path)


def test_L062_a_helper_function_that_reports_then_exits_is_clean(tmp_path):
    """`finish() { report_failures; test_pass "ok"; }` is correct. Not treating
    `{` as a command position reported it as never calling report_failures."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nfinish() { report_failures; test_pass "ok"; }\n'
                     'fail_later "x" "broke"\nfinish\n')
    assert "L062" not in errs(img, tmp_path)


def test_L062_a_helper_function_that_exits_without_reporting_fires(tmp_path):
    """The same machinery in the other direction: the discard is one call away."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nbail() { test_pass "early"; }\n'
                     'fail_later "x" "broke"\nbail\n')
    assert has(img, tmp_path, "L062", "deferred failure is pending")


def test_L062_test_skip_discards_a_pending_failure_too(tmp_path):
    """test_skip exits 77 and drops FAILURES exactly as test_pass drops it, so a
    rule that only knows test_pass leaves the same hole one keyword over."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nfail_later "x" "broke"\ntest_skip "nothing to do"\n')
    assert has(img, tmp_path, "L062", "deferred failure is pending")


def test_L062_a_hash_inside_an_expansion_is_not_a_comment(tmp_path):
    """`${v#"${p}"}` truncated by a naive comment strip unbalanced the brace
    count, swallowed the rest of 26-caddy-auth.sh, and hid its report_failures —
    a false positive on a real, correct, shipped test."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/26-caddy-auth",
                     'source lib.sh\ncheck() {\n    local v="${varname#"${prefix}"}"\n'
                     '    fail_later "x" "broke"\n}\ncheck\nreport_failures\ntest_pass "ok"\n')
    assert "L062" not in errs(img, tmp_path)


def test_L062_a_case_arm_defers_and_is_seen(tmp_path):
    """`)` is a command position. Missing it did worse than miss the call: with
    no deferring call found anywhere, the file was treated as non-deferring and
    skipped entirely — the never-reports check went with it."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\ncase "$MODE" in\n    a) fail_later "x" "broke" ;;\nesac\n'
                     'test_pass "ok"\n')
    assert has(img, tmp_path, "L062", "deferred failure is pending")


def test_L062_an_elif_chain_with_no_else_is_not_exhaustive(tmp_path):
    """Neither arm need run, so the failure recorded before the chain survives it.
    Treating `elif` as an `else` suppressed the fall-through arm at close."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nfail_later "x" "broke"\n'
                     'if [[ "$A" ]]; then\n    report_failures\n'
                     'elif [[ "$B" ]]; then\n    report_failures\nfi\n'
                     'test_pass "ok"\n')
    assert has(img, tmp_path, "L062", "deferred failure is pending")


def test_L062_a_guarded_helper_call_does_not_clear(tmp_path):
    """One-line `if Z; then fin; fi`. Two bugs met here: the helper's clearing
    effect was applied regardless of the guard, and a block that opened and
    closed on one line pushed a frame nothing ever popped."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nfin() { report_failures; }\nfail_later "x" "broke"\n'
                     'if [[ "$Z" ]]; then fin; fi\ntest_pass "ok"\n')
    assert has(img, tmp_path, "L062", "deferred failure is pending")


def test_L062_a_helper_that_reports_after_exiting_is_not_a_report(tmp_path):
    """Order inside the body matters: the report is unreachable past test_pass."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nbad() {\n    fail_later "x" "broke"\n'
                     '    test_pass "ok"\n    report_failures\n}\nbad\n')
    assert has(img, tmp_path, "L062", "deferred failure is pending")


def test_L062_a_shell_keyword_inside_a_string_is_not_control_flow(tmp_path):
    """`grep -qE "a|done"` contains `|done`. Reading that as a block close
    suppressed the frame push, so every conditional in the block read as
    unconditional — the fix for one false negative introducing another."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nfail_later "x" "broke"\n'
                     'if grep -qE "a|done" /etc/hosts; then\n    report_failures\nfi\n'
                     'test_pass "ok"\n')
    assert has(img, tmp_path, "L062", "deferred failure is pending")


def test_L062_a_guarded_report_inside_a_helper_body_does_not_clear(tmp_path):
    """The call-site guard was closed and the identical guard one level down —
    inside the body — was left open. Both go through the same walk now."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nfin() {\n    if [[ -n "$Q" ]]; then\n'
                     '        report_failures\n    fi\n}\n'
                     'fail_later "x" "broke"\nfin\ntest_pass "ok"\n')
    assert has(img, tmp_path, "L062", "deferred failure is pending")


def test_L062_an_unguarded_report_inside_a_helper_body_still_clears(tmp_path):
    """The other direction of the same change: a helper that always reports is
    still a report, and must not be flagged."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\nfin() {\n    report_failures\n}\n'
                     'fail_later "x" "broke"\nfin\ntest_pass "ok"\n')
    assert "L062" not in errs(img, tmp_path)


def test_L062_reports_the_real_file_line(tmp_path):
    """Function bodies are skipped during the walk, so the offending line has to
    be mapped back — a finding pointing at the wrong line is unactionable."""
    img = make(tmp_path, cls="base")
    _write_base_test(tmp_path, "base/60-gpu-cuda",
                     'source lib.sh\n'          # 1
                     'noop() {\n'               # 2
                     '    echo hi\n'            # 3
                     '    echo there\n'         # 4
                     '}\n'                      # 5
                     'fail_later "x" "broke"\n' # 6
                     'test_pass "ok"\n')        # 7
    found = [f for f in lint_image(img, tmp_path) if f.code == "L062"]
    assert found, "expected L062"
    assert found[0].path.endswith(":7"), found[0].path


# ---- L064: one native-libcuda resolver, not one per caller ----------------
#
# `LD_LIBRARY_PATH=<dir> cuda-driver-version` is a search HINT: name a directory
# with no loadable libcuda.so.1 and the loader continues to the ld.so cache — on
# a second boot, a previous boot's forward-compat library. It fails OPEN, to
# exactly the wrong answer. Worse, the same six lines lived in the boot script
# AND in the test meant to check it, so the test agreed rather than verified.

def test_L064_ld_library_path_wrapper_fires(tmp_path):
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "etc/vast_boot.d/05-configure-cuda.sh",
                       'MAX=$(LD_LIBRARY_PATH="$d" /opt/instance-tools/bin/cuda-driver-version)\n')
    assert has(img, tmp_path, "L064", "search hint, not a pin")


def test_L064_open_coded_libcuda_search_fires(tmp_path):
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "opt/instance-tools/tests/base/60-gpu-cuda.sh",
                       "p=$(find /usr/lib -name 'libcuda.so.1' | head -1)\n")
    assert has(img, tmp_path, "L064", "cuda-driver-version --native")


def test_L064_the_native_mode_is_clean(tmp_path):
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "etc/vast_boot.d/05-configure-cuda.sh",
                       'MAX=$(/opt/instance-tools/bin/cuda-driver-version --native || true)\n')
    assert "L064" not in errs(img, tmp_path)


def test_L064_probing_for_compat_libs_is_a_different_question(tmp_path):
    """try_forward_compat asks "does this toolkit ship compat libs" with
    `compgen -G .../libcuda.so.*`. Legitimate, and not a native-driver probe —
    the rule keyed on the bare name fired on both shipped callers."""
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "etc/vast_boot.d/05-configure-cuda.sh",
                       'compgen -G "$COMPAT_DIR/libcuda.so.*" > /dev/null || return 1\n')
    assert "L064" not in errs(img, tmp_path)


def test_L064_a_path_component_ending_in_ls_is_not_a_search(tmp_path):
    """The alternation had a trailing \\b but no leading one, so `tools` supplied
    the `ls` and a plain CDLL of an absolute path fired an ERROR-level rule."""
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "opt/instance-tools/bin/some-tool",
                       "#!/usr/bin/env python3\n"
                       "import ctypes; ctypes.CDLL('/opt/tools/libcuda.so.1')\n")
    assert "L064" not in errs(img, tmp_path)


def test_L064_a_lookalike_filename_is_not_exempt(tmp_path):
    """The exemption was a substring test, so cuda-driver-version.bak — or a
    wrapper — inherited the one sanctioned implementation's licence to scrape."""
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "opt/instance-tools/bin/cuda-driver-version-wrapper",
                       '#!/bin/bash\nMAX=$(LD_LIBRARY_PATH="$d" '
                       '/opt/instance-tools/bin/cuda-driver-version)\n')
    assert "L064" in errs(img, tmp_path)


def test_L064_extensionless_shipped_tools_are_scanned(tmp_path):
    """10 of the 12 tools in ROOT/opt/instance-tools/bin have no extension, so an
    *.sh/*.py glob exempts the directory most likely to re-introduce this."""
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "opt/instance-tools/bin/vast-capabilities",
                       '#!/bin/bash\nMAX=$(LD_LIBRARY_PATH="$d" '
                       '/opt/instance-tools/bin/cuda-driver-version)\n')
    assert "L064" in errs(img, tmp_path)


def test_mut_L062_dropping_the_real_final_report_fires(tmp_path):
    """Mutation against the REAL file: base/60-gpu-cuda defers three failures and
    reports before each of its two exits. Remove either report and the exit below
    it discards whatever was recorded."""
    repo, img = _real("base-image")
    src = repo / "ROOT/opt/instance-tools/tests/base/60-gpu-cuda.sh"
    original = src.read_text()
    assert original.count("\nreport_failures\n") >= 1
    for target in ("\nreport_failures\n", "\n    report_failures\n"):
        assert target in original, target
        try:
            src.write_text(original.replace(target, "\n", 1))
            assert "L062" in errs(img, repo), f"removing {target!r} did not fire L062"
        finally:
            src.write_text(original)


def test_mut_L064_the_real_boot_script_reverted_fires(tmp_path):
    """Mutation against the REAL file: restore the bash probe this change removed
    and the rule must fire. Without this the rule could be a no-op."""
    repo, img = _real("base-image")
    src = repo / "ROOT/etc/vast_boot.d/05-configure-cuda.sh"
    original = src.read_text()
    assert "cuda-driver-version --native" in original
    mutated = original.replace(
        "MAX_CUDA=$(/opt/instance-tools/bin/cuda-driver-version --native 2>/dev/null || true)",
        "_p=$(find /usr/lib -name 'libcuda.so.1' | head -1)\n"
        '    MAX_CUDA=$(LD_LIBRARY_PATH="$(dirname "$_p")" '
        "/opt/instance-tools/bin/cuda-driver-version 2>/dev/null || true)",
    )
    assert mutated != original
    try:
        src.write_text(mutated)
        codes = errs(img, repo)
        assert "L064" in codes
    finally:
        src.write_text(original)


def test_mut_L064_the_real_gpu_test_reverted_fires(tmp_path):
    """The second copy — the one a human review had to catch by hand."""
    repo, img = _real("base-image")
    src = repo / "ROOT/opt/instance-tools/tests/base/60-gpu-cuda.sh"
    original = src.read_text()
    assert "cuda-driver-version --native" in original
    mutated = original.replace(
        "native_driver_cuda=$(/opt/instance-tools/bin/cuda-driver-version --native "
        "2>/dev/null || true)",
        "_p=$(find /usr/lib -name 'libcuda.so.1' | head -1)\n"
        'native_driver_cuda=$(LD_LIBRARY_PATH="$(dirname "$_p")" '
        "/opt/instance-tools/bin/cuda-driver-version 2>/dev/null || true)",
    )
    assert mutated != original
    try:
        src.write_text(mutated)
        codes = errs(img, repo)
        assert "L064" in codes
    finally:
        src.write_text(original)


# ---- L063: never scrape nvidia-smi's table for the CUDA version -----------
#
# Driver 610 renamed the field ("CUDA Version" -> "CUDA UMD Version"), so every
# scrape returned empty on every 610 host at once. In 05-configure-cuda.sh the
# empty value aborted AFTER the CUDA ld.so.conf entries had been deleted.

def _write_root_script(repo, rel, body):
    p = repo / "ROOT" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def test_L063_scraping_the_smi_table_fires(tmp_path):
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "etc/vast_boot.d/05-configure-cuda.sh",
                       'MAX=$(nvidia-smi | grep -oP "CUDA Version: \\K[0-9]+\\.[0-9]+")\n')
    assert has(img, tmp_path, "L063", "cuda-driver-version")


def test_L063_catches_the_renamed_field_too(tmp_path):
    """Chasing the new spelling is the tempting wrong fix — it breaks again at
    the next rename. The rule rejects both."""
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "etc/vast_boot.d/05-configure-cuda.sh",
                       'MAX=$(nvidia-smi | grep -oP "CUDA UMD Version: \\K[0-9]+\\.[0-9]+")\n')
    assert "L063" in errs(img, tmp_path)


def test_L063_using_the_helper_is_clean(tmp_path):
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "etc/vast_boot.d/05-configure-cuda.sh",
                       'MAX=$(/opt/instance-tools/bin/cuda-driver-version || true)\n')
    assert "L063" not in errs(img, tmp_path)


def test_L063_ignores_a_comment_explaining_the_history(tmp_path):
    """The fix's own comments name the old field; the rule must not fire on
    prose or it becomes impossible to document."""
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "etc/vast_boot.d/05-configure-cuda.sh",
                       '# driver 610 renamed "CUDA Version:" to "CUDA UMD Version:"\n'
                       'MAX=$(/opt/instance-tools/bin/cuda-driver-version || true)\n')
    assert "L063" not in errs(img, tmp_path)


# ---- L066: one cert-usability predicate -----------------------------------
#
# Three sites asked "is this cert usable" and gave three different wrong answers.
# Two rejected a valid EC keypair (`openssl rsa` cannot load one at all) and
# neither compared the cert to the key; the third hashed both public keys before
# comparing them, and sha256sum of EMPTY input is the same fixed string on both
# sides — so two failed extractions certified each other.

def _write_portal_file(repo, rel, body):
    p = repo / "portal-aio" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def test_L066_openssl_rsa_check_fires(tmp_path):
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "opt/instance-tools/tests/base/27-caddy-tls.sh",
                       'openssl rsa -in "$KEY_PATH" -check -noout || test_fail "bad key"\n')
    assert has(img, tmp_path, "L066", "RSA-only openssl")


def test_L066_openssl_modulus_matching_fires(tmp_path):
    """The other RSA-only shape: comparing moduli. On an EC pair the cert side
    yields "Modulus=No modulus for this public key type" and the key side yields
    nothing, so a correct pair reads as a mismatch."""
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "etc/vast_boot.d/55-tls-cert-gen.sh",
                       'c=$(openssl x509 -in "$CRT" -noout -modulus)\n')
    assert has(img, tmp_path, "L066", "RSA-only openssl")


def test_L066_hashing_the_public_key_fires(tmp_path):
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "etc/vast_boot.d/55-tls-cert-gen.sh",
                       'k=$(openssl pkey -in "$KEY" -pubout -outform DER 2>/dev/null | sha256sum)\n')
    assert has(img, tmp_path, "L066", "digest of empty input")


def test_L066_the_argv_list_form_fires(tmp_path):
    """The portal calls openssl as a Python argv list, not a shell line. A rule
    written only for shell syntax would have exempted the one site that actually
    gates Caddy's TLS listener."""
    img = make(tmp_path, cls="base")
    _write_portal_file(tmp_path, "caddy_manager/caddy_config_manager.py",
                       'subprocess.run(["openssl", "rsa", "-in", KEY_PATH, '
                       '"-check", "-noout"], check=True)\n')
    assert has(img, tmp_path, "L066", "RSA-only openssl")


def test_L066_calling_the_helper_is_clean(tmp_path):
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "opt/instance-tools/tests/base/27-caddy-tls.sh",
                       '/opt/instance-tools/bin/cert-usable "$CERT_PATH" "$KEY_PATH" '
                       '|| test_fail "unusable"\n')
    assert "L066" not in errs(img, tmp_path)


def test_L066_the_helper_itself_is_exempt(tmp_path):
    """The sanctioned implementation necessarily contains openssl key handling."""
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "opt/instance-tools/bin/cert-usable",
                       '#!/bin/bash\n_k=$(openssl pkey -in "$KEY" -pubout)\n')
    assert "L066" not in errs(img, tmp_path)


def test_L066_generic_openssl_use_is_not_matched(tmp_path):
    """The rule must not fire on the many legitimate openssl calls the images
    make — random tokens, s_client probes, plain parseability, expiry."""
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "opt/instance-tools/tests/base/27-caddy-tls.sh",
                       'tok=$(openssl rand -hex 8)\n'
                       'openssl x509 -in "$CRT" -noout -checkend 0\n'
                       'issuer=$(echo "$info" | openssl x509 -noout -issuer)\n'
                       'echo | openssl s_client -connect 127.0.0.1:1 2>/dev/null\n'
                       'fp=$(openssl x509 -in "$CRT" -noout -fingerprint)\n')
    assert "L066" not in errs(img, tmp_path)


def test_mut_L066_the_real_boot_script_reverted_fires(tmp_path):
    """Mutation against the REAL file: restore the sha256sum comparison and the
    rule must fire. That form certified an unreadable cert against an unreadable
    key, because sha256sum of empty input is e3b0c442... on both sides."""
    repo, img = _real("base-image")
    src = repo / "ROOT/etc/vast_boot.d/55-tls-cert-gen.sh"
    original = src.read_text()
    assert "bin/cert-usable" in original
    mutated = original.replace(
        '_cert_usable() { "$_CERT_USABLE" "${1:-/etc/instance.crt}" '
        '"${2:-/etc/instance.key}"; }',
        "_cert_usable() {\n"
        "    local c k\n"
        "    c=$(openssl x509 -in /etc/instance.crt -noout -pubkey 2>/dev/null "
        "| openssl pkey -pubin -outform DER 2>/dev/null | sha256sum)\n"
        "    k=$(openssl pkey -in /etc/instance.key -pubout -outform DER "
        "2>/dev/null | sha256sum)\n"
        '    [[ -n "$c" && "$c" == "$k" ]]\n'
        "}",
    )
    assert mutated != original
    try:
        src.write_text(mutated)
        assert "L066" in errs(img, repo)
    finally:
        src.write_text(original)


def test_mut_L066_the_real_caddy_test_reverted_fires(tmp_path):
    repo, img = _real("base-image")
    src = repo / "ROOT/opt/instance-tools/tests/base/27-caddy-tls.sh"
    original = src.read_text()
    assert "bin/cert-usable" in original
    mutated = original.replace(
        'cert_reason=$(/opt/instance-tools/bin/cert-usable "$CERT_PATH" "$KEY_PATH" 2>&1)',
        'openssl rsa -in "$KEY_PATH" -check -noout 2>/dev/null',
    )
    assert mutated != original
    try:
        src.write_text(mutated)
        assert "L066" in errs(img, repo)
    finally:
        src.write_text(original)


def test_mut_L066_the_real_portal_validator_reverted_fires(tmp_path):
    """The site that is not a test: this is what gates Caddy's TLS listener."""
    repo, img = _real("base-image")
    src = repo / "portal-aio/caddy_manager/caddy_config_manager.py"
    original = src.read_text()
    assert "CERT_USABLE" in original
    mutated = original.replace(
        "            [CERT_USABLE, CERT_PATH, KEY_PATH],",
        '            ["openssl", "rsa", "-in", KEY_PATH, "-check", "-noout"],',
    )
    assert mutated != original
    try:
        src.write_text(mutated)
        assert "L066" in errs(img, repo)
    finally:
        src.write_text(original)


# ---- The rule CATALOGUE must not drift from the tree -----------------------

# `/opt/...` paths that are deliberately illustrative rather than real. Adding to
# this list is the explicit way to say "placeholder"; everything else must exist.
_RULES_PATH_PLACEHOLDERS = {
    "/opt/supervisor-scripts/NAME.sh",     # L010: NAME stands for the app name
}


def test_rules_text_cites_paths_that_exist():
    """Every /opt path a RULES entry names must be real.

    `imagegen rules` generates docs/lint-rules.md, which CLAUDE.md treats as
    ground truth and which is what a developer reads when a rule fires and they
    ask "what should I do instead". L066 shipped telling them to source
    `/opt/instance-tools/lib/tls-cert.sh` — a file that does not exist, and the
    design ADR 0026 explicitly REJECTED. Every L066 test passed, and the
    doc-currency test kept the published catalogue in perfect sync with the
    wrong text, because nothing compared the prose to the tree. The likely
    reader response would have been to CREATE the named file: a second
    implementation, which is the precise drift the rule exists to prevent.
    """
    repo = find_repo_root(Path(__file__).resolve().parent)
    root = repo / "ROOT"
    pat = re.compile(r"/opt/[A-Za-z0-9_./-]*[A-Za-z0-9_-]")
    missing = []
    for code, _sev, text in RULES:
        for p in sorted(set(pat.findall(text))):
            if p in _RULES_PATH_PLACEHOLDERS:
                continue
            if not (root / p.lstrip("/")).exists():
                missing.append(f"{code} cites {p}, which is not in ROOT/")
    assert not missing, "\n".join(missing)


def test_L066_a_wrapped_argv_does_not_escape_the_rule(tmp_path):
    """ruff and black both wrap a 6-element list. The line-scoped regex exempted
    the portal — the one caller that is NOT a test, and the one that gates
    Caddy's TLS listener — while the mutation test kept passing because it only
    ever mutated to the single-line form."""
    img = make(tmp_path, cls="base")
    _write_portal_file(tmp_path, "caddy_manager/caddy_config_manager.py",
                       "subprocess.run(\n"
                       '    [\n'
                       '        "openssl", "rsa",\n'
                       '        "-in", KEY_PATH,\n'
                       '        "-check", "-noout",\n'
                       '    ],\n'
                       ")\n")
    assert has(img, tmp_path, "L066", "RSA-only openssl")


def test_L066_rsa_noout_without_output_fires(tmp_path):
    """`openssl rsa -in K -noout` is the same RSA-only load with no flag at all;
    it rejects a valid EC key exactly as `-check` does."""
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "opt/instance-tools/tests/base/27-caddy-tls.sh",
                       'openssl rsa -in "$KEY_PATH" -noout || test_fail "bad key"\n')
    assert has(img, tmp_path, "L066", "RSA-only openssl")


def test_L066_rsa_conversion_is_not_a_check(tmp_path):
    """Producing output is a conversion, not a validity test — legitimate."""
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "opt/instance-tools/bin/some-tool",
                       'openssl rsa -in "$KEY" -pubout -out "$PUB" -noout\n')
    assert "L066" not in errs(img, tmp_path)


def test_L066_python_docstrings_are_prose_not_code(tmp_path):
    """The window join made explanatory prose dangerous: a docstring describing
    the old code fired the rule on the file that had just been fixed. Blanking
    ALL string literals would have been the obvious fix and is wrong — the
    offending call IS a list of strings — so only docstrings are dropped."""
    img = make(tmp_path, cls="base")
    _write_portal_file(tmp_path, "caddy_manager/caddy_config_manager.py",
                       "def validate():\n"
                       '    """The previous version used the RSA-ONLY `openssl rsa`\n'
                       '    entry point and its `-check` flag, which cannot load EC."""\n'
                       "    return run([CERT_USABLE, CERT_PATH, KEY_PATH])\n")
    assert "L066" not in errs(img, tmp_path)


def test_L066_a_string_literal_argv_is_still_code(tmp_path):
    """The other side of the same coin — dropping prose must not drop data."""
    img = make(tmp_path, cls="base")
    _write_portal_file(tmp_path, "caddy_manager/caddy_config_manager.py",
                       "def validate():\n"
                       '    """Checks the key."""\n'
                       '    return run(["openssl", "rsa", "-in", KEY, "-check"])\n')
    assert has(img, tmp_path, "L066", "RSA-only openssl")
