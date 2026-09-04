"""Linter tests: a regression net over the real repo + one mutant per invariant.

Run: cd tools/imagegen && PYTHONPATH=. python -m pytest -q
"""
import re
import shutil

import pytest
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


def _wire_gate(repo, template_dir):
    """Hand a template dir to qa-gate.yml, the way a build workflow does.

    L057/L072 scope on this wiring rather than on the template's NAME, so the tests
    have to create it: a template no workflow boots is deliberately exempt.
    """
    wf = repo / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "build-img.yml").write_text(
        "jobs:\n  qa:\n    uses: ./.github/workflows/qa-gate.yml\n"
        "    with:\n      template_dir: " + template_dir + "\n")


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


def test_L041_no_image_is_grandfathered_any_more(tmp_path, monkeypatch):
    """The exemption list is EMPTY, and aio-studio — its only ever entry — is the case
    that proves it. It was exempted because its Dockerfile pinned a base in the staging
    namespace; that pin was removed 2026-09-02, so the exemption was retired by being
    FIXED rather than renewed. A name reappearing here should fail this test and force
    the question of why, which is the whole point of an exemption that expires."""
    assert L._L041_GRANDFATHERED == frozenset(), (
        f"L041 exemptions are back: {sorted(L._L041_GRANDFATHERED)} — an exemption is a "
        f"deferred fix, not a permanent state")
    monkeypatch.setenv("DOCKERHUB_NAMESPACE_STAGING", "acmestaging")
    df = VALID_DF.replace("uv pip install foo", "uv pip install foo  # acmestaging/x")
    img = replace(make(tmp_path, df=df), name="aio-studio")
    assert "L041" in errs(img, tmp_path), "aio-studio must now be gated like every other image"


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
    # base-qa is gated by promote-base-image.yml; L057 keys on that wiring, not on
    # the image class, so the fixture has to supply it.
    _wire_gate(img.dir.parent, "img/templates/qa")


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


def test_L057_applies_to_a_non_base_gating_template(tmp_path):
    """The rule was base-only from ADR 0019 until ADR 0031 re-validated the other
    two gates. Scope is now the gate WIRING, not the image class: a pytorch-nested
    image whose template is handed to qa-gate.yml owes the same declaration."""
    img = make(tmp_path, cls="pytorch-nested")
    _write_template(img, f"name: QA\nimage: vastai/x\n{_FLOORS}")
    _wire_gate(tmp_path, "img/templates/qa")
    assert has(img, tmp_path, "L057", "no env.INSTANCE_TEST_REQUIRE_PASS")


def test_L057_ignores_a_template_no_gate_boots(tmp_path):
    """The asymmetry that keeps this rule honest: a template nothing gates on
    certifies nothing, so it owes nothing. Only the wiring creates the obligation
    — which is why the rule reads the workflows instead of matching `*-qa`."""
    img = make(tmp_path, cls="pytorch-nested")
    _write_template(img, f"name: QA\nimage: vastai/x\n{_FLOORS}")
    _wire_gate(tmp_path, "img/templates/some-other-dir")
    assert "L057" not in errs(img, tmp_path)


def test_L057_ignores_an_unresolvable_expression(tmp_path):
    """A `template_dir` built from a ${{ }} expression cannot be resolved at lint
    time. Skipping it loses coverage loudly rather than guessing a path and
    reporting a finding against a template that may not be the gated one."""
    img = make(tmp_path, cls="pytorch-nested")
    _write_template(img, f"name: QA\nimage: vastai/x\n{_FLOORS}")
    _wire_gate(tmp_path, "${{ inputs.template_dir }}")
    assert "L057" not in errs(img, tmp_path)


# ---- L072: a gating template must require the image's OWN suite (ADR 0031) ----


def _own_suite(img, dirname="app.d", tests=("10-serving",)):
    """Give the image a suite of its own, the way a derivative ships one."""
    d = img.dir / "ROOT/opt/instance-tools/tests" / dirname
    d.mkdir(parents=True, exist_ok=True)
    for t in tests:
        f = d / f"{t}.sh"
        f.write_text('#!/bin/bash\ntest_fail "nope"\n')
        f.chmod(0o755)      # L065 — a 0644 test is silently not discovered


def _gated(img, required):
    _write_template(img, f"name: QA\nimage: vastai/x\n"
                         f"env:\n  INSTANCE_TEST_REQUIRE_PASS: {required}\n{_FLOORS}")
    _wire_gate(img.dir.parent, "img/templates/qa")


def test_L072_trio_only_fires_on_an_image_with_its_own_suite(tmp_path):
    """THE mutation, and the shape ADR 0031 found live: the template names only the
    GPU trio, which is inherited from base and identical on every image. The gate
    then certifies that the rented BOX has a working GPU while the app suite could
    self-skip — `vllm.d/10-vllm-serving` skips on one missing env var — and promote
    green."""
    img = make(tmp_path)
    _own_suite(img)
    _gated(img, _TRIO)
    assert has(img, tmp_path, "L072", "names no test from this image's own suite")


def test_L072_naming_an_own_test_is_clean(tmp_path):
    img = make(tmp_path)
    _own_suite(img)
    _gated(img, f"{_TRIO} app.d/10-serving")
    assert "L072" not in errs(img, tmp_path)


def test_L072_does_not_fire_when_the_image_ships_no_suite(tmp_path):
    """An image whose only tests are base's inherits base's coverage and owes
    nothing extra — the rule must not invent an obligation it cannot be satisfied."""
    img = make(tmp_path)
    _gated(img, _TRIO)
    assert "L072" not in errs(img, tmp_path)


def test_L072_ignores_the_exposure_allowlist_dir(tmp_path):
    """`exposure-allowlist/` sits beside the suites and holds .conf data, not tests.
    Counting it as an own suite would demand a required test that cannot exist —
    which is why a suite dir is defined by holding an NN-*.sh, not by its name."""
    img = make(tmp_path)
    d = img.dir / "ROOT/opt/instance-tools/tests/exposure-allowlist"
    d.mkdir(parents=True, exist_ok=True)
    (d / "00-base.conf").write_text("# allowlist\n")
    _gated(img, _TRIO)
    assert "L072" not in errs(img, tmp_path)


def test_L072_stays_silent_when_L057_already_reports(tmp_path):
    """One finding per defect: a template with NO declaration is L057's report. Two
    errors for one missing line reads as two problems."""
    img = make(tmp_path)
    _own_suite(img)
    _write_template(img, f"name: QA\nimage: vastai/x\n{_FLOORS}")
    _wire_gate(tmp_path, "img/templates/qa")
    e = errs(img, tmp_path)
    assert "L057" in e and "L072" not in e


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

# Paths that are REAL on an instance but absent from ROOT/ because a build step
# creates them. Allowing these by name would be a free pass, so each maps to the
# file that creates it and the test asserts the path is still mentioned there — if
# the creator stops making it, this fails instead of silently excusing a dead path.
_RULES_BUILD_TIME_PATHS = {
    "/opt/sys-venv": "tools/convert-non-vast-image.sh",
    "/opt/sys-venv/shim": "tools/convert-non-vast-image.sh",
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
            creator = _RULES_BUILD_TIME_PATHS.get(p)
            if creator:
                # Not a free pass: the creator must still create it.
                text_of = (repo / creator).read_text(encoding="utf-8", errors="replace")
                if p not in text_of:
                    missing.append(f"{code} cites {p} as build-time, but {creator} "
                                   "no longer mentions it")
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


def test_L066_a_one_element_per_line_argv_does_not_escape(tmp_path):
    """The shape a magic trailing comma FORCES: ruff/black explode a 6-element
    list to one element per line, so `"openssl"` and `"-check"` land five lines
    apart. _WINDOW=4 missed exactly this (it caught only the two-per-line form
    the older test pinned)."""
    img = make(tmp_path, cls="base")
    _write_portal_file(tmp_path, "caddy_manager/caddy_config_manager.py",
                       "subprocess.run(\n"
                       "    [\n"
                       '        "openssl",\n'
                       '        "rsa",\n'
                       '        "-in",\n'
                       "        KEY_PATH,\n"
                       '        "-check",\n'
                       '        "-noout",\n'
                       "    ],\n"
                       ")\n")
    assert has(img, tmp_path, "L066", "RSA-only openssl")


def test_L066_one_statement_yields_one_finding(tmp_path):
    """Deduping on the START line reports the same wrapped statement once per
    overlapping window that reaches it — one of them pointing at a bare `[`.
    The whole matched statement must be marked seen."""
    img = make(tmp_path, cls="base")
    _write_portal_file(tmp_path, "caddy_manager/caddy_config_manager.py",
                       "subprocess.run(\n"
                       "    [\n"
                       '        "openssl", "rsa",\n'
                       '        "-in", KEY_PATH,\n'
                       '        "-check", "-noout",\n'
                       "    ],\n"
                       ")\n")
    l066 = [f for f in lint_image(img, tmp_path) if f.code == "L066"]
    assert len(l066) == 1, [f.path for f in l066]


def test_L066_rsa_keygen_idiom_is_not_flagged(tmp_path):
    """`openssl req -newkey rsa:2048 ... -noout` generates a key and suppresses
    the CSR — it is not the RSA-only `openssl rsa` subcommand. The `rsa:2048`
    token must not read as one, regardless of a nearby `-noout`."""
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "etc/vast_boot.d/55-tls-cert-gen.sh",
                       'openssl req -newkey rsa:2048 -subj "/CN=t" -nodes '
                       '-keyout k.pem -noout\n')
    assert "L066" not in errs(img, tmp_path)


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


def test_L066_prose_is_blanked_not_dropped(tmp_path):
    """DROPPING prose lines closes the gap, so a window stitches code from either
    side of a long docstring into a false positive. Here `openssl x509` and
    `-modulus` are nine real lines apart (beyond the window), separated only by a
    docstring — blanking keeps them apart, dropping would collapse them together.
    Neither line is a real RSA-only check, so this must stay clean."""
    img = make(tmp_path, cls="base")
    _write_portal_file(tmp_path, "caddy_manager/caddy_config_manager.py",
                       "def f(crt):\n"
                       '    subject = run(["openssl", "x509", "-in", crt, "-subject"])\n'
                       "    def helper():\n"
                       '        """A deliberately long docstring so the two code\n'
                       "        lines below and above are far enough apart that a\n"
                       "        window cannot join them unless the prose in between\n"
                       "        is removed rather than blanked. Line four. Line five.\n"
                       "        Line six. Line seven. Line eight of prose here.\"\"\"\n"
                       "        return 1\n"
                       '    other = run(["-modulus", crt])\n'
                       "    return subject, other\n")
    assert "L066" not in errs(img, tmp_path)


def test_L066_a_string_literal_argv_is_still_code(tmp_path):
    """The other side of the same coin — dropping prose must not drop data."""
    img = make(tmp_path, cls="base")
    _write_portal_file(tmp_path, "caddy_manager/caddy_config_manager.py",
                       "def validate():\n"
                       '    """Checks the key."""\n'
                       '    return run(["openssl", "rsa", "-in", KEY, "-check"])\n')
    assert has(img, tmp_path, "L066", "RSA-only openssl")



# ---- L069: presence is not readiness ------------------------------------
#
# 65-supervisor-launch.sh backgrounds supervisord; boot walks straight on to
# 70-instance-test.sh, which backgrounds the runner. The suite's first test
# therefore races supervisord's own startup, and `pgrep` — the gate it used — is
# satisfied at fork while the RPC socket it calls on the next line is not.
# Measured in vastai/base-image:cuda-13.2.0-auto, idle, 16 cores: presence at
# 1.7ms, socket usable at 383ms. On a contended QA host that window is seconds.

def _tests_repo(tmp: Path, name: str, body: str, *, confs: str = "") -> Image:
    """A repo-level tests tree. The rule reads programs from the repo overlay
    (repo/ROOT/etc/supervisor/conf.d), not from the image dir."""
    img = make(tmp, cls="base")
    t = tmp / "ROOT/opt/instance-tools/tests/base" / name
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text(body)
    t.chmod(0o755)
    if confs:
        c = tmp / "ROOT/etc/supervisor/conf.d/caddy.conf"
        c.parent.mkdir(parents=True, exist_ok=True)
        c.write_text(confs)
    return img


def test_L069_presence_as_the_readiness_gate_fires(tmp_path):
    """THE defect, in the shape base/10-supervisor.sh shipped it."""
    img = _tests_repo(tmp_path, "10-supervisor.sh",
                      'pgrep -f supervisord &>/dev/null || test_fail "supervisord not running"\n'
                      'supervisorctl status &>/dev/null; rc=$?\n')
    assert has(img, tmp_path, "L069", "presence is true at fork")


def test_L069_presence_used_for_IDENTITY_after_a_socket_call_is_clean(tmp_path):
    """The fix is not to delete `pidof`. Caddy's pid is genuinely needed to
    attribute its listening sockets and supervisord cannot supply it — the
    program is a wrapper script, so `supervisorctl pid caddy` returns the shell.
    Presence AFTER readiness is an identity lookup, and legal."""
    img = _tests_repo(tmp_path, "25-caddy-proxy.sh",
                      'assert_service_running caddy\n'
                      'caddy_pid=$(pidof caddy 2>/dev/null) || test_fail "caddy not running"\n',
                      confs="[program:caddy]\ncommand=/opt/supervisor-scripts/caddy.sh\n")
    assert "L069" not in errs(img, tmp_path)


def test_L069_a_socket_call_LATER_in_the_file_does_not_excuse_the_gate(tmp_path):
    """Order is the whole rule. A wait further down the file did not run yet."""
    img = _tests_repo(tmp_path, "25-caddy-proxy.sh",
                      'caddy_pid=$(pidof caddy 2>/dev/null) || test_fail "caddy not running"\n'
                      'assert_service_running caddy\n',
                      confs="[program:caddy]\ncommand=/opt/supervisor-scripts/caddy.sh\n")
    assert has(img, tmp_path, "L069", "presence is true at fork")


def test_L069_a_negated_ABSENCE_assertion_is_exempt(tmp_path):
    """`! pidof caddy || test_fail "still running"` asserts caddy is GONE, which
    is what serverless mode requires. There is nothing to wait for and presence
    is the correct instrument, so demanding a socket call first would be a false
    positive — and one that pushes an author toward a weaker check."""
    img = _tests_repo(tmp_path, "85-serverless-services.sh",
                      '! pidof caddy &>/dev/null || test_fail "caddy still running"\n',
                      confs="[program:caddy]\ncommand=/opt/supervisor-scripts/caddy.sh\n")
    assert "L069" not in errs(img, tmp_path)


def test_L069_an_if_predicate_is_not_an_assertion(tmp_path):
    """65-conditional-services and 67-service-functionality branch on `pgrep -f
    jupyter` to tell the .launch-managed case from the supervisor-managed one.
    Nothing is asserted, so nothing raced."""
    img = _tests_repo(tmp_path, "65-conditional-services.sh",
                      'if pgrep -f "jupyter" &>/dev/null; then\n  echo launch-managed\nfi\n',
                      confs="[program:jupyter]\ncommand=/opt/supervisor-scripts/jupyter.sh\n")
    assert "L069" not in errs(img, tmp_path)


def test_L069_a_process_supervisord_does_not_manage_is_out_of_scope(tmp_path):
    """`pidof sshd` in 40-users-permissions: sshd is not a supervisord program,
    so its presence is not a proxy for a socket that has to come up first."""
    img = _tests_repo(tmp_path, "40-users-permissions.sh",
                      'pidof sshd &>/dev/null || test_fail "sshd not running"\n')
    assert "L069" not in errs(img, tmp_path)


def test_L069_the_program_list_is_READ_from_the_overlay_not_restated(tmp_path):
    """A list of service names hardcoded in the linter would go stale the first
    time a program is added, and the rule would report clean on precisely the
    newest service."""
    img = _tests_repo(tmp_path, "99-new.sh",
                      'pidof brandnew &>/dev/null || test_fail "brandnew not running"\n')
    assert "L069" not in errs(img, tmp_path), "not a program yet"
    c = tmp_path / "ROOT/etc/supervisor/conf.d/brandnew.conf"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text("[program:brandnew]\ncommand=/opt/supervisor-scripts/brandnew.sh\n")
    assert has(img, tmp_path, "L069", "presence is true at fork"), "adding the conf must arm it"


def test_L069_a_line_continuation_does_not_hide_the_assertion(tmp_path):
    """Found by the mutation test, not by review: the first detector scanned raw
    lines, so splitting `pidof caddy ... || test_fail` across a backslash hid the
    `|| test_fail` from the probe and the rule went quiet on a file that had the
    defect. A rule a line break defeats is not a rule."""
    img = _tests_repo(tmp_path, "25-caddy-proxy.sh",
                      'caddy_pid=$(pidof caddy 2>/dev/null) \\\n'
                      '    || test_fail "caddy not running"\n',
                      confs="[program:caddy]\ncommand=/opt/supervisor-scripts/caddy.sh\n")
    assert has(img, tmp_path, "L069", "presence is true at fork")


def test_mut_L069_the_real_suite_with_the_readiness_gate_reverted_fires(tmp_path):
    """THE mutation: put base/10-supervisor.sh back the way it shipped.

    Against a COPY of the real tree, following L065's precedent — mutating the
    tree in place and restoring in a `finally` reintroduces the exact defect the
    rule exists to catch if the process dies between the two.
    """
    repo, img = _real("base-image")
    work = tmp_path / "repo"
    shutil.copytree(repo / "ROOT", work / "ROOT")
    (work / "Dockerfile").write_text((repo / "Dockerfile").read_text())
    mutant = replace(img, dir=work, root=work / "ROOT")
    assert "L069" not in errs(mutant, work), "the copy must start clean"

    t = work / "ROOT/opt/instance-tools/tests/base/10-supervisor.sh"
    t.write_text('source "$(dirname "$0")/../lib.sh"\n'
                 'pgrep -f supervisord &>/dev/null || test_fail "supervisord not running"\n'
                 'supervisorctl status &>/dev/null; rc=$?\n'
                 '[[ $rc -le 3 ]] || test_fail "cannot communicate (exit ${rc})"\n')
    assert has(mutant, work, "L069", "presence is true at fork")


def test_mut_L069_dropping_the_socket_gate_from_25_caddy_proxy_fires(tmp_path):
    """The second real site: caddy's `startsecs=5` means a single-shot pidof is
    racing a five-second window on every boot, not a sub-second one."""
    repo, img = _real("base-image")
    work = tmp_path / "repo"
    shutil.copytree(repo / "ROOT", work / "ROOT")
    (work / "Dockerfile").write_text((repo / "Dockerfile").read_text())
    mutant = replace(img, dir=work, root=work / "ROOT")
    t = work / "ROOT/opt/instance-tools/tests/base/25-caddy-proxy.sh"
    # Back to the single-shot gate it shipped with. Removing only the
    # assert_service_running line is NOT this defect any more: the post-fix file
    # wraps pidof in a bounded retry, which is itself a wait.
    t.write_text('source "$(dirname "$0")/../lib.sh"\n'
                 'caddy_pid=$(pidof caddy 2>/dev/null) || test_fail "caddy not running"\n')
    assert has(mutant, work, "L069", "presence is true at fork")


def test_L069_derivative_and_external_suites_are_covered(tmp_path):
    """The engine images ship their own .d/ suites against the same supervisord."""
    img = make(tmp_path, cls="base")
    t = tmp_path / "external/vllm/ROOT/opt/instance-tools/tests/vllm.d/10-serving.sh"
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text('pgrep -f supervisord &>/dev/null || test_fail "supervisord not running"\n')
    t.chmod(0o755)
    assert has(img, tmp_path, "L069", "presence is true at fork")


def test_L069_the_idiomatic_if_not_rewrite_is_caught(tmp_path):
    """The single most likely way a future author rewrites the banned line — and
    it reads MORE careful, not less. An earlier version of the rule required the
    `|| test_fail` spelling and let this through, which would have made the whole
    gate decorative the first time someone tidied the file."""
    img = _tests_repo(tmp_path, "10-supervisor.sh",
                      'if ! pgrep -f supervisord &>/dev/null; then\n'
                      '    test_fail "supervisord not running"\n'
                      'fi\n')
    assert has(img, tmp_path, "L069", "presence is true at fork")


def test_L069_an_if_predicate_asserting_ABSENCE_stays_exempt(tmp_path):
    """The symmetric case, and why the exemption cannot key on the `!` token:
    `if pgrep X; then test_fail` says X must be GONE, `if ! pgrep X; then
    test_fail` says X must be PRESENT. Same tokens, opposite meanings."""
    img = _tests_repo(tmp_path, "85-serverless-services.sh",
                      'if pidof caddy &>/dev/null; then\n'
                      '    test_fail "caddy still running in serverless mode"\n'
                      'fi\n',
                      confs="[program:caddy]\ncommand=/opt/supervisor-scripts/caddy.sh\n")
    assert "L069" not in errs(img, tmp_path)


def test_L069_brace_group_and_fail_later_are_assertions(tmp_path):
    """`|| { test_fail ...; }` and `|| fail_later ...` fail the test exactly as
    `|| test_fail` does. fail_later matters most: it is the house idiom in
    26-caddy-auth and 65-conditional-services, i.e. the rule was blindest in the
    two files most likely to grow one of these."""
    for i, body in enumerate((
            'pgrep -f supervisord &>/dev/null || { test_fail "x"; }\n',
            'pgrep -f supervisord &>/dev/null || fail_later "sup" "x"\nreport_failures\n',
            'pgrep -f supervisord &>/dev/null || test_fatal "x"\n')):
        d = tmp_path / f"case{i}"
        d.mkdir()
        img = _tests_repo(d, "10-supervisor.sh", body)
        assert has(img, d, "L069", "presence is true at fork"), body


def test_L069_a_helper_NAME_inside_a_string_does_not_disarm_the_rule(tmp_path):
    """These files are written with long narrative prose and explicit messages,
    so a helper name inside an `echo` or a failure message is a realistic
    accident — and it used to silence the rule for the whole rest of the file."""
    img = _tests_repo(tmp_path, "10-supervisor.sh",
                      'echo "next step: wait_for_supervisor"\n'
                      'pgrep -f supervisord &>/dev/null || test_fail "run assert_service_running"\n')
    assert has(img, tmp_path, "L069", "presence is true at fork")


def test_L069_a_bare_unwaited_supervisorctl_does_not_count_as_readiness(tmp_path):
    """The rule must not be satisfiable by code that is strictly worse. A
    single-shot `supervisorctl status` is what failed on 2026-08-18; hoisting one
    above the probe used to turn the finding off."""
    img = _tests_repo(tmp_path, "10-supervisor.sh",
                      'sup=$(supervisorctl status 2>/dev/null)\n'
                      'pgrep -f supervisord &>/dev/null || test_fail "not running"\n')
    assert has(img, tmp_path, "L069", "presence is true at fork")


def test_L069_assert_service_stopped_counts_as_reaching_the_socket(tmp_path):
    """It calls wait_for_supervisor and is exactly as socket-backed as its
    sibling; omitting it made the rule fire on a correctly-guarded file."""
    img = _tests_repo(tmp_path, "85-serverless-services.sh",
                      'assert_service_stopped caddy\n'
                      'pidof caddy &>/dev/null || test_fail "gone"\n',
                      confs="[program:caddy]\ncommand=/opt/supervisor-scripts/caddy.sh\n")
    assert "L069" not in errs(img, tmp_path)


def test_L069_reports_the_file_and_line_of_the_probe(tmp_path):
    """Every other L069 test keys on the message, so all of them would pass with
    the rule pointing at the wrong line of the wrong file."""
    img = _tests_repo(tmp_path, "10-supervisor.sh",
                      '# a comment\n'
                      'echo hello\n'
                      'pgrep -f supervisord &>/dev/null || test_fail "not running"\n')
    f = [f for f in lint_image(img, tmp_path) if f.code == "L069"]
    assert len(f) == 1
    assert f[0].path == "ROOT/opt/instance-tools/tests/base/10-supervisor.sh:3", f[0].path


@pytest.mark.parametrize("rel", [
    "derivatives/llama-cpp/ROOT/opt/instance-tools/tests/llama.d/10-serving.sh",
    "derivatives/pytorch/derivatives/comfyui/ROOT/opt/instance-tools/tests/comfyui.d/10-serving.sh",
    "external/vllm/ROOT/opt/instance-tools/tests/vllm.d/10-serving.sh",
])
def test_L069_every_scanned_root_is_locked_in(tmp_path, rel):
    """One assert per root. The single-root version passed with any of the other
    two roots deleted from the scan list — a silent coverage regression on the
    trees where the newest tests actually live."""
    img = make(tmp_path, cls="base")
    t = tmp_path / rel
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text('pgrep -f supervisord &>/dev/null || test_fail "supervisord not running"\n')
    t.chmod(0o755)
    assert has(img, tmp_path, "L069", "presence is true at fork")


# ---- L071: a restart is not a readiness event -----------------------------
#
# `supervisorctl restart` returns when the WRAPPER clears startsecs — measured
# at 5145ms with the port still answering 000. And `wait_for_caddy` bare waits
# on :2019, Caddy's default admin endpoint, which binds before the site
# listeners; six bare calls in 26-caddy-auth waited on a port the test never
# probes, and the checks that followed recorded `expected 401, got 000`.

def test_L071_a_bare_wait_for_caddy_fires(tmp_path):
    """THE defect. :2019 is the admin endpoint; the checks target :1111/:6006."""
    img = _tests_repo(tmp_path, "26-caddy-auth.sh",
                      'supervisorctl restart caddy &>/dev/null\n'
                      'wait_for_caddy || test_fail "x"\n')
    assert has(img, tmp_path, "L071", "no port")


def test_L071_a_restart_with_no_wait_before_the_next_probe_fires(tmp_path):
    img = _tests_repo(tmp_path, "26-caddy-auth.sh",
                      'supervisorctl restart caddy &>/dev/null\n'
                      'http_check "after restart" 401 http://127.0.0.1:1111/\n'
                      'report_failures\n')
    assert has(img, tmp_path, "L071", "without waiting for it to be")


def test_L071_a_restart_on_the_way_OUT_of_a_test_is_exempt(tmp_path):
    """No next probe to race, and the cross-FILE hazard is closed at the other
    end: 26-caddy-auth and 27-caddy-tls each wait for the ports before
    enumerating them, so every file guards its own entry rather than trusting
    its predecessor's exit. Requiring a wait there produced one that provably
    could not fail — the skip path is only reachable when zero ports are
    declared, so it iterates nothing — and a guard that cannot fail reads as
    protection while giving none."""
    img = _tests_repo(tmp_path, "26-caddy-auth.sh",
                      'supervisorctl restart caddy &>/dev/null\n'
                      'test_skip "no external caddy ports found"\n')
    assert "L071" not in errs(img, tmp_path)


def test_L071_a_long_comment_between_restart_and_wait_does_not_fire(tmp_path):
    """The window counted LINES, and _strip_comment blanks a comment into an
    empty line that still consumed a slot — so a five-line explanation pushed
    the wait out of view. Same class as the window that started at i+1: the rule
    reading layout instead of behaviour."""
    img = _tests_repo(tmp_path, "26-caddy-auth.sh",
                      'supervisorctl restart caddy &>/dev/null\n'
                      '# one\n# two\n# three\n# four\n# five\n# six\n'
                      'wait_for_caddy_ports || test_fail "x"\n')
    assert "L071" not in errs(img, tmp_path)


def test_L071_wait_for_caddy_ports_satisfies_it(tmp_path):
    img = _tests_repo(tmp_path, "26-caddy-auth.sh",
                      'supervisorctl restart caddy &>/dev/null\n'
                      'wait_for_caddy_ports || test_fail "x"\n')
    assert "L071" not in errs(img, tmp_path)


def test_L071_an_explicit_port_satisfies_it(tmp_path):
    """27-caddy-tls was always correct: it passes "$test_port"."""
    img = _tests_repo(tmp_path, "27-caddy-tls.sh",
                      'supervisorctl restart caddy &>/dev/null\n'
                      'wait_for_caddy "$test_port" "https" || test_fail "x"\n')
    assert "L071" not in errs(img, tmp_path)


def test_L071_a_comment_between_restart_and_wait_is_fine(tmp_path):
    """House style puts the reasoning between the two lines."""
    img = _tests_repo(tmp_path, "26-caddy-auth.sh",
                      'supervisorctl restart caddy &>/dev/null\n'
                      '# it needs a moment to rebind\n'
                      'wait_for_caddy_ports || test_fail "x"\n')
    assert "L071" not in errs(img, tmp_path)


def test_mut_L071_the_real_suite_with_the_bare_call_restored_fires(tmp_path):
    """Mutation against a copy of the real tree."""
    repo, img = _real("base-image")
    work = tmp_path / "repo"
    shutil.copytree(repo / "ROOT", work / "ROOT")
    (work / "Dockerfile").write_text((repo / "Dockerfile").read_text())
    mutant = replace(img, dir=work, root=work / "ROOT")
    assert "L071" not in errs(mutant, work), "the copy must start clean"
    t = work / "ROOT/opt/instance-tools/tests/base/26-caddy-auth.sh"
    t.write_text(t.read_text().replace(
        'wait_for_caddy_ports || test_fail "caddy did not rebind its ports after restart (see WARN above)"',
        'wait_for_caddy || test_fail "caddy did not come back after restart (see WARN above)"'))
    assert has(mutant, work, "L071", "no port")


@pytest.mark.parametrize("body", [
    'supervisorctl restart caddy && wait_for_caddy_ports || test_fail "x"\n',
    'supervisorctl restart caddy; wait_for_caddy_ports\n',
    'supervisorctl restart caddy \\\n    && wait_for_caddy_ports\n',
])
def test_L071_restart_and_wait_on_ONE_line_is_clean(tmp_path, body):
    """The window used to start at the NEXT logical line, so the two most
    natural spellings were rejected — and so was the backslash-continued form,
    because _logical_lines folds it into this same line. A rule that accepts only
    one layout is a formatting opinion, and the author reformats to silence it."""
    d = tmp_path / str(abs(hash(body)))
    d.mkdir()
    img = _tests_repo(d, "26-caddy-auth.sh", body)
    assert "L071" not in errs(img, d), body


@pytest.mark.parametrize("wait", ["assert_service_running caddy", "wait_for_supervisor"])
def test_L071_a_WRAPPER_state_wait_does_not_satisfy_it(tmp_path, wait):
    """These were on the allowlist and should never have been. Both wait for the
    wrapper to reach RUNNING (or for supervisord's own socket), and the premise
    of this rule is that neither says anything about the restarted program:
    caddy.sh clears startsecs=5 while caddy_config_manager.py is still hashing.
    `restart && assert_service_running caddy` reproduced the exact defect the
    rule exists to stop, and was lint-clean."""
    d = tmp_path / wait.split()[0]
    d.mkdir()
    img = _tests_repo(d, "26-caddy-auth.sh",
                      f'supervisorctl restart caddy &>/dev/null\n{wait}\n')
    assert has(img, d, "L071", "without waiting for it to be")


@pytest.mark.parametrize("rel", [
    "derivatives/llama-cpp/ROOT/opt/instance-tools/tests/llama.d/10-serving.sh",
    "derivatives/pytorch/derivatives/comfyui/ROOT/opt/instance-tools/tests/comfyui.d/10-serving.sh",
    "external/vllm/ROOT/opt/instance-tools/tests/vllm.d/10-serving.sh",
])
def test_L071_every_scanned_root_is_locked_in(tmp_path, rel):
    """Same coverage guarantee L069 has. Without one per root, deleting a root
    from the scan list is a silent regression on the trees where the newest
    tests live."""
    img = make(tmp_path, cls="base")
    t = tmp_path / rel
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text('supervisorctl restart caddy &>/dev/null\ntest_pass ok\n')
    t.chmod(0o755)
    assert has(img, tmp_path, "L071", "without waiting for it to be")


# ---- L070: a budget may be raised, never quietly lowered -----------------
#
# L069 gates the STRUCTURE of a readiness check; L070 gates the NUMBERS, which
# structure cannot reach. Before it existed, reverting http_check to --max-time 5
# and the portal wait to 30s — the two exact values that failed cells on
# 2026-08-18 — passed every test in this repo.

def _lib(tmp_path, **over):
    vals = {"SUPERVISOR_READY_TIMEOUT": 60, "PORTAL_READY_TIMEOUT": 120,
            "CADDY_READY_TIMEOUT": 120, "HTTP_CHECK_MAX_TIME": 20}
    vals.update(over)
    img = make(tmp_path, cls="base")
    p = tmp_path / "ROOT/opt/instance-tools/tests/lib.sh"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f'{k}="${{{k}:-{v}}}"\n' for k, v in vals.items())
                 + 'status=$(curl -s --max-time "$HTTP_CHECK_MAX_TIME" -o /dev/null "$@")\n')
    p.chmod(0o755)
    return img


def test_L070_the_shipped_defaults_are_clean(tmp_path):
    assert "L070" not in errs(_lib(tmp_path), tmp_path)


@pytest.mark.parametrize("var,bad", [("HTTP_CHECK_MAX_TIME", 5), ("PORTAL_READY_TIMEOUT", 30),
                                     ("CADDY_READY_TIMEOUT", 30), ("SUPERVISOR_READY_TIMEOUT", 1)])
def test_L070_lowering_a_budget_below_the_measured_floor_fires(tmp_path, var, bad):
    """The first two are the exact values that failed real cells."""
    assert has(_lib(tmp_path, **{var: bad}), tmp_path, "L070", "below the measured")


def test_L070_RAISING_a_budget_is_always_allowed(tmp_path):
    """A floor, not an equality: a new measurement must not need a linter change."""
    assert "L070" not in errs(_lib(tmp_path, HTTP_CHECK_MAX_TIME=45), tmp_path)


def test_L070_baking_the_budget_back_into_the_file_fires(tmp_path):
    """The suite ships INSIDE the image, so a baked number can only be corrected
    by rebuilding and re-promoting every image in the family."""
    img = _lib(tmp_path)
    p = tmp_path / "ROOT/opt/instance-tools/tests/lib.sh"
    p.write_text(p.read_text().replace('HTTP_CHECK_MAX_TIME="${HTTP_CHECK_MAX_TIME:-20}"',
                                       'HTTP_CHECK_MAX_TIME=20'))
    assert has(img, tmp_path, "L070", "not defined as an overridable default")


def test_L070_a_lever_nothing_reads_is_not_a_lever(tmp_path):
    img = _lib(tmp_path)
    p = tmp_path / "ROOT/opt/instance-tools/tests/lib.sh"
    p.write_text(p.read_text().replace('--max-time "$HTTP_CHECK_MAX_TIME"', '--max-time 20'))
    assert has(img, tmp_path, "L070", "cannot be overridden from a")


# ---- L067: a base/ test must hold on a BARE base image -------------------

def test_L067_a_pyworker_assertion_in_base_fires(tmp_path):
    """Proven live on a driver-610 host: `pyworker: RUNNING` then `port 3000 not
    listening after 60s`. Base ships the pyworker unit but no engine to put
    behind it, so the assertion is structurally unsatisfiable there."""
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "opt/instance-tools/tests/base/86-serverless-pyworker.sh",
                       '#!/bin/bash\nassert_service_running "pyworker"\n')
    assert has(img, tmp_path, "L067", "serverless backend")


def test_L067_a_port_3000_assertion_in_base_fires(tmp_path):
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "opt/instance-tools/tests/base/86-serverless-pyworker.sh",
                       '#!/bin/bash\nwait_for_port 3000 60 || test_fail "no"\n')
    assert has(img, tmp_path, "L067", "serverless backend")


def test_L067_the_serverless_SERVICES_test_stays_legal(tmp_path):
    """85 asserts that the non-serverless services are stopped and their ports
    closed — a property base genuinely owns. Its ports (11111/11112/18080) must
    not be mistaken for :3000."""
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "opt/instance-tools/tests/base/85-serverless-services.sh",
                       '#!/bin/bash\nfor port in 11111 11112 18080; do\n'
                       '  ss -tln | grep -q ":${port} " && test_fail "open"\ndone\n')
    assert "L067" not in errs(img, tmp_path)


def test_L067_prose_explaining_the_history_is_allowed(tmp_path):
    img = make(tmp_path, cls="base")
    _write_root_script(tmp_path, "opt/instance-tools/tests/base/85-serverless-services.sh",
                       '#!/bin/bash\n# 86 moved out because pyworker binds :3000 only\n'
                       '# when an engine is present.\ntest_pass "ok"\n')
    assert "L067" not in errs(img, tmp_path)


def test_mut_L067_the_real_engine_suites_carry_the_test(tmp_path):
    """The move must be real: base/ no longer has it, and all four engine
    suites do — each with the lib.sh source that makes is_serverless defined."""
    repo, _img = _real("base-image")
    assert not (repo / "ROOT/opt/instance-tools/tests/base/86-serverless-pyworker.sh").exists()
    suites = [
        "external/vllm/ROOT/opt/instance-tools/tests/vllm.d",
        "external/sglang/ROOT/opt/instance-tools/tests/sglang.d",
        "derivatives/llama-cpp/ROOT/opt/instance-tools/tests/llama.d",
        "derivatives/pytorch/derivatives/comfyui/ROOT/opt/instance-tools/tests/comfyui.d",
    ]
    for s in suites:
        f = repo / s / "20-serverless-pyworker.sh"
        assert f.is_file(), f
        assert f.stat().st_mode & 0o111, f"{f} is not executable (L065)"
        body = f.read_text()
        assert 'source "$(dirname "$0")/../lib.sh"' in body, f"{f} lost its lib.sh source"
        assert "is_serverless" in body and "wait_for_port 3000" in body


def test_L061_covers_the_CS_project(tmp_path):
    """The tracker prefix list named three projects; the tracker has more.

    A CS- id reached a commit message and a shipped test docstring with L061
    green, because the project was simply not in `_INTERNAL_TRACKERS`. A prefix
    allowlist is only as good as its completeness.

    Token built at runtime, per the convention above: a literal here would make
    this file trip the repo-wide scanner it is testing.
    """
    ticket = "CS" + "-" + "4551"
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "note.md").write_text(f"see {ticket} for the escalation\n")
    assert "L061" in {f.code for f in L.lint_repo(tmp_path)}


def test_L061_scans_persisted_review_artifacts(tmp_path):
    """docs/panels/ and docs/redteam/ persist diffs verbatim, and `.diff` was not
    in the scanned extensions — so the artifact recording a leak was itself an
    unscanned leak."""
    ticket = "CON" + "-" + "1234"
    d = tmp_path / "docs" / "redteam" / "x"
    d.mkdir(parents=True, exist_ok=True)
    (d / "artifact.diff").write_text(f"+# see {ticket} for context\n")
    assert "L061" in {f.code for f in L.lint_repo(tmp_path)}


# ---- L068: unguarded VAST_*_PORT_* in a listen address ----------------------

def _shipped(tmp_path, rel, body):
    p = tmp_path / "ROOT" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_mut_L068_fires_on_the_real_syncthing_defect(tmp_path):
    """The exact line that shipped, and what it silently produced.

    Unset -> `tcp://0.0.0.0:` -> syncthing binds its own default [::]:22000, a
    port nothing publishes. Direct sync never works and the exposure allowlist
    entry keyed `env:VAST_TCP_PORT_72299` can never match.
    """
    _shipped(tmp_path, "opt/supervisor-scripts/syncthing.sh",
             '#!/bin/bash\nLISTEN_ADDR="tcp://0.0.0.0:${VAST_TCP_PORT_72299}"\n')
    assert "L068" in {f.code for f in L.lint_repo(tmp_path)}


def test_L068_accepts_the_guarded_idiom_already_in_the_tree(tmp_path):
    """coturn's `-p "${VAST_UDP_PORT_70000:-3478}"` is the blessed pattern; a rule
    that flagged it would be a false-positive generator on a real shipped file."""
    _shipped(tmp_path, "opt/supervisor-scripts/coturn.sh",
             '#!/bin/bash\nturnserver -p "${VAST_UDP_PORT_70000:-3478}" \\\n')
    assert "L068" not in {f.code for f in L.lint_repo(tmp_path)}


def test_L068_accepts_an_explicit_emptiness_guard(tmp_path):
    _shipped(tmp_path, "opt/supervisor-scripts/svc.sh",
             '#!/bin/bash\n'
             'if [[ -n "${VAST_TCP_PORT_72299}" ]]; then\n'
             '    ADDR="tcp://0.0.0.0:${VAST_TCP_PORT_72299}"\n'
             'fi\n')
    assert "L068" not in {f.code for f in L.lint_repo(tmp_path)}


def test_L068_ignores_the_var_outside_a_port_position(tmp_path):
    """Scoped to the interpolation SITE. Reading the variable to log it, or to
    branch on it, cannot produce an empty-port bind — flagging those would make
    the rule noise and get it switched off."""
    _shipped(tmp_path, "opt/supervisor-scripts/svc.sh",
             '#!/bin/bash\n'
             'echo "sync port is ${VAST_TCP_PORT_72299}"\n'
             '[[ -z "$VAST_TCP_PORT_72299" ]] && echo unmapped\n')
    assert "L068" not in {f.code for f in L.lint_repo(tmp_path)}


def test_L068_does_not_fire_on_PROSE_describing_the_bug(tmp_path):
    """It did, first time it ran — on the docstring in the exposure scan that
    explains this very defect, inside a `python3 - <<'PY'` heredoc in a .sh (so
    the AST-based prose helper returned nothing for it). A rule that reddens the
    documentation of the thing it detects is a rule nobody can keep green."""
    _shipped(tmp_path, "opt/instance-tools/tests/base/28-x.sh",
             '#!/bin/bash\npython3 - <<\'PY\'\n'
             'def resolve_port(spec):\n'
             '    """A literal port, or env:NAME.\n\n'
             '    syncthing\'s sync listener is tcp://0.0.0.0:$VAST_TCP_PORT_72299,\n'
             '    so a static key can never match it.\n'
             '    """\n'
             '    return spec\n'
             'PY\n')
    assert "L068" not in {f.code for f in L.lint_repo(tmp_path)}


def test_mut_the_shipped_syncthing_reconciles_and_guards(tmp_path):
    """Round-trip on the REAL file: the fix must guard, must reconcile away a
    malformed entry from an earlier boot (config.xml is on overlayfs and survives
    stop/start), and must not use the substring-colliding guard."""
    repo = find_repo_root(Path(__file__).resolve())
    body = (repo / "ROOT" / "opt" / "supervisor-scripts" / "syncthing.sh").read_text()
    assert 'raw-listen-addresses remove' in body, (
        "the malformed entry from an earlier boot is never removed; overlayfs "
        "persists it across stop/start so the fix would not reach a booted instance")
    assert '=~ ^[0-9]+$' in body, "the platform port variable is not validated"
    assert 'grep -qxF' in body, (
        "grep -qF substring-collides: 'tcp://0.0.0.0:' is a prefix of every "
        "well-formed 'tcp://0.0.0.0:NNNN'")
    assert 'no direct TCP listener' in body, (
        "when the port is unmapped the script must configure no listener and say "
        "so, rather than silently binding a default that cannot receive")


# ---- L073: a serverless gate's template must map the worker port (ADR 0031) ----


def _wire_serverless_gate(repo, template_dir, extra_env="SERVERLESS=true\nBACKEND=openai\n"):
    """A qa-gate caller that turns serverless ON, the way build-vllm.yml's does."""
    wf = repo / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "build-img.yml").write_text(
        "jobs:\n"
        "  qa:\n"
        "    uses: ./.github/workflows/qa-gate.yml\n"
        "    with:\n"
        "      template_dir: " + template_dir + "\n"
        "  qa-serverless:\n"
        "    uses: ./.github/workflows/qa-gate.yml\n"
        "    with:\n"
        "      template_dir: " + template_dir + "\n"
        "      extra_env: |\n"
        + "".join(f"        {ln}\n" for ln in extra_env.strip().splitlines()))


def _tpl_with_ports(img, ports):
    body = "name: QA\nimage: vastai/x\n"
    if ports is not None:
        body += "ports:\n" + "".join(f'  - "{p}"\n' for p in ports)
    _write_template(img, body + _FLOORS)


def test_L073_serverless_gate_without_the_worker_port_fires(tmp_path):
    """THE mutation, and it is the defect as measured. The platform injects
    VAST_TCP_PORT_<n> only for MAPPED ports, and the SDK looks it up unguarded:
    with 3000 unmapped the worker died `KeyError: 'VAST_TCP_PORT_3000'` inside
    Metrics(), before binding the port or running a benchmark."""
    img = make(tmp_path)
    _tpl_with_ports(img, ["1111:1111", "8080:8080"])
    _wire_serverless_gate(tmp_path, "img/templates/qa")
    assert has(img, tmp_path, "L073", "does not map port 3000")


def test_L073_mapping_the_worker_port_is_clean(tmp_path):
    img = make(tmp_path)
    _tpl_with_ports(img, ["1111:1111", "3000:3000"])
    _wire_serverless_gate(tmp_path, "img/templates/qa")
    assert "L073" not in errs(img, tmp_path)


def test_L073_does_not_fire_when_no_gate_enables_serverless(tmp_path):
    """A template is never asked to map a port for a mode it is not run in. This is
    what keeps the rule off every non-serverless QA template in the repo."""
    img = make(tmp_path)
    _tpl_with_ports(img, ["1111:1111"])
    _wire_gate(tmp_path, "img/templates/qa")          # plain gate, no SERVERLESS
    assert "L073" not in errs(img, tmp_path)


def test_L073_reads_the_worker_port_from_the_caller(tmp_path):
    """WORKER_PORT is what the SDK interpolates, so a gate that moves it moves the
    obligation with it — a hardcoded 3000 would check the wrong port silently."""
    img = make(tmp_path)
    _tpl_with_ports(img, ["3000:3000"])
    _wire_serverless_gate(tmp_path, "img/templates/qa",
                          "SERVERLESS=true\nWORKER_PORT=3100\n")
    assert has(img, tmp_path, "L073", "does not map port 3100")


def test_L073_pairs_extra_env_with_its_OWN_template_dir(tmp_path):
    """The pair that matters is (template_dir, extra_env) on the SAME job. A workflow
    with a standard `qa` and a `qa-serverless` — which build-vllm.yml has — would fool
    any rule that grepped the file for SERVERLESS=true and a template_dir separately."""
    img = make(tmp_path)
    _tpl_with_ports(img, ["1111:1111"])
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "build-img.yml").write_text(
        "jobs:\n"
        "  qa:\n"
        "    uses: ./.github/workflows/qa-gate.yml\n"
        "    with:\n"
        "      template_dir: img/templates/qa\n"
        "  qa-serverless:\n"
        "    uses: ./.github/workflows/qa-gate.yml\n"
        "    with:\n"
        "      template_dir: img/templates/somewhere-else\n"
        "      extra_env: |\n        SERVERLESS=true\n")
    assert "L073" not in errs(img, tmp_path)


def test_L073_accepts_a_bare_port(tmp_path):
    img = make(tmp_path)
    _tpl_with_ports(img, ["3000"])
    _wire_serverless_gate(tmp_path, "img/templates/qa")
    assert "L073" not in errs(img, tmp_path)


# ---- L074: a template must not publish an `internal` port (ADR 0028) ----


def _allowlist(img, body):
    d = img.dir / "ROOT/opt/instance-tools/tests/exposure-allowlist"
    d.mkdir(parents=True, exist_ok=True)
    (d / "50-x.conf").write_text(body)


def test_L074_publishing_an_internal_port_fires(tmp_path):
    """THE hole, and it was live for one commit. Declaring Ray's pinned range made
    every port in it pass the exposure scan unconditionally — including one a
    template maps. Ray's GCS has no auth of its own, which is the only reason it was
    allowed to bind wide, so publishing 6379 puts an unauthenticated cluster control
    plane on the internet and the scan prints `ok`."""
    img = make(tmp_path)
    _allowlist(img, "range:6379-6499/tcp internal Ray\n")
    _write_template(img, 'name: QA\nimage: vastai/x\nports:\n  - "6379:6379"\n' + _FLOORS)
    assert has(img, tmp_path, "L074", "declares `internal`")


def test_L074_a_port_outside_the_range_is_clean(tmp_path):
    img = make(tmp_path)
    _allowlist(img, "range:6379-6499/tcp internal Ray\n")
    _write_template(img, 'name: QA\nimage: vastai/x\nports:\n  - "8000:8000"\n' + _FLOORS)
    assert "L074" not in errs(img, tmp_path)


def test_L074_reads_the_internal_side_of_the_mapping(tmp_path):
    """Vast writes external:internal. The INTERNAL side is what the service binds and
    therefore what publication exposes — matching on the external side would miss the
    mapping that actually opens the port."""
    img = make(tmp_path)
    _allowlist(img, "range:6379-6499/tcp internal Ray\n")
    _write_template(img, 'name: QA\nimage: vastai/x\nports:\n  - "40001:6390"\n' + _FLOORS)
    assert has(img, tmp_path, "L074", "maps port 6390")


def test_L074_only_the_internal_class_constrains(tmp_path):
    """sshd and syncthing are allowlisted AND published, correctly — they
    authenticate. Only `internal` makes the negative claim."""
    img = make(tmp_path)
    _allowlist(img, "22/tcp raw sshd\n")
    _write_template(img, 'name: QA\nimage: vastai/x\nports:\n  - "22:22"\n' + _FLOORS)
    assert "L074" not in errs(img, tmp_path)


def test_L074_literal_internal_entries_count_too(tmp_path):
    img = make(tmp_path)
    _allowlist(img, "9001/tcp internal something\n")
    _write_template(img, 'name: QA\nimage: vastai/x\nports:\n  - "9001:9001"\n' + _FLOORS)
    assert has(img, tmp_path, "L074", "maps port 9001")


# ---- L075: the selftest's pinned PATH must reach the image's own tools ----


def _selftest(img, path_line):
    d = img.dir / "ROOT/opt/instance-tools/tests/base"
    d.mkdir(parents=True, exist_ok=True)
    (d / "13-provisioner-selftest.sh").write_text(
        "#!/bin/bash\n" + path_line + "\ntest_pass \"ok\"\n")


def test_L075_a_system_only_path_fires(tmp_path):
    """THE regression, measured on the first SGLang QA cell ever run:
    `Service registration failed: [Errno 2] No such file or directory: 'supervisorctl'`
    — deterministic on both cells and on the retry, while vLLM passed the identical
    test because its upstream base also ships supervisorctl in /usr/local/bin. A
    self-test whose verdict depends on the base image is testing the base image."""
    img = make(tmp_path, cls="base")
    _selftest(img, "_PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    assert has(img, tmp_path, "L075", "/opt/sys-venv/shim")


def test_L075_including_the_image_tool_dirs_is_clean(tmp_path):
    img = make(tmp_path, cls="base")
    _selftest(img, "_PATH=/opt/instance-tools/bin:/opt/sys-venv/shim:/usr/bin:/bin")
    assert "L075" not in errs(img, tmp_path)


def test_L075_a_missing_pin_is_itself_the_finding(tmp_path):
    """The pinned PATH is the point of the `env -i` run. If it is gone, the run is no
    longer pinned and the rule cannot check what it exists to check."""
    img = make(tmp_path, cls="base")
    _selftest(img, "# no pin here")
    assert has(img, tmp_path, "L075", "no _PATH assignment")


def test_L075_is_scoped_to_base(tmp_path):
    """The file ships in base and every other image inherits it; only base owns it."""
    img = make(tmp_path, cls="pytorch-nested")
    _selftest(img, "_PATH=/usr/bin:/bin")
    assert "L075" not in errs(img, tmp_path)


# ---- L076: a llama.cpp image must assert its CUDA backend actually serves (ADR 0016) ----


def _llama_suite(img, body):
    """Ship a llama.d suite, the way derivatives/llama-cpp does."""
    d = img.dir / "ROOT/opt/instance-tools/tests/llama.d"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "10-llama-serving.sh"
    f.write_text('#!/bin/bash\ntest_fail "not serving"\n')
    f.chmod(0o755)
    if body is not None:
        g = d / "11-llama-offload.sh"
        g.write_text(body)
        g.chmod(0o755)


_OFFLOAD_OK = ('#!/bin/bash\n'
               'devices=$(llama-server --list-devices)\n'
               'grep -q CUDA0 <<< "$devices" || fail_later "x" "no gpu"\n'
               'report_failures\n')


def test_L076_a_llama_image_with_no_offload_assertion_fires(tmp_path):
    """THE mutation, and the state the shipped image was actually in: a llama.d suite
    that asserts serving, health, models and tokens — every one of which a CPU-only
    llama.cpp satisfies, because ggml_backend_dl turns a failed dlopen of
    libggml-cuda.so into a silent CPU fallback rather than a crash."""
    img = make(tmp_path)
    _llama_suite(img, None)
    _gated(img, f"{_TRIO} llama.d/10-llama-serving")
    assert has(img, tmp_path, "L076", "no GPU-offload assertion")


def test_L076_an_offload_test_that_cannot_fail_does_not_satisfy_it(tmp_path):
    """A file that greps for the device and never calls test_fail/fail_later reports
    `passed` on a CPU-only box. Same hole L059 exists for, one layer up: the rule
    requires a real failure path, not a filename."""
    img = make(tmp_path)
    _llama_suite(img, '#!/bin/bash\nllama-server --list-devices\ntest_pass "looks fine"\n')
    _gated(img, f"{_TRIO} llama.d/10-llama-serving")
    assert has(img, tmp_path, "L076", "no GPU-offload assertion")


def test_L076_a_file_existence_check_does_not_satisfy_it(tmp_path):
    """`test -f …libggml-cuda.so` is L056's build-time assertion and is NOT evidence
    the backend loads: the file is present in exactly the failure this rule exists
    to catch — a bundle whose libcublas minor or host SM the driver cannot serve."""
    img = make(tmp_path)
    _llama_suite(img, '#!/bin/bash\ntest -f /opt/llama.cpp/libggml-cuda.so || test_fail "missing"\n')
    _gated(img, f"{_TRIO} llama.d/10-llama-serving")
    assert has(img, tmp_path, "L076", "no GPU-offload assertion")


def test_L076_an_offload_test_not_named_in_the_template_fires(tmp_path):
    """The second hole: the assertion exists but nothing requires it, so it can
    test_skip — no GPU, no model, an unset var — and the gate stays green."""
    img = make(tmp_path)
    _llama_suite(img, _OFFLOAD_OK)
    _gated(img, f"{_TRIO} llama.d/10-llama-serving")
    assert has(img, tmp_path, "L076", "does not require the GPU-offload test")


def test_L076_asserted_and_required_is_clean(tmp_path):
    img = make(tmp_path)
    _llama_suite(img, _OFFLOAD_OK)
    _gated(img, f"{_TRIO} llama.d/10-llama-serving llama.d/11-llama-offload")
    assert "L076" not in errs(img, tmp_path)


def test_L076_does_not_fire_on_an_image_without_a_llama_suite(tmp_path):
    """Scoped to the engine whose backend is dlopen'd. An image with no llama.d owes
    nothing here — the rule must not invent an obligation it cannot satisfy."""
    img = make(tmp_path)
    _own_suite(img)
    _gated(img, f"{_TRIO} app.d/10-serving")
    assert "L076" not in errs(img, tmp_path)


def test_L076_a_comment_mentioning_offload_does_not_satisfy_it(tmp_path):
    """THE evasion the first version shipped with, found by review and reproduced on
    the real repo: delete the offload test entirely, then put the word "offloading" in
    a COMMENT in a sibling that already has an unrelated test_fail and is already named
    in INSTANCE_TEST_REQUIRE_PASS. The evidence regex read the raw body, so the
    baseline reported CLEAN with the assertion gone — the rule certified its own
    absence. Same trap `_has_failure_path`'s docstring documents, one level up."""
    img = make(tmp_path)
    d = img.dir / "ROOT/opt/instance-tools/tests/llama.d"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "10-llama-serving.sh"
    f.write_text('#!/bin/bash\n'
                 '# NOTE: offloading of layers to GPU is handled elsewhere\n'
                 '# see also: llama-server --list-devices\n'
                 'test_fail "not serving"\n')
    f.chmod(0o755)
    _gated(img, f"{_TRIO} llama.d/10-llama-serving")
    assert has(img, tmp_path, "L076", "no GPU-offload assertion")


def test_L076_a_neutered_assertion_keeping_its_comments_does_not_satisfy_it(tmp_path):
    """The edit a maintainer reaches for the first time the cell reds a release: keep
    the file and its explanatory header, demote both arms to echo, and leave the one
    unrelated test_fail on the readiness probe. Evidence words survive only in prose."""
    img = make(tmp_path)
    _llama_suite(img, '#!/bin/bash\n'
                      '# gates on llama-server --list-devices and --query-compute-apps\n'
                      'wait_for_url http://127.0.0.1:18000/health 60 || test_fail "not up"\n'
                      'echo "backend check disabled"\n'
                      'report_failures\n')
    _gated(img, f"{_TRIO} llama.d/10-llama-serving llama.d/11-llama-offload")
    assert has(img, tmp_path, "L076", "no GPU-offload assertion")


# ---- L077: a declared expiry must still be in date, and must be a date (ADR 0034) ----


def _expiring_stage(tmp_path, expires_line):
    """A shipped boot stage declaring itself temporary, the way ADR 0034's does."""
    d = tmp_path / "ROOT/etc/vast_boot.d"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "01-detect-serverless.sh"
    f.write_text(f"#!/bin/bash\n# Stage 01 — a bridge.\n#\n{expires_line}\n#\nexport SERVERLESS=true\n")
    f.chmod(0o755)
    return tmp_path


def test_L077_a_passed_expiry_fires(tmp_path):
    """THE mutation. A bridge whose date has gone by must stop being invisible — the
    whole failure mode is that nobody revisits it and it quietly becomes load-bearing."""
    repo = _expiring_stage(tmp_path, "# EXPIRES: 2020-01-01")
    hits = [f for f in L.lint_repo(repo) if f.code == "L077"]
    assert hits and hits[0].severity == L.ERROR
    assert "has passed" in hits[0].msg


def test_L077_an_unparseable_expiry_fires_immediately(tmp_path):
    """`EXPIRES: TBD` is how an expiry becomes decorative. Fail closed: an expiry nobody
    can evaluate is worse than none, because it reads like a control and is not one."""
    repo = _expiring_stage(tmp_path, "# EXPIRES: TBD")
    hits = [f for f in L.lint_repo(repo) if f.code == "L077"]
    assert hits and hits[0].severity == L.ERROR
    assert "not a YYYY-MM-DD date" in hits[0].msg


def test_L077_an_in_date_expiry_warns_but_does_not_gate(tmp_path):
    """It must not block while the bridge is legitimately in use — an ERROR here would
    make the declaration itself a cost, and people would stop declaring."""
    repo = _expiring_stage(tmp_path, "# EXPIRES: 2099-01-01")
    hits = [f for f in L.lint_repo(repo) if f.code == "L077"]
    assert hits and hits[0].severity == L.WARN
    assert not [f for f in hits if f.severity == L.ERROR]


def test_L077_a_script_with_no_expiry_is_untouched(tmp_path):
    """Scoped to files that OPT IN by declaring. The rule must not invent an obligation
    for every shipped script."""
    d = tmp_path / "ROOT/etc/vast_boot.d"
    d.mkdir(parents=True, exist_ok=True)
    (d / "10-prep-env.sh").write_text("#!/bin/bash\necho hello\n")
    assert "L077" not in {f.code for f in L.lint_repo(tmp_path)}


def test_L077_the_real_repo_is_in_date():
    """The baseline assertion: whatever bridges exist right now are still declared and
    still current. This is the test that goes red on the day someone must decide."""
    repo = find_repo_root(Path(__file__).resolve().parent)
    expired = [f for f in L.lint_repo(repo) if f.code == "L077" and f.severity == L.ERROR]
    assert not expired, f"a declared expiry has passed: {[(f.path, f.msg) for f in expired]}"


def test_L073_covers_a_cell_that_infers_serverless(tmp_path):
    """ADR 0034. A cell can now turn serverless on by supplying the autoscaler signals
    instead of SERVERLESS=true. L073 keyed strictly on the literal, so such a cell would
    have dropped out of the rule entirely and the worker-port requirement would go
    unenforced — the defect L073 exists for (KeyError: 'VAST_TCP_PORT_3000', measured
    live on the first serverless cell ever run) would come straight back."""
    img = make(tmp_path)
    _write_template(img, "name: QA\nimage: vastai/x\n" + _FLOORS)   # no port 3000 mapped
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "build-img.yml").write_text(
        "jobs:\n  qa:\n    uses: ./.github/workflows/qa-gate.yml\n"
        "    with:\n      template_dir: img/templates/qa\n"
        "      extra_env: |\n        MASTER_TOKEN=sentinel\n        REPORT_ADDR=https://a\n")
    assert "L073" in errs(img, tmp_path)


def test_L073_ignores_a_cell_with_only_one_signal(tmp_path):
    """One signal does not activate the mode in the image, so it must not be treated as
    a serverless cell here either — mirroring the activation rule rather than guessing."""
    img = make(tmp_path)
    _write_template(img, "name: QA\nimage: vastai/x\n" + _FLOORS)
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "build-img.yml").write_text(
        "jobs:\n  qa:\n    uses: ./.github/workflows/qa-gate.yml\n"
        "    with:\n      template_dir: img/templates/qa\n"
        "      extra_env: |\n        MASTER_TOKEN=sentinel\n")
    assert "L073" not in errs(img, tmp_path)


def test_L073_explicit_false_beats_the_signals(tmp_path):
    """SERVERLESS=false wins over the inference in the image; the rule must agree."""
    img = make(tmp_path)
    _write_template(img, "name: QA\nimage: vastai/x\n" + _FLOORS)
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "build-img.yml").write_text(
        "jobs:\n  qa:\n    uses: ./.github/workflows/qa-gate.yml\n"
        "    with:\n      template_dir: img/templates/qa\n"
        "      extra_env: |\n        SERVERLESS=false\n        MASTER_TOKEN=s\n        REPORT_ADDR=https://a\n")
    assert "L073" not in errs(img, tmp_path)


def test_L073_ignores_a_cell_that_skips_the_worker(tmp_path):
    """The rule exists because the pyworker SDK looks up VAST_TCP_PORT_3000 unguarded.
    A cell that sets SUPERVISOR_SKIP_PYWORKER=true starts no worker, so there is nothing
    to look it up — demanding a port mapping there would be cargo-culting the rule past
    its own rationale. Base's detection cell is exactly this: mode switch, no engine."""
    img = make(tmp_path)
    _write_template(img, "name: QA\nimage: vastai/x\n" + _FLOORS)
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "build-img.yml").write_text(
        "jobs:\n  qa:\n    uses: ./.github/workflows/qa-gate.yml\n"
        "    with:\n      template_dir: img/templates/qa\n"
        "      extra_env: |\n        MASTER_TOKEN=s\n        REPORT_ADDR=https://a\n"
        "        SUPERVISOR_SKIP_PYWORKER=true\n")
    assert "L073" not in errs(img, tmp_path)
# ---- L078: the engine must listen where the OpenAI-core worker proxies (ADR 0031) ----
#
# pyworker's workers/openai/core.py proxies to a HARDCODED http://127.0.0.1:18000 —
# MODEL_SERVER_URL/MODEL_SERVER_PORT are module constants, the only values in that file
# that are NOT os.environ reads. So the address is an obligation on the image and its
# template, and nothing in the tree gated it.

_BACKEND_DF = VALID_DF + "ENV BACKEND=llama\n"


def _engine(img, launch):
    """Ship the engine's supervisor script, the way derivatives/llama-cpp does."""
    sp = img.dir / "ROOT/opt/supervisor-scripts/llama.sh"
    sp.write_text('#!/bin/bash\n. "${utils}/logging.sh"\n' + launch + "\n")
    sp.chmod(0o755)


def _pinned_template(img, args):
    _write_template(img, f"name: QA\nimage: vastai/x\nenv:\n  LLAMA_ARGS: \"{args}\"\n{_FLOORS}")
    _wire_gate(img.dir.parent, "img/templates/qa")


_GOOD_ARGS = "--host 127.0.0.1 --port 18000 --ctx-size 4096"


def test_L078_a_bind_hidden_in_an_erasable_default_fires(tmp_path):
    """THE mutation, and the exact state derivatives/llama-cpp shipped in: the pin
    lives in `${LLAMA_ARGS:---port 18000}`, so it holds only while LLAMA_ARGS is
    UNSET. A template setting it for any unrelated reason (-ngl 99, --ctx-size 8192)
    erases the port and llama-server falls back to its own default of 8080
    (llama.cpp common.h), away from the 18000 the worker proxies to."""
    img = make(tmp_path, df=_BACKEND_DF)
    _engine(img, 'pty llama-server -hf "$LLAMA_MODEL" ${LLAMA_ARGS:---port 18000} 2>&1')
    _pinned_template(img, _GOOD_ARGS)
    assert has(img, tmp_path, "L078", "hidden in `${LLAMA_ARGS:-...}`")


def test_L078_a_hidden_host_also_fires(tmp_path):
    """The host half is the worse one: an erased --host leaves the engine on the
    upstream default, and vLLM's is 0.0.0.0 — a public bind, not merely a missed proxy."""
    img = make(tmp_path, df=_BACKEND_DF)
    _engine(img, 'pty llama-server ${LLAMA_ARGS:---host 127.0.0.1} 2>&1')
    _pinned_template(img, _GOOD_ARGS)
    assert has(img, tmp_path, "L078", "hidden in `${LLAMA_ARGS:-...}`")


def test_L078_pinning_each_flag_unconditionally_is_clean(tmp_path):
    """The fix shape: pin outside the default, adding each flag only when the
    template has not already supplied it, so an explicit template choice still wins."""
    img = make(tmp_path, df=_BACKEND_DF)
    _engine(img, 'llama_args="${LLAMA_ARGS:-}"\n'
                 'if [[ ! "${llama_args}" =~ (^|[[:space:]])--host([=[:space:]]|$) ]]; then\n'
                 '    llama_args="--host 127.0.0.1 ${llama_args}"\n'
                 'fi\n'
                 'if [[ ! "${llama_args}" =~ (^|[[:space:]])--port([=[:space:]]|$) ]]; then\n'
                 '    llama_args="--port 18000 ${llama_args}"\n'
                 'fi\n'
                 'pty llama-server -hf "$LLAMA_MODEL" ${llama_args} 2>&1')
    _pinned_template(img, _GOOD_ARGS)
    assert "L078" not in errs(img, tmp_path)


def test_L078_a_comment_carrying_the_old_shape_does_not_fire(tmp_path):
    """Comment-stripped, and this is load-bearing rather than theoretical: the real
    llama.d/10-llama-serving.sh documents the quirk by quoting the defective line
    verbatim. A raw-body match would fire on the file that DESCRIBES the bug, which
    is the mirror image of L076's trap (a comment SATISFYING a rule) and just as wrong."""
    img = make(tmp_path, df=_BACKEND_DF)
    _engine(img, '# it used to be ${LLAMA_ARGS:---port 18000}, which a template erased\n'
                 'pty llama-server --host 127.0.0.1 --port 18000 ${LLAMA_ARGS:-} 2>&1')
    _pinned_template(img, _GOOD_ARGS)
    assert "L078" not in errs(img, tmp_path)


def test_L078_does_not_fire_on_a_backend_with_a_different_worker_address(tmp_path):
    """Scoped to the workers that proxy to 18000. comfyui-json/ace/wan hardcode 18288
    and tgi 5001, so an image baking one of those owes nothing HERE — and comfyui
    carries the same erasable SHAPE (`${COMFYUI_ARGS:---disable-auto-launch --port
    18188 ...}`) without the same contract. The rule must not invent an obligation
    from a shape alone."""
    img = make(tmp_path, df=VALID_DF + "ENV BACKEND=comfyui-json\n")
    _engine(img, 'pty comfyui ${COMFYUI_ARGS:---port 18188} 2>&1')
    assert "L078" not in errs(img, tmp_path)


def test_L078_does_not_fire_on_an_image_with_no_backend(tmp_path):
    """Most images ship no serverless worker at all and have no 18000 obligation."""
    img = make(tmp_path)
    _engine(img, 'pty app ${APP_ARGS:---port 17860} 2>&1')
    assert "L078" not in errs(img, tmp_path)


def test_L078_a_gating_template_with_no_port_pin_fires(tmp_path):
    """The other arm, and the one that covers vLLM and SGLang: vllm.sh/sglang.sh
    interpolate ${VLLM_ARGS:-}/${SGLANG_ARGS:-} BARE, so the address exists only in
    the template. Drop the pin and nothing else supplies it."""
    img = make(tmp_path, df=_BACKEND_DF)
    _pinned_template(img, "--host 127.0.0.1 --ctx-size 4096")
    assert has(img, tmp_path, "L078", "no --port 18000")
    # ...and ONLY the port half: with both absent the message names both, so without
    # this the test would pass even if _PINNED_HOST never matched anything.
    assert not has(img, tmp_path, "L078", "no --host")


def test_L078_a_gating_template_with_no_host_pin_fires(tmp_path):
    img = make(tmp_path, df=_BACKEND_DF)
    _pinned_template(img, "--port 18000 --ctx-size 4096")
    assert has(img, tmp_path, "L078", "no --host 127.0.0.1")
    assert not has(img, tmp_path, "L078", "no --port")


def test_L078_a_near_miss_port_does_not_satisfy_it(tmp_path):
    """8000 is vLLM's own default and 180000 is a typo; neither is the contract.
    Matching on the substring `--port 18000` alone would accept 180000."""
    for bad in ("--host 127.0.0.1 --port 8000", "--host 127.0.0.1 --port 180000"):
        img = make(tmp_path, df=_BACKEND_DF)
        _pinned_template(img, bad)
        assert has(img, tmp_path, "L078", "no --port 18000"), bad


def test_L078_a_decoy_args_variable_does_not_satisfy_it(tmp_path):
    """THE hole a first draft of this rule had, and the reason it is worth a test of
    its own. Joining every key ending in `_ARGS` and searching the concatenation meant
    a variable NO engine reads satisfied the requirement while LLAMA_ARGS stayed empty
    — measured: `L078 fires: False`. Same satisfied-by-cosmetics trap L076 documents,
    reintroduced one level up in the rule written to avoid it."""
    img = make(tmp_path, df=_BACKEND_DF)
    _write_template(img, "name: QA\nimage: vastai/x\nenv:\n"
                         "  DUMMY_ARGS: \"--host 127.0.0.1 --port 18000\"\n"
                         f"  LLAMA_ARGS: \"--ctx-size 4096\"\n{_FLOORS}")
    _wire_gate(img.dir.parent, "img/templates/qa")
    assert has(img, tmp_path, "L078", "in LLAMA_ARGS")


def test_L078_the_two_flags_split_across_variables_does_not_satisfy_it(tmp_path):
    """The same hole from the other direction: each flag present, neither reaching the
    engine. Both must be in the ONE variable the launcher interpolates."""
    img = make(tmp_path, df=_BACKEND_DF)
    _write_template(img, "name: QA\nimage: vastai/x\nenv:\n"
                         "  A_ARGS: \"--host 127.0.0.1\"\n  B_ARGS: \"--port 18000\"\n"
                         f"  LLAMA_ARGS: \"--ctx-size 4096\"\n{_FLOORS}")
    _wire_gate(img.dir.parent, "img/templates/qa")
    assert has(img, tmp_path, "L078", "in LLAMA_ARGS")


def test_L078_a_pin_outside_an_args_variable_does_not_satisfy_it(tmp_path):
    """A template that only MENTIONS the address configures nothing."""
    img = make(tmp_path, df=_BACKEND_DF)
    _write_template(img, "name: QA\nimage: vastai/x\nenv:\n"
                         "  NOTE: \"serves on --host 127.0.0.1 --port 18000\"\n"
                         f"  LLAMA_ARGS: \"--ctx-size 4096\"\n{_FLOORS}")
    _wire_gate(img.dir.parent, "img/templates/qa")
    assert has(img, tmp_path, "L078", "in LLAMA_ARGS")


def test_L078_the_equals_spelling_is_accepted(tmp_path):
    """`--host=127.0.0.1` is valid argparse, which is what vLLM and SGLang use. A rule
    that demanded the space spelling would be a FALSE error on a correct template."""
    img = make(tmp_path, df=_BACKEND_DF)
    _pinned_template(img, "--host=127.0.0.1 --port=18000 --ctx-size 4096")
    assert "L078" not in errs(img, tmp_path)


def test_L078_a_single_quoted_backend_does_not_exempt_the_image(tmp_path):
    """`ENV BACKEND='llama'` is valid docker and means `llama`. Stripping only `"`
    left `'llama'`, which matched no known backend, so ONE quote character turned an
    ERROR gate off for the whole image with no diagnostic — measured."""
    img = make(tmp_path, df=VALID_DF + "ENV BACKEND='llama'\n")
    _engine(img, 'pty llama-server ${LLAMA_ARGS:---port 18000} 2>&1')
    _pinned_template(img, _GOOD_ARGS)
    assert has(img, tmp_path, "L078", "hidden in `${LLAMA_ARGS:-...}`")


def test_L078_an_erasable_default_in_any_spelling_fires(tmp_path):
    """`:-` is not the only erasable form. `${VAR-...}` and `${VAR:=...}` supply the
    default on the same condition and are erased by a template the same way."""
    for launch in ('pty llama-server ${LLAMA_ARGS---port 18000} 2>&1',
                   'pty llama-server ${LLAMA_ARGS:=--port 18000} 2>&1'):
        img = make(tmp_path, df=_BACKEND_DF)
        _engine(img, launch)
        _pinned_template(img, _GOOD_ARGS)
        assert has(img, tmp_path, "L078", "hidden in"), launch


def test_baked_env_reads_what_the_image_actually_ships(tmp_path):
    """_baked_env's contract, table-driven — it had none, which is why the
    single-quote exemption above was invisible. Docker semantics: the FINAL stage
    wins, and within it the LAST write wins."""
    cases = [
        ("ENV BACKEND=llama\n", "llama"),
        ("ENV BACKEND='llama'\n", "llama"),
        ('ENV BACKEND="llama"\n', "llama"),
        ("ENV A=1 BACKEND=llama B=2\n", "llama"),                 # multi-pair line
        ("ENV BACKEND=sglang\nENV BACKEND=llama\n", "llama"),      # last write wins
        ("ENV BACKEND=llama\nFROM scratch\n", ""),                # builder-stage only
        ("ENV OTHER=llama\n", ""),
    ]
    for tail, want in cases:
        img = make(tmp_path, df=VALID_DF + tail)
        assert L._baked_env(img, "BACKEND") == want, (tail, L._baked_env(img, "BACKEND"))


def test_L078_a_fully_pinned_image_and_template_is_clean(tmp_path):
    img = make(tmp_path, df=_BACKEND_DF)
    _engine(img, 'pty llama-server --host 127.0.0.1 --port 18000 ${LLAMA_ARGS:-} 2>&1')
    _pinned_template(img, _GOOD_ARGS)
    assert "L078" not in errs(img, tmp_path)


def test_L078_the_real_engine_images_pin_the_worker_address():
    """Round-trip on the REAL images: all three OpenAI-core engines must be clean,
    which is the assertion that would have failed before the llama.sh fix."""
    repo = find_repo_root(Path(__file__).resolve().parent)
    seen = []
    for img in discover(repo):
        if L._baked_env(img, "BACKEND").lower() not in L._OPENAI_CORE_BACKENDS:
            continue
        seen.append(img.name)
        bad = [f.msg for f in lint_image(img, repo) if f.code == "L078"]
        assert not bad, f"{img.name}: {bad}"
    assert sorted(seen) == ["llama-cpp", "sglang", "vllm"], seen


# ---- L079: a serverless QA cell must not be able to reach the production autoscaler ----
#
# The worker POSTs to `${REPORT_ADDR}/worker_status/`, and BOTH layers default that to
# the live autoscaler — start_server.sh `${REPORT_ADDR:-https://run.vast.ai}` and the
# SDK's `os.environ.get("REPORT_ADDR", "https://run.vast.ai")`. Setting nothing does not
# send nothing; it sends to production.


def _sl_gate(tmp_path, env: str, job="qa-serverless"):
    """A workflow handing a serverless cell to qa-gate.yml, the way a build workflow does."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "build-img.yml").write_text(
        f"jobs:\n  {job}:\n    uses: ./.github/workflows/qa-gate.yml\n"
        f"    with:\n      template_dir: img/templates/qa\n"
        f"      extra_env: |\n{env}")
    return tmp_path


def _l079(repo):
    return [f for f in L.lint_repo(repo) if f.code == "L079" and f.severity == L.ERROR]


def test_L079_a_declared_serverless_cell_with_no_report_addr_fires(tmp_path):
    """THE mutation, and the state build-sglang.yml and build-llama-cpp.yml were
    actually in: the cell deliberately passes nothing beyond SERVERLESS=true — correct
    for BACKEND/MODEL_NAME/MODEL_LOG, which must come from the image's own bakes, and
    wrong for the address of a live external service."""
    repo = _sl_gate(tmp_path, "        SERVERLESS=true\n")
    assert any("sets no REPORT_ADDR" in f.msg for f in _l079(repo))


def test_L079_a_sentinel_address_is_clean(tmp_path):
    repo = _sl_gate(tmp_path, "        SERVERLESS=true\n"
                              "        REPORT_ADDR=https://qa-detection-sentinel.invalid\n")
    assert not _l079(repo)


def test_L079_loopback_is_also_accepted(tmp_path):
    """A loopback literal cannot leave the box either; the rule is about reachability,
    not about one spelling."""
    for addr in ("http://127.0.0.1:1", "http://localhost:9/x", "http://[::1]:1"):
        repo = _sl_gate(tmp_path, f"        SERVERLESS=true\n        REPORT_ADDR={addr}\n")
        assert not _l079(repo), addr


def test_L079_a_real_endpoint_fires_even_if_it_is_not_the_known_one(tmp_path):
    """ALLOWLIST, not blocklist. Blocking `run.vast.ai` by name would pass every other
    live endpoint someone reaches for next — the enumerate-the-failures shape that let
    a cancelled run announce a promotion earlier the same day."""
    for addr in ("https://run.vast.ai", "https://console.vast.ai/api",
                 "https://staging.example.com", "https://run.vast.ai.invalid.example.com"):
        repo = _sl_gate(tmp_path, f"        SERVERLESS=true\n        REPORT_ADDR={addr}\n")
        assert _l079(repo), addr


def test_L079_a_hostname_merely_CONTAINING_invalid_does_not_pass(tmp_path):
    """`.invalid` has to be the TLD. A substring test would accept
    `invalid.example.com`, which resolves perfectly well."""
    repo = _sl_gate(tmp_path, "        SERVERLESS=true\n"
                              "        REPORT_ADDR=https://invalid.example.com\n")
    assert _l079(repo)


def test_L079_an_inferred_cell_is_in_scope_too(tmp_path):
    """A detection cell turns the worker on without SERVERLESS=true, so keying on that
    literal alone would exempt exactly the cells added for ADR 0034."""
    repo = _sl_gate(tmp_path, "        MASTER_TOKEN=sentinel\n"
                              "        REPORT_ADDR=https://run.vast.ai\n", job="qa-serverless-detect")
    assert _l079(repo)


def test_L079_a_non_serverless_cell_owes_nothing(tmp_path):
    """pyworker.sh exits early unless serverless, so a standard cell posts nothing and
    must not be asked to declare an address it has no use for."""
    repo = _sl_gate(tmp_path, "        INSTANCE_TEST_REQUIRE_PASS=base/60-gpu-cuda\n", job="qa")
    assert not _l079(repo)


def test_L079_serverless_false_is_not_serverless(tmp_path):
    repo = _sl_gate(tmp_path, "        SERVERLESS=false\n")
    assert not _l079(repo)


def test_L079_the_real_repo_reaches_no_live_autoscaler():
    """Round-trip over the real workflows — the assertion that failed on two cells
    before the fix."""
    repo = find_repo_root(Path(__file__).resolve().parent)
    bad = _l079(repo)
    assert not bad, f"serverless cells that can reach a live endpoint: {[(f.path, f.msg) for f in bad]}"


# ---- L080: UNSECURED is QA scaffolding and must not live in a template ----------
#
# It disables the pubkey gate AND __check_signature, which then returns True for every
# inbound request without verifying it. The QA cells set it deliberately, and only
# because they also set an unresolvable sentinel REPORT_ADDR (L079) that makes the
# pubkey gate impossible to pass — masking a gate that cannot succeed hides nothing.
# A template has no sentinel and is what a production template gets copied from.


def _tpl(tmp_path, body):
    d = tmp_path / "img" / "templates" / "qa"
    d.mkdir(parents=True, exist_ok=True)
    (d / "template.yml").write_text(body)
    return tmp_path


def _l080(repo):
    return [f for f in L.lint_repo(repo) if f.code == "L080" and f.severity == L.ERROR]


def test_L080_a_template_declaring_unsecured_fires(tmp_path):
    """THE mutation: the flag escapes from a gate into a launchable artefact."""
    repo = _tpl(tmp_path, "name: QA\nimage: vastai/x\nenv:\n  UNSECURED: \"true\"\n")
    assert _l080(repo)


def test_L080_fires_however_it_is_spelled(tmp_path):
    """Quoted, unquoted, list-item, extra indentation — the danger is the declaration,
    not its YAML styling."""
    for body in ('env:\n  UNSECURED: "true"\n',
                 "env:\n  UNSECURED: true\n",
                 'env:\n  "UNSECURED": "true"\n',
                 "env:\n    UNSECURED: 1\n",
                 "env:\n  - UNSECURED=true\n"):
        repo = _tpl(tmp_path, "name: QA\nimage: vastai/x\n" + body)
        assert _l080(repo), body


def test_L080_a_commented_mention_does_not_fire(tmp_path):
    """A template may legitimately EXPLAIN why it does not set the flag. Firing on the
    prose that documents the rule is the trap L076 and L078 both had to close."""
    repo = _tpl(tmp_path, "name: QA\nimage: vastai/x\nenv:\n"
                          "  # UNSECURED is deliberately NOT set here — it is QA-only (L080)\n"
                          "  JUPYTER_DIR: \"/\"\n")
    assert not _l080(repo)


def test_L080_a_similarly_named_key_does_not_fire(tmp_path):
    """Anchored to the whole key. `UNSECURED_PORTS` is a different variable."""
    repo = _tpl(tmp_path, "name: QA\nimage: vastai/x\nenv:\n  UNSECURED_PORTS: \"1\"\n")
    assert not _l080(repo)


def test_L080_the_real_repo_ships_no_template_with_it():
    """Round-trip: the flag is in the gates' extra_env and nowhere a customer can reach."""
    repo = find_repo_root(Path(__file__).resolve().parent)
    bad = _l080(repo)
    assert not bad, f"templates declaring UNSECURED: {[f.path for f in bad]}"


# ---- L056 (amended) + L081: the studio's CUDA backend must be USABLE, not just present ----
#
# Existence was a sufficient guard only while the binary was COMPILED here: a CPU-only
# fallback then produced no libggml-cuda.so at all (ADR 0016). Once it arrives as a
# prebuilt bundle (ADR 0018), `test -f` is satisfied by `tar x`. GGML_BACKEND_DL=ON means
# an unresolvable backend is skipped silently and every inference runs on CPU — the same
# defect, now expressible with the file present.

_STUDIO_DF = VALID_DF.replace(
    "RUN torch_versions_pre=$(pip list); \\",
    "RUN unsloth studio setup && \\\n    torch_versions_pre=$(pip list); \\")


def _studio(tmp_path, asserts: str):
    df = _STUDIO_DF.replace("uv pip install foo;", f"uv pip install foo; {asserts}")
    return make(tmp_path, df=df)


_EXISTS = 'test -f /opt/llama-cpp/build/bin/libggml-cuda.so || exit 1;'
_LDD = ('ldd -r /opt/llama-cpp/build/bin/libggml-cuda.so > /tmp/l.ldd 2>&1; '
        'grep -qE "not found|undefined symbol" /tmp/l.ldd && exit 1;')
_SASS = ('_elf="$(cuobjdump --list-elf /opt/llama-cpp/build/bin/libggml-cuda.so)"; '
         'for _a in sm_75 sm_120; do [[ "$_elf" == *"$_a"* ]] || exit 1; done;')


def test_L056_existence_without_ldd_fires(tmp_path):
    """THE mutation for the prebuilt era: the file is there because tar put it there."""
    img = _studio(tmp_path, _EXISTS + _SASS)
    assert has(img, tmp_path, "L056", "never runs `ldd -r`")


def test_L056_ldd_whose_output_is_never_read_does_not_satisfy_it(tmp_path):
    """`ldd -r` EXITS 0 while printing `undefined symbol`. Running it and discarding the
    result is decoration — the same shape as `file … || true` in an earlier rule."""
    img = _studio(tmp_path, _EXISTS + 'ldd -r /opt/llama-cpp/build/bin/libggml-cuda.so;' + _SASS)
    assert has(img, tmp_path, "L056", "never inspects the output")


def test_L056_existence_is_still_required(tmp_path):
    """The two halves fail differently: a missing file means the install did not happen,
    an unresolved one means it happened against the wrong CUDA. Both stay required."""
    img = _studio(tmp_path, _LDD + _SASS)
    assert has(img, tmp_path, "L056", "existence assertion")


def test_L056_both_halves_present_is_clean(tmp_path):
    img = _studio(tmp_path, _EXISTS + _LDD + _SASS)
    assert "L056" not in errs(img, tmp_path)


def test_L081_no_sass_check_fires(tmp_path):
    """A resolvable backend with no cubin for an admitted GPU CRASHES (no-kernel-image);
    it does not fall back, so neither L056 half can see it."""
    img = _studio(tmp_path, _EXISTS + _LDD)
    assert has(img, tmp_path, "L081", "never checks the SASS arch coverage")


def test_L081_a_single_arch_does_not_satisfy_it(tmp_path):
    """A build can satisfy one end of the admitted range and miss the other, so the
    BRACKET is the requirement — floor and ceiling, not one arch."""
    one = ('_elf="$(cuobjdump --list-elf /opt/llama-cpp/build/bin/libggml-cuda.so)"; '
           '[[ "$_elf" == *"sm_120"* ]] || exit 1;')
    img = _studio(tmp_path, _EXISTS + _LDD + one)
    assert has(img, tmp_path, "L081", "fewer than two literal sm_NN")


def test_L081_reading_the_manifest_instead_of_the_artifact_does_not_satisfy_it(tmp_path):
    """Measured on a real release: the bundle's own manifest claimed an sm_103 the
    binary does not contain. The rule requires cuobjdump ON the .so."""
    manifest = ('python3 -c "import json;print(json.load(open(\'/opt/llama-cpp/UNSLOTH_PREBUILT_INFO.json\'))"'
                '; grep -q sm_75 /opt/llama-cpp/UNSLOTH_PREBUILT_INFO.json && grep -q sm_120 /opt/llama-cpp/UNSLOTH_PREBUILT_INFO.json;')
    img = _studio(tmp_path, _EXISTS + _LDD + manifest)
    assert has(img, tmp_path, "L081", "never checks the SASS arch coverage")


def test_L056_and_L081_do_not_fire_on_an_image_without_the_studio(tmp_path):
    """Scoped to `unsloth studio setup`. Every other image owes nothing here."""
    img = make(tmp_path)
    assert "L056" not in errs(img, tmp_path)
    assert "L081" not in errs(img, tmp_path)


def test_the_real_studio_images_assert_a_usable_backend():
    """Round-trip over the REAL images: both studio images must satisfy both rules."""
    repo = find_repo_root(Path(__file__).resolve().parent)
    seen = []
    for img in discover(repo):
        if "unsloth studio setup" not in L.code_text(parse(img.text)):
            continue
        seen.append(img.name)
        bad = [f.msg for f in lint_image(img, repo) if f.code in ("L056", "L081")]
        assert not bad, f"{img.name}: {bad}"
    assert sorted(seen) == ["aio-studio", "unsloth-studio"], seen


# ---- L082: a bind verdict must read ss's LOCAL column, not the whole line ----
#
# `ss -tln` prints State Recv-Q Send-Q Local:Port Peer:Port, and for a LISTENING socket
# the peer column is always 0.0.0.0:* — so a whole-line match for a wildcard address is
# true for every listener ever printed. Measured live: `LISTEN 0 2048 127.0.0.1:18888
# 0.0.0.0:*`, a correct loopback bind, reported as PUBLIC, failed the cell, and burned
# two host redraws reproducing a test bug on fresh hardware.


def _shipped_test(tmp_path, body, name="app.d/50-bind.sh"):
    f = tmp_path / "ROOT/opt/instance-tools/tests" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("#!/bin/bash\n" + body)
    f.chmod(0o755)
    return tmp_path


def _l082(repo):
    return [f for f in L.lint_repo(repo) if f.code == "L082" and f.severity == L.ERROR]


def test_L082_whole_line_wildcard_match_fires(tmp_path):
    """THE mutation, and the exact line that shipped."""
    repo = _shipped_test(tmp_path, 'ss -tln | grep ":8080 " | grep -q "0.0.0.0:" && echo public\n')
    assert _l082(repo)


def test_L082_the_ss_filter_form_fires_too(tmp_path):
    """`ss -tlnH "sport = :8080"` narrows the ROWS but not the COLUMNS — the peer field
    is still on every line, so the whole-line match is just as wrong."""
    repo = _shipped_test(tmp_path, 'ss -tlnH "sport = :8080" | grep -qE \'0\\.0\\.0\\.0:|\\[::\\]:\'\n')
    assert _l082(repo)


def test_L082_a_hand_rolled_field_4_read_is_no_longer_accepted(tmp_path):
    """This USED to be the blessed form, and that was the hole: a bare `$4` counted as
    proof of correctness, so 67-service-functionality's awk — whose program had been
    destroyed by shell quoting into a constant-true pattern — passed lint while
    reporting the first listener on the box. Only the shared helpers count now."""
    repo = _shipped_test(tmp_path, 'a=$(ss -tlnH | awk \'{print $4}\'); [[ "$a" =~ ^0\\.0\\.0\\.0: ]]\n')
    assert _l082(repo)


def test_L082_the_shared_helper_is_clean(tmp_path):
    """The blessed form: lib.sh's listener_is_public already reads field 4."""
    repo = _shipped_test(tmp_path, 'listener_is_public 8080 && fail_later x "public"\n')
    assert not _l082(repo)


def test_L082_matching_a_PORT_is_unaffected(tmp_path):
    """Scoped to ADDRESS matching. Finding a port or a pid in an ss line is unambiguous
    across columns and must not be caught — over-firing here would push authors toward
    awk for cases that never needed it."""
    for body in ('ss -tln | grep -q ":8080 " && echo up\n',
                 'ss -tlnpH | grep -oP "pid=\\\\K[0-9]+" | head -1\n'):
        repo = _shipped_test(tmp_path, body)
        assert not _l082(repo), body


def test_L082_a_comment_quoting_the_broken_form_does_not_fire(tmp_path):
    """The files that DOCUMENT this trap quote the broken line verbatim — base/65 and
    lib.sh both do. Firing on the explanation is the trap one level up, and this repo
    has hit it before (L076, L078)."""
    repo = _shipped_test(tmp_path,
                         '# WRONG: ss -tln | grep -q "0.0.0.0:" matches the peer column\n'
                         'listener_is_public 8080\n')
    assert not _l082(repo)


def test_L082_the_real_repo_reads_the_local_field_everywhere():
    """Round-trip: the assertion that was red on two shipped files before the fix — one
    that always failed, one whose WARN could never fire."""
    repo = find_repo_root(Path(__file__).resolve().parent)
    bad = _l082(repo)
    assert not bad, f"bind checks matching the whole ss line: {[f.path for f in bad]}"


# ---- L083-L086 + tightened L082: codified from the 2026-08-28 instance-test audit ----


def _t(tmp_path, body, name="app.d/50-x.sh"):
    f = tmp_path / "ROOT/opt/instance-tools/tests" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("#!/bin/bash\n" + body)
    f.chmod(0o755)
    return tmp_path


def _codes(repo, code):
    return [f for f in L.lint_repo(repo) if f.code == code and f.severity == L.ERROR]


def test_L083_a_third_argument_fires(tmp_path):
    """lib.sh takes LABEL and MSG. The third is dropped and the prose fragment becomes
    the FAILURES label, so report_failures emits a sentence where a label belongs."""
    repo = _t(tmp_path, 'fail_later "a" "b" "c"\n')
    assert _codes(repo, "L083")


def test_L083_counts_across_line_continuations(tmp_path):
    """The real instances split prose across continuations — that is HOW the third
    argument gets written."""
    repo = _t(tmp_path, 'fail_later "label" \\\n    "part one" \\\n    "part two"\n')
    assert _codes(repo, "L083")


def test_L083_a_command_substitution_is_not_an_argument(tmp_path):
    """THE false positive this rule shipped with for one lint run: quotes inside
    `$(echo "$x" | tail -3)` are nested shell, not argument boundaries. A linter that
    reds a correct call is worse than no linter."""
    repo = _t(tmp_path, 'fail_later "lbl" "failed: $(echo "$out" | tail -3)"\n')
    assert not _codes(repo, "L083")


def test_L084_curl_status_fallback_fires(tmp_path):
    """curl writes the -w template THEN exits non-zero, so `|| echo 000` appends and the
    capture becomes 000000 — matching no arm the author wrote."""
    repo = _t(tmp_path, "code=$(curl -s -o /dev/null -w '%{http_code}' http://x/ || echo 000)\n")
    assert _codes(repo, "L084")


def test_L084_the_plain_capture_is_clean(tmp_path):
    repo = _t(tmp_path, "code=$(curl -s -o /dev/null -w '%{http_code}' http://x/ 2>/dev/null)\n")
    assert not _codes(repo, "L084")


def test_L085_a_wait_longer_than_the_files_own_budget_fires(tmp_path):
    """runner.sh execs under `timeout $TEST_TIMEOUT`, so the wait can never complete —
    it is killed and reported as a bare timeout naming no check."""
    repo = _t(tmp_path, '# TEST_TIMEOUT=1800\nREADY_TIMEOUT="${LLAMA_OFFLOAD_READY_TIMEOUT:-3600}"\n')
    assert _codes(repo, "L085")


def test_L085_a_wait_inside_the_budget_is_clean(tmp_path):
    repo = _t(tmp_path, '# TEST_TIMEOUT=1800\nREADY_TIMEOUT="${LLAMA_OFFLOAD_READY_TIMEOUT:-900}"\n')
    assert not _codes(repo, "L085")


def test_L086_running_plus_port_as_a_guard_fires(tmp_path):
    """The false-green that made a hung jupyter report ALL TESTS PASSED."""
    repo = _t(tmp_path, 'if service_running jupyter && wait_for_port 18080 5; then\n  :\nfi\n')
    assert _codes(repo, "L086")


def test_L086_assert_service_serving_is_clean(tmp_path):
    repo = _t(tmp_path, 'assert_service_serving jupyter 18080\n')
    assert not _codes(repo, "L086")


def test_L082_no_longer_accepts_a_bare_field_reference(tmp_path):
    """The tightening the audit asked for. A bare `$4` was accepted as proof, which is
    exactly how 67-service-functionality's destroyed awk passed lint while reporting the
    first listener on the box."""
    repo = _t(tmp_path, '''owner=$(ss -tlnpH | awk -v p=":8080$" '"'"'$4 ~ p'"'"' | head -1)
ss -tln | grep -q "0.0.0.0:" && echo public
''')
    assert _codes(repo, "L082")


def test_L082_the_shared_helpers_are_still_accepted(tmp_path):
    repo = _t(tmp_path, 'listener_is_public 8080 && echo pub\nlistener_owner 8080\n')
    assert not _codes(repo, "L082")


def test_the_real_repo_satisfies_the_audit_rules():
    """Round-trip: every rule codified from the audit holds across the shipped tests."""
    repo = find_repo_root(Path(__file__).resolve().parent)
    bad = [f for f in L.lint_repo(repo)
           if f.code in ("L082", "L083", "L084", "L085", "L086") and f.severity == L.ERROR]
    assert not bad, f"audit rules failing: {[(f.code, f.path) for f in bad]}"


# ---- the corrections the pre-build review forced (2026-08-28) ----


def test_L084_fires_on_the_two_line_form_it_was_written_from(tmp_path):
    """The rule shipped INERT against its own motivating defect: the capture and the
    `|| echo` sit either side of a line continuation, and a per-line scan could not see
    it. Worse than absent — it looked like coverage."""
    repo = _t(tmp_path, '''_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \\
        "http://127.0.0.1:${PORT}/" || echo 000)
''')
    assert _codes(repo, "L084")


def test_L084_does_not_fire_when_the_fallback_belongs_to_a_later_statement(tmp_path):
    """The same rule ALSO fired on correct code: a `|| echo WARN` after a separate
    statement is not a status fallback. Both directions wrong at once."""
    for body in ('code=$(curl -s -w \'%{http_code}\' http://x/); [[ -n "$code" ]] || echo WARN\n',
                 'c=$(curl -s -o /dev/null -w \'%{http_code}\' http://x/) ; [[ "$c" == 200 ]] || echo "  WARN: bad"\n'):
        repo = _t(tmp_path, body)
        assert not _codes(repo, "L084"), body


def test_L083_stops_counting_at_a_statement_boundary(tmp_path):
    """`fail_later "a" "b"; echo "c"` is a correct two-argument call followed by an echo.
    Counting past the `;` reported it as three — a false ERROR on correct code."""
    for body in ('fail_later "a" "b"; echo "c"\n', 'fail_later "a" "b" && echo "c"\n'):
        repo = _t(tmp_path, body)
        assert not _codes(repo, "L083"), body


def test_L083_counts_single_quoted_arguments(tmp_path):
    """An earlier counter saw only double quotes and returned 0 for a single-quoted
    call — silently exempting a whole spelling."""
    repo = _t(tmp_path, "fail_later 'a' 'b' 'c'\n")
    assert _codes(repo, "L083")


def test_L083_a_substitution_inside_a_quoted_argument_is_not_an_argument(tmp_path):
    """`$( )` is still a substitution INSIDE double quotes — that is the point of
    `"msg: $(echo "$x")"`. Treating its inner quotes as boundaries made a correct call
    read as three arguments."""
    repo = _t(tmp_path, 'fail_later "lbl" "failed: $(echo "$out" | tail -3)"\n')
    assert not _codes(repo, "L083")


# ---- L087: an upstream image's CUDA label is READ, not inferred (ADR 0035) ----


def _wf(tmp_path, body, name="build-thing.yml"):
    f = tmp_path / ".github/workflows" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body)
    return tmp_path


_RESOLVES_UPSTREAM = """
jobs:
  preflight:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/check-dockerhub-release
        with:
          repository: vendor/thing
"""


def test_L087_a_hardcoded_upstream_cuda_label_fires(tmp_path):
    """The real defect: build-vllm-omni.yml pinned its nightly matrix to 12.9 while
    vllm/vllm-omni:nightly reported 13.0.2, so every nightly said cuda-12.9 and
    carried CUDA 13."""
    repo = _wf(tmp_path, _RESOLVES_UPSTREAM + """      - name: Handle nightly build
        run: |
          echo 'matrix=[{"tag":"nightly","cuda":"12.9"}]' >> $GITHUB_OUTPUT
""")
    assert _codes(repo, "L087")


def test_L087_the_suffix_chain_that_dropped_an_image_fires(tmp_path):
    """The sglang release shape. Not a wrong label — the bare tag matched no branch
    and the genuine 12.9 image was dropped with no error anywhere."""
    repo = _wf(tmp_path, _RESOLVES_UPSTREAM + """      - name: Build matrix from variant tags
        run: |
          MATRIX=$(echo "$VARIANTS" | jq -c '
            map(if endswith("-cu130") then {tag: ., cuda: "13.0"}
                elif endswith("-cu129") then {tag: ., cuda: "12.9"}
                else empty end)')
""")
    assert _codes(repo, "L087")


def test_L087_reading_the_artifact_is_clean(tmp_path):
    repo = _wf(tmp_path, _RESOLVES_UPSTREAM + """      - name: Build matrix from variant tags
        run: |
          config=$(docker buildx imagetools inspect --format '{{ json .Image }}' "$ref")
          cuda=$(printf '%s' "$config" | jq -r '.. | .Env? // empty' | grep CUDA_VERSION | cut -d= -f2)
          MATRIX=$(jq -cn --arg c "${cuda%.*}" '[{tag: "x", cuda: $c}]')
""")
    assert not _codes(repo, "L087")


def test_L087_is_PER_STEP_not_per_file(tmp_path):
    """THE miss this rule exists to catch. build-vllm-omni.yml had its release path
    converted to read the artifact and its nightly path left hardcoded, in the same
    file — a file-level check sees CUDA_VERSION present and calls it clean."""
    repo = _wf(tmp_path, _RESOLVES_UPSTREAM + """      - name: Handle nightly build
        run: |
          echo 'matrix=[{"tag":"nightly","cuda":"12.9"}]' >> $GITHUB_OUTPUT
      - name: Build matrix from variant tags
        run: |
          config=$(docker buildx imagetools inspect --format '{{ json .Image }}' "$ref")
          cuda=$(printf '%s' "$config" | grep CUDA_VERSION | cut -d= -f2)
""")
    assert _codes(repo, "L087")


def test_L087_our_own_base_matrix_is_not_an_upstream_claim(tmp_path):
    """build-comfyui.yml's `{cuda: "12.9", py: "py312"}` selects OUR pytorch base. It
    is a build input we control, not a claim about someone else's artifact, and a rule
    that reds it would be wrong."""
    repo = _wf(tmp_path, """
jobs:
  build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - { cuda: "12.9", py: "py312" }
    steps:
      - run: |
          echo 'matrix=[{"tag":"x","cuda":"12.9"}]'
""", name="build-comfyui.yml")
    assert not _codes(repo, "L087")


# ---- L088: a test ships the sibling helper it reaches for ----


def _suite(tmp_path, files: dict, suite="engine.d"):
    d = tmp_path / "ROOT/opt/instance-tools/tests" / suite
    d.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        f = d / name
        f.write_text(body)
        if name.endswith(".sh"):
            f.chmod(0o755)
    return tmp_path


_REACHES = '#!/bin/bash\nCHECKER="$(dirname "$0")/contract_check.py"\n'


def test_L088_a_missing_sibling_helper_fires(tmp_path):
    """THE real defect, 2026-09-02: the vllm-omni gate was built by copying two .sh
    files out of vllm.d and shipped without the 811-line contract_check.py beside
    them. The suite failed correctly on a rented GPU — after a full image build."""
    repo = _suite(tmp_path, {"12-engine-contract.sh": _REACHES})
    assert _codes(repo, "L088")


def test_L088_shipping_the_helper_is_clean(tmp_path):
    repo = _suite(tmp_path, {"12-engine-contract.sh": _REACHES,
                             "contract_check.py": "# assertions\n"})
    assert not _codes(repo, "L088")


def test_L088_does_not_sweep_in_the_ubiquitous_lib_source(tmp_path):
    """Every test in the tree opens with `source "$(dirname "$0")/../lib.sh"`. That is
    a path segment, not a sibling, and a rule that fired on it would red the repo."""
    repo = _suite(tmp_path, {"10-x.sh": '#!/bin/bash\nsource "$(dirname "$0")/../lib.sh"\n'})
    assert not _codes(repo, "L088")


def test_own_test_prefixes_sees_every_overlay_not_just_ROOT(tmp_path):
    """aio-studio is a two-STAGE build: Dockerfile.base copies ROOT_BASE (the cached
    base layer) and Dockerfile copies ROOT on top, so a suite in ROOT_BASE ships in BOTH
    images. Scanning ROOT alone made L072 report that the base's QA template named no
    own-suite test while it was naming two of them."""
    d = tmp_path / "img"
    for overlay, suite in (("ROOT", "app.d"), ("ROOT_BASE", "base-layer.d")):
        sub = d / overlay / "opt/instance-tools/tests" / suite
        sub.mkdir(parents=True)
        (sub / "10-x.sh").write_text("#!/bin/bash\n")
    (d / "Dockerfile").write_text("FROM scratch\n")
    img = Image(name="img", cls="pytorch-nested", dir=d,
                dockerfile=d / "Dockerfile", text="FROM scratch\n",
                root=d / "ROOT")
    assert L._own_test_prefixes(img) == ["app.d", "base-layer.d"]


# ---- L089: a vendored Python entrypoint is EXECUTED, not just tested for presence ----


# The two images run the converter with DIFFERENT interpreters, because the studio
# resolves python through .../studio/unsloth_studio: /venv/main in unsloth-studio,
# /venv/unsloth in aio-studio. A mutation that hardcoded one would silently no-op on
# the other and assert nothing, so strip whatever interpreter invocation is there.
_EXEC_RE = re.compile(r"\S*python\S*\s+\S*convert_hf_to_gguf\.py[^\n]*")


@pytest.mark.parametrize("name", ["unsloth-studio", "aio-studio"])
def test_L089_real_studio_images_execute_their_converter(name):
    """Both shipping images vendor convert_hf_to_gguf.py from the pinned llama.cpp
    source tag and run it at build time, so L089 must not fire on them."""
    repo, img = _real(name)
    assert "convert_hf_to_gguf.py" in img.text
    assert "L089" not in errs(img, repo)


@pytest.mark.parametrize("name", ["unsloth-studio", "aio-studio"])
def test_mut_converter_is_fetched_but_never_executed(name):
    """THE real defect, 2026-09-04. Drop the execution and leave the fetch and the
    `test -f` guards standing — exactly the shape that built green, passed QA and was
    promoted, then failed on a rented GPU with `ModuleNotFoundError: No module named
    'conversion'` because upstream had refactored the script into a wrapper over an
    89-module sibling package the build never fetched."""
    repo, img = _real(name)
    assert _EXEC_RE.search(img.text), "real image does not execute its converter"
    mut = replace(img, text=_EXEC_RE.sub(
        "test -f /opt/llama-cpp/convert_hf_to_gguf.py", img.text))
    assert not _EXEC_RE.search(mut.text), "mutation did not apply"
    assert "L089" in errs(mut, repo)


def test_L089_importing_a_sibling_does_not_substitute_for_running_the_script():
    """The assertion this rule replaces probed `import gguf` — the sibling the author
    had in mind — and passed while the converter's own first import was unsatisfiable.
    Restoring that probe must NOT satisfy L089: only executing the file proves the
    closure, because only the file knows what it imports."""
    repo, img = _real("unsloth-studio")
    mut = replace(img, text=_EXEC_RE.sub(
        'PYTHONPATH=/opt/llama-cpp/gguf-py /venv/main/bin/python -c "import gguf"', img.text))
    assert "L089" in errs(mut, repo)


def test_L089_an_image_that_vendors_no_script_is_out_of_scope(tmp_path):
    """The rule keys off a fetched .py. An image that fetches none must stay clean."""
    assert "L089" not in errs(make(tmp_path), tmp_path)


def test_L089_matches_the_output_path_not_the_url():
    """`wget -qO <dest>.py <url>.py` — a rule that keyed off the URL would read the
    remote name and, for a fetch whose URL basename differs from the destination,
    look for the execution of a file the image does not have."""
    repo, img = _real("unsloth-studio")
    assert 'wget -qO /opt/llama-cpp/convert_hf_to_gguf.py' in img.text
    hits = L._FETCHED_PY.findall(L.code_text(L.parse(img.text)))
    assert hits == ["/opt/llama-cpp/convert_hf_to_gguf.py"], hits


@pytest.mark.parametrize("name", ["unsloth-studio", "aio-studio"])
def test_L089_converter_is_executed_by_the_interpreter_the_exporter_uses(name):
    """Running the converter proves nothing unless it runs under the venv the STUDIO
    resolves python through — the exporter invokes
    `.../studio/unsloth_studio/bin/python`, and that symlink is /venv/main in
    unsloth-studio but /venv/unsloth in aio-studio.

    Caught during the L089 fix: the assertion was first written against /venv/main for
    both. In aio-studio that venv is merely the BASE unsloth is built from, so the probe
    would have passed against an environment the exporter never uses while the real one
    stayed unverified — a false green of exactly the kind L089 exists to prevent. It
    would also have run before /venv/unsloth was created at all."""
    _, img = _real(name)
    m = re.search(r"ln -sf (/venv/\S+) /opt/workspace-internal/unsloth/studio/unsloth_studio",
                  img.text)
    assert m, "no studio venv symlink found"
    studio_venv = m.group(1)
    ex = _EXEC_RE.search(img.text)
    assert ex, "converter is never executed"
    assert ex.group(0).startswith(f"{studio_venv}/bin/python"), (
        f"{name}: converter executed by {ex.group(0).split()[0]} but the exporter "
        f"resolves python through {studio_venv}")


@pytest.mark.parametrize("name", ["unsloth-studio", "aio-studio"])
def test_L089_converter_runs_after_the_venv_it_needs_is_complete(name):
    """The converter's import closure reaches `transformers` THROUGH `conversion/`, and
    transformers arrives with unsloth. Executing it before that install fails on an
    environment that is merely unfinished rather than broken.

    Measured 2026-09-04: the first fix ran the assertion inside the llama.cpp stage,
    which precedes the unsloth install in unsloth-studio, and the build failed with
    `ModuleNotFoundError: No module named 'transformers'`. The failure direction was
    safe — a red build, not a silent pass — but the assertion has to sit where the venv
    is complete or it can never go green. aio-studio was already correct because its
    assertion was placed beside /venv/unsloth, which only exists after the install."""
    _, img = _real(name)
    install = re.search(r"uv pip install[^\n]*unsloth", img.text)
    assert install, "no unsloth install found"
    ex = _EXEC_RE.search(img.text)
    assert ex, "converter is never executed"
    assert install.start() < ex.start(), (
        f"{name}: the converter is executed at offset {ex.start()} but unsloth (and with "
        f"it transformers) is not installed until {install.start()} — the closure is "
        f"incomplete at that point and the build cannot pass")
