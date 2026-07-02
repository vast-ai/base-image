"""Round-trip: the generator's output must pass the linter, for every class.

This closes the loop — the trustworthy oracle (the linter, with its mutation
tests) validates the generator. Run via pytest or the stdlib fallback below.
"""
import re
from pathlib import Path

from imagegen.generate import generate, CLASSES
from imagegen.discover import discover
from imagegen.linter import lint_image, ERROR


def _gen_repo(repo: Path):
    for d in ("external", "derivatives/pytorch/derivatives", "derivatives", ".github/workflows"):
        (repo / d).mkdir(parents=True, exist_ok=True)
    generate(repo, name="mytool", cls="derivative", label="My Tool", port=7860)
    generate(repo, name="myapp", cls="pytorch-nested", label="My App", port=7861)
    generate(repo, name="myext", cls="external", label="My Ext", port=7862,
             upstream="someupstream/img:1.0")


def test_generated_images_structurally_valid_but_flagged_skeleton(tmp_path):
    """Generated output passes every STRUCTURAL check, and L040 flags it as an
    incomplete skeleton — so it is never mistaken for a complete image."""
    _gen_repo(tmp_path)
    imgs = {i.name: i for i in discover(tmp_path)}
    assert set(imgs) == {"mytool", "myapp", "myext"}
    for name, img in imgs.items():
        errors = [f for f in lint_image(img, tmp_path) if f.severity == ERROR]
        structural = [(f.code, f.msg) for f in errors if f.code != "L040"]
        assert not structural, f"{img.cls}/{name} structural errors: {structural}"
        assert any(f.code == "L040" for f in errors), f"{name}: skeleton not flagged by L040"


def test_filled_image_lints_clean(tmp_path):
    """Once the markers are resolved, the image lints fully clean (no L040)."""
    _gen_repo(tmp_path)
    repo = tmp_path
    # resolve every skeleton marker across the pytorch-nested image's files
    img_dir = repo / "derivatives/pytorch/derivatives/myapp"
    for p in list(img_dir.rglob("*")) + [repo / ".github/workflows/build-myapp.yml"]:
        if p.is_file():
            # simulate a good-faith fill: REMOVE stub lines (incl. the `exit 1` stub),
            # resolve CHANGE* tokens. No `>>> FILL` may survive.
            kept = [ln for ln in p.read_text().splitlines(keepends=True) if ">>> FILL" not in ln]
            t = "".join(kept).replace("CHANGEME", "1.0.0").replace("CHANGEPORT", "7861")
            p.write_text(t)
    img = next(i for i in discover(repo) if i.name == "myapp")
    errors = [(f.code, f.msg) for f in lint_image(img, repo) if f.severity == ERROR]
    assert not errors, f"filled image should lint clean, got: {errors}"


def test_generated_classes_are_correct(tmp_path):
    _gen_repo(tmp_path)
    imgs = {i.name: i for i in discover(tmp_path)}
    assert imgs["mytool"].cls == "derivative"
    assert imgs["myapp"].cls == "pytorch-nested"
    assert imgs["myext"].cls == "external"


def test_external_requires_upstream(tmp_path):
    (tmp_path / "external").mkdir(parents=True, exist_ok=True)
    try:
        generate(tmp_path, name="x", cls="external", label="X", port=8000)
    except ValueError:
        return
    raise AssertionError("external without upstream should raise ValueError")


def test_upstream_only_for_external(tmp_path):
    """Class-sanity (ADR cond #3): --upstream on a non-external class is rejected."""
    (tmp_path / "derivatives/pytorch/derivatives").mkdir(parents=True, exist_ok=True)
    try:
        generate(tmp_path, name="x", cls="pytorch-nested", label="X", port=8000, upstream="foo/bar:1")
    except ValueError:
        return
    raise AssertionError("--upstream on non-external should raise ValueError")


def test_fill_markers_present(tmp_path):
    """The judgment residue must be clearly fenced for the human/LLM to complete."""
    _gen_repo(tmp_path)
    for name, cls in (("mytool", "derivatives"), ("myapp", "derivatives/pytorch/derivatives"),
                      ("myext", "external")):
        df = (tmp_path / cls / name / "Dockerfile").read_text()
        assert ">>> FILL:" in df, f"{name} Dockerfile missing a FILL marker"


def test_l040_flags_all_placeholder_files(tmp_path):
    """Every generated file with a placeholder — incl. the agent doc, both READMEs,
    and the launch-stub line — must be L040-flagged (no single-> / FIXME gaps)."""
    _gen_repo(tmp_path)
    img = next(i for i in discover(tmp_path) if i.name == "myext")
    flagged = {f.path for f in lint_image(img, tmp_path) if f.code == "L040"}
    assert any("vast_agents" in p for p in flagged), "agent doc not flagged"
    assert "README.md" in flagged and "README.template.md" in flagged, "READMEs not flagged"
    assert any("supervisor-scripts" in p for p in flagged), "launch stub not flagged"


def test_no_single_marker_dialect_leaks(tmp_path):
    """All placeholders use the `>>> FILL` dialect L040 knows — no bare `> FILL`/`FIXME:`."""
    _gen_repo(tmp_path)
    for p in (tmp_path / "external/myext").rglob("*"):
        if p.is_file():
            t = p.read_text()
            assert "> FILL" not in t.replace(">>> FILL", ""), f"{p.name}: stray single-> FILL marker"
            assert "FIXME" not in t, f"{p.name}: stray FIXME marker L040 won't catch"


def test_pytorch_has_ref_guard_and_install_between_snapshots(tmp_path):
    """The pytorch Dockerfile must guard the ref AND place the install marker between
    the pre/post snapshots (so a faithful fill keeps the drift guard meaningful)."""
    _gen_repo(tmp_path)
    df = (tmp_path / "derivatives/pytorch/derivatives/myapp/Dockerfile").read_text()
    assert re.search(r'\[\[ -n "\$\{MYAPP_REF\}" \]\] \|\|', df), "missing ref-presence guard"
    pre = df.index("torch_versions_pre=")
    post = df.index("torch_versions_post=")
    fill = df.index(">>> FILL: install myapp")
    assert pre < fill < post, "install marker must sit between the pre/post snapshots"


def test_external_has_shell_and_env(tmp_path):
    """External must declare bash SHELL (it uses bashisms) and the convert ENV block."""
    _gen_repo(tmp_path)
    df = (tmp_path / "external/myext/Dockerfile").read_text()
    assert 'SHELL ["/bin/bash", "-c"]' in df
    assert "UV_LINK_MODE=copy" in df and "PIP_BREAK_SYSTEM_PACKAGES=1" in df


def test_supervisor_sources_exit_portal_with_label(tmp_path):
    """Correctness (not just lint): exit_portal.sh must be SOURCED WITH the label arg,
    and there must be no bogus `exit_portal "..."` function call (the round-4 fatal bug)."""
    _gen_repo(tmp_path)
    sh = (tmp_path / "derivatives/pytorch/derivatives/myapp/ROOT/opt/supervisor-scripts/myapp.sh").read_text()
    assert '. "${utils}/exit_portal.sh" "My App"' in sh
    assert not re.search(r'^\s*exit_portal\s+"', sh, re.M), "bogus exit_portal function call present"


def test_readmes_are_distinct(tmp_path):
    _gen_repo(tmp_path)
    d = tmp_path / "external/myext"
    assert (d / "README.md").read_text() != (d / "README.template.md").read_text()
    assert "Create an Instance" in (d / "README.template.md").read_text()


def test_capability_yaml_has_image_mapping(tmp_path):
    _gen_repo(tmp_path)
    cap = (tmp_path / "derivatives/pytorch/derivatives/myapp/ROOT/etc/vast_capabilities.d/50-myapp.yaml").read_text()
    assert re.search(r"^image:\s*$", cap, re.M) and "name:" in cap


def test_no_baked_false_conventions(tmp_path):
    """Generator must not hardcode +10000 ports or a uniform cron (invariants §3)."""
    _gen_repo(tmp_path)
    env = (tmp_path / "external/myext/ROOT/etc/vast_boot.d/05-myext-env.sh").read_text()
    assert "17862" not in env  # no baked internal+10000
    wf = (tmp_path / ".github/workflows/build-myapp.yml").read_text()
    assert "0 0,12 * * *" not in wf  # no baked uniform cron


def test_workflow_scaffold_is_full_pipeline(tmp_path):
    """The scaffolded build-<name>.yml is the full 5-job pipeline with the DockerHub
    secret-refs, the prod approval gate, and notify wired — not a bare stub."""
    _gen_repo(tmp_path)
    for name in ("mytool", "myapp", "myext"):
        wf = (tmp_path / ".github/workflows" / f"build-{name}.yml").read_text()
        for job in ("preflight:", "build:", "merge-manifests:", "collect-tags:", "notify:"):
            assert f"\n  {job}" in wf, f"{name}: missing job {job}"
        # secret-refs, never a literal namespace (L041-clean by construction)
        assert "${{ secrets.DOCKERHUB_NAMESPACE_STAGING }}" in wf
        assert "${{ secrets.DOCKERHUB_NAMESPACE }}" in wf
        assert "'production'" in wf                              # prod approval gate
        assert f'DEFAULT_DOCKERHUB_REPO: "{name}"' in wf         # repo defaults to image name
        assert "./.github/workflows/notify-slack.yml" in wf     # notify wired


def test_workflow_context_matches_class_dir(tmp_path):
    _gen_repo(tmp_path)
    for name, ctx in (("mytool", "derivatives/mytool"),
                      ("myapp", "derivatives/pytorch/derivatives/myapp"),
                      ("myext", "external/myext")):
        wf = (tmp_path / ".github/workflows" / f"build-{name}.yml").read_text()
        assert f"context: {ctx}" in wf


def test_workflow_scaffold_includes_qa_gate(tmp_path):
    """The scaffold wires the live-GPU QA gate: a qa job calling qa-gate.yml with the
    pre-filled repo/template_dir/label + secrets, and promotion + notify gated on it."""
    _gen_repo(tmp_path)
    for name, ctx in (("mytool", "derivatives/mytool"),
                      ("myapp", "derivatives/pytorch/derivatives/myapp"),
                      ("myext", "external/myext")):
        wf = (tmp_path / ".github/workflows" / f"build-{name}.yml").read_text()
        assert "\n  qa:" in wf                                            # qa job present
        assert "uses: ./.github/workflows/qa-gate.yml" in wf             # calls the gate
        assert f"template_dir: {ctx}/templates/{name}-qa" in wf          # pre-filled dir
        assert f"label: base-image-qa-{name}" in wf                       # pre-filled label
        assert "VAST_API_KEY: ${{ secrets.VAST_API_KEY }}" in wf          # secrets wired
        assert "needs: [preflight, build, qa]" in wf                      # merge gated on qa
        assert "needs: [preflight, build, qa, merge-manifests, collect-tags]" in wf  # notify
        assert "needs.qa.outputs.gated" in wf                             # gated-pass headline


def test_qa_template_scaffolded(tmp_path):
    """A QA template skeleton is scaffolded per image: private, with an L050-satisfying
    numeric compute_cap floor, and L040-flagged as an incomplete skeleton."""
    _gen_repo(tmp_path)
    for name, ctx in (("mytool", "derivatives/mytool"),
                      ("myapp", "derivatives/pytorch/derivatives/myapp"),
                      ("myext", "external/myext")):
        qat = tmp_path / ctx / "templates" / f"{name}-qa" / "template.yml"
        assert qat.is_file(), f"{name}: QA template not scaffolded"
        t = qat.read_text()
        assert "private: true" in t
        assert "compute_cap" in t and "gte: 750" in t                     # L050 floor (numeric)
        assert ">>> FILL" in t or "CHANGEME" in t                         # skeleton marker


def test_workflow_scaffold_is_l041_clean_with_namespace_set(tmp_path, monkeypatch):
    """Even with the staging namespace set, the scaffold trips no L041 — it uses the
    secret-ref, never a literal account name (correct by construction)."""
    monkeypatch.setenv("DOCKERHUB_NAMESPACE_STAGING", "acmestaging")
    _gen_repo(tmp_path)
    for img in discover(tmp_path):
        codes = {f.code for f in lint_image(img, tmp_path) if f.severity == ERROR}
        assert "L041" not in codes, f"{img.name}: scaffold tripped L041"


def test_qa_template_markers_are_linted(tmp_path):
    """The QA template lives OUTSIDE ROOT/ — regression guard that L040 scans it, else a
    scaffolded QA template's CHANGEME/FILL markers slip past the lint gate."""
    _gen_repo(tmp_path)
    img = next(i for i in discover(tmp_path) if i.name == "myapp")
    flagged = {f.path for f in lint_image(img, tmp_path) if f.code == "L040"}
    assert any("templates/myapp-qa/template.yml" in p for p in flagged), \
        f"QA template not L040-scanned; flagged={flagged}"


def test_qa_repo_uses_literal_not_env(tmp_path):
    """`env` is unavailable in a reusable-workflow (`uses: qa-gate.yml`) `with:` block —
    GitHub rejects the whole file — so the qa job's `repo:` must use a LITERAL fallback,
    not env.DEFAULT_DOCKERHUB_REPO. Regression from the QA-CI scaffold (only GitHub's
    workflow validator catches it; YAML parses + lint passes)."""
    _gen_repo(tmp_path)
    for name in ("mytool", "myapp", "myext"):
        wf = (tmp_path / ".github/workflows" / f"build-{name}.yml").read_text()
        assert "repo: ${{ inputs.DOCKERHUB_REPO || env." not in wf, \
            f"{name}: qa `repo:` references env. in a reusable with: (GitHub rejects it)"
        assert ("repo: ${{ inputs.DOCKERHUB_REPO || '" + name + "' }}") in wf, \
            f"{name}: qa `repo:` should fall back to the literal image name"


def test_default_launch_template_scaffolded(tmp_path):
    """Every image gets a public default launch template at templates/default/template.yml —
    the user-facing template, with an L050 floor and an L040-flagged launch spec."""
    _gen_repo(tmp_path)
    for name, ctx in (("mytool", "derivatives/mytool"),
                      ("myapp", "derivatives/pytorch/derivatives/myapp"),
                      ("myext", "external/myext")):
        t = (tmp_path / ctx / "templates" / "default" / "template.yml").read_text()
        assert "private: false" in t and "readme_visible: true" in t  # public launch template
        assert "gte: 750" in t                                         # L050 floor
        assert ">>> FILL" in t                                         # launch-spec skeleton


def test_generator_supervisor_is_executable(tmp_path):
    """L051 regression: the generator must write supervisor launch scripts executable — the
    .conf execs them directly (command=...sh), so 0644 is fatal on launch."""
    _gen_repo(tmp_path)
    for name, ctx in (("mytool", "derivatives/mytool"),
                      ("myapp", "derivatives/pytorch/derivatives/myapp"),
                      ("myext", "external/myext")):
        sh = tmp_path / ctx / "ROOT/opt/supervisor-scripts" / f"{name}.sh"
        assert sh.stat().st_mode & 0o111, f"{name}: supervisor script not executable"


def test_l051_fires_on_non_executable_supervisor(tmp_path):
    """Mutation: strip +x from a generated supervisor script -> L051 fires."""
    _gen_repo(tmp_path)
    sh = tmp_path / "derivatives/pytorch/derivatives/myapp/ROOT/opt/supervisor-scripts/myapp.sh"
    sh.chmod(0o644)
    img = next(i for i in discover(tmp_path) if i.name == "myapp")
    assert "L051" in {f.code for f in lint_image(img, tmp_path)}, "L051 must fire on a non-exec supervisor script"


if __name__ == "__main__":
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
