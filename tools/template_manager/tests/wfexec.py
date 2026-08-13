"""Execute a workflow job's shell against a fake registry (ADR 0019).

Why this exists
---------------
Every safety property of the base promotion gate lives in bash inside a YAML
file. The gate has been disarmed twice, and both times the whole test suite
stayed green, because the tests asserted that a *string* appeared in the file:

  * the hold check was written into the `dry-run` job instead of `promote`;
    a whole-file grep found the stray copy and reported success;
  * scoping that grep to the `promote` job closed relocation and left neutering
    wide open — deleting the single word `continue` from the hold branch flips
    every held tag to its untested digest, and 19/19 tests still pass.

A third defect of the same shape (four assignments deleted from the `dry-run`
loop) shipped in the commit that fixed the first two. The pattern is not
carelessness about any one line; it is that string-matching a YAML file cannot
see broken shell, and all of it is shell.

So: run the shell. The promote dance calls exactly two external commands,
`crane digest` and `crane copy`, over inputs that are plain JSON files. That is
small enough to stub faithfully, which makes the real question answerable —
after this script runs, **what digest does each `cuda-X.Y.Z-auto` tag hold?**

What is and is not covered
--------------------------
This executes the step's `run:` script under `bash -e` with a stub `crane` on
PATH and a JSON file standing in for the registry. It does NOT reproduce
GitHub's expression evaluation, `if:` conditions, the job graph, or artifact
plumbing — those are asserted structurally in test_promote_gate_wiring.py, and
the two layers catch different things. A `${{ }}` expression left inside a
`run:` block is rejected rather than guessed at, because guessing would make a
passing test mean less than it appears to.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
PROMOTE = REPO / ".github/workflows/promote-base-image.yml"
MOVE = REPO / ".github/workflows/move-base-auto-tag.yml"
QA_GATE = REPO / ".github/workflows/qa-gate.yml"

# A stub standing in for `python` inside the QA retry loop. It scripts
# test_template.py's exit codes attempt-by-attempt, and DELEGATES qa_verdict.py
# to the real interpreter — the point of the exercise is whether the loop retries
# what the real classifier calls inconclusive, so faking the classifier too would
# leave the actual coupling untested.
_PYTHON_STUB = r'''#!/usr/bin/env python3
import json, os, subprocess, sys

script = next((a for a in sys.argv[1:] if a.endswith(".py")), "")
rest = sys.argv[sys.argv.index(script) + 1:] if script in sys.argv else []

if script.endswith("qa_verdict.py"):
    sys.exit(subprocess.run([os.environ["REAL_PYTHON"], script, *rest]).returncode)

if script.endswith("test_template.py"):
    plan = json.load(open(os.environ["ATTEMPT_PLAN"]))
    n_file = os.environ["ATTEMPT_COUNT"]
    n = int(open(n_file).read() or "0")
    with open(n_file, "w") as fh:
        fh.write(str(n + 1))
    step = plan[min(n, len(plan) - 1)]
    # --raw goes to stdout, which the loop redirects to /tmp/qa-raw.json
    print(json.dumps(step["raw"]))
    sys.exit(step["code"])

print(f"python stub: unexpected script {script!r}", file=sys.stderr)
sys.exit(97)
'''

# A stub standing in for crane, backed by a JSON file: {"ref": "sha256:..."}.
# It implements only what the scripts under test actually call, and it fails
# loudly on anything else rather than returning a plausible zero — a stub that
# silently succeeds would recreate the exact problem this module exists to fix.
_CRANE_STUB = r'''#!/usr/bin/env python3
import json, os, sys

REG = os.environ["FAKE_REGISTRY"]
LOG = os.environ["CRANE_LOG"]


def load():
    with open(REG) as fh:
        return json.load(fh)


def save(d):
    with open(REG, "w") as fh:
        json.dump(d, fh, indent=2, sort_keys=True)


KNOWN = os.environ["FAKE_DIGESTS"]


def known():
    with open(KNOWN) as fh:
        return set(json.load(fh))


def learn(d):
    s = known() | {d}
    with open(KNOWN, "w") as fh:
        json.dump(sorted(s), fh)


def resolve(reg, ref):
    """A ref is repo:tag or repo@sha256:...

    A digest ref resolves as long as the manifest EXISTS, which is not the same
    as some tag currently pointing at it — Phase A of the dance deliberately moves
    a tag off a digest and Phase B then copies that digest back. Modelling
    "digest exists iff a tag points at it" made the real script look broken.
    """
    if "@" in ref:
        digest = ref.split("@", 1)[1]
        return digest if digest in known() else None
    return reg.get(ref)


argv = [a for a in sys.argv[1:] if not a.startswith("--")]
# swallow the value of --platform-style flags
argv = [a for a in argv if a not in ("linux/amd64", "linux/arm64")]
cmd, args = argv[0], argv[1:]
reg = load()

with open(LOG, "a") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\n")

if cmd == "digest":
    d = resolve(reg, args[0])
    if d is None:
        print(f"MANIFEST_UNKNOWN: {args[0]}", file=sys.stderr)
        sys.exit(1)
    print(d)
elif cmd == "copy":
    src, dst = args[0], args[1]
    d = resolve(reg, src)
    if d is None:
        print(f"MANIFEST_UNKNOWN: {src}", file=sys.stderr)
        sys.exit(1)
    reg[dst] = d
    learn(d)
    save(reg)
elif cmd == "ls":
    repo = args[0]
    for ref in sorted(reg):
        if ref.startswith(repo + ":"):
            print(ref.split(":", 1)[1])
elif cmd == "config":
    d = resolve(reg, args[0])
    cfgs = json.loads(os.environ.get("FAKE_CONFIGS", "{}"))
    if d is None or d not in cfgs:
        print(f"no config for {args[0]}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(cfgs[d]))
elif cmd == "delete":
    ref = args[0]
    if ref not in reg:
        sys.exit(1)
    del reg[ref]
    save(reg)
elif cmd == "auth":
    pass
else:
    print(f"crane stub: unimplemented subcommand {cmd!r}", file=sys.stderr)
    sys.exit(97)
'''


def step_script(workflow: Path, job: str, step_name: str) -> str:
    """Return one step's `run:` script, refusing any unresolved expression.

    Substituting `${{ }}` here would mean the test exercises a script that is not
    the one CI runs. Steps under test pass their inputs via `env:` (which is also
    the injection-safe form), so a leftover expression is a finding, not an
    inconvenience to paper over.
    """
    wf = yaml.safe_load(workflow.read_text())
    assert job in wf["jobs"], f"job {job!r} not in {workflow.name}"
    for s in wf["jobs"][job]["steps"]:
        if s.get("name") == step_name:
            run = s["run"]
            leftover = re.findall(r"\$\{\{[^}]*\}\}", run)
            # secrets.* are masked by the runner and are not attacker-controlled
            # free text; anything else spliced into bash is a real hazard.
            bad = [x for x in leftover if "secrets." not in x]
            assert not bad, (
                f"{workflow.name}:{job}/{step_name} interpolates {bad} directly into "
                f"bash. Pass it via env: — a dispatch input is free text and `${{{{ }}}}` "
                f"splices it in before the shell sees it.")
            for x in leftover:
                run = run.replace(x, "SECRET")
            return run
    raise AssertionError(f"step {step_name!r} not found in {job}")


def step_script_resolved(workflow: Path, job: str, step_name: str,
                         subs: dict[str, str]) -> str:
    """Like `step_script`, but for a REUSABLE workflow's step.

    `qa-gate.yml` is called with `with:`/`uses:`, so its steps legitimately read
    `${{ inputs.* }}` and `${{ steps.*.outputs.* }}`. Those are declared, typed
    values supplied by another workflow in this repo — not the dispatch free text
    that `step_script`'s blanket refusal exists to stop being spliced into bash.

    The guard is kept, not dropped: every expression must be named explicitly in
    `subs`. Anything left over still fails, so a NEW expression cannot appear in
    a covered step without a test author looking at it and deciding it is safe.
    """
    wf = yaml.safe_load(workflow.read_text())
    assert job in wf["jobs"], f"job {job!r} not in {workflow.name}"
    for s in wf["jobs"][job]["steps"]:
        if s.get("name") != step_name:
            continue
        run = s["run"]
        for expr in set(re.findall(r"\$\{\{[^}]*\}\}", run)):
            inner = expr[3:-2].strip()
            if inner in subs:
                run = run.replace(expr, subs[inner])
            elif inner.startswith("secrets."):
                run = run.replace(expr, "SECRET")
        leftover = re.findall(r"\$\{\{[^}]*\}\}", run)
        assert not leftover, (
            f"{workflow.name}:{job}/{step_name} has unsubstituted expressions "
            f"{leftover}. Add them to `subs` deliberately — an expression that "
            f"appears without anyone deciding what it means is how an injection "
            f"or a silently-disabled guard gets into a covered step.")
        return run
    raise AssertionError(f"step {step_name!r} not found in {job}")


def run_step(script: str, workdir: Path, registry: dict, env: dict,
             configs: dict | None = None, extra_digests=()) -> dict:
    """Run `script` under bash with a stubbed crane. Returns the outcome + registry.

    `registry` maps a ref ("ns/base-image:tag") to a digest. It is mutated by
    `crane copy` exactly as the real one would be, so a test can assert the final
    state of an auto tag rather than the presence of a line that would set it.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    bindir = workdir / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "crane").write_text(_CRANE_STUB)
    (bindir / "crane").chmod(0o755)

    reg_file = workdir / "registry.json"
    reg_file.write_text(json.dumps(registry, indent=2))
    # Manifests are content-addressed and outlive the tags that reference them.
    known_file = workdir / "digests.json"
    # extra_digests models manifests that no tag points at any more — e.g. a
    # rebuild rewrote the tag, but the digest this run pinned is still pullable.
    # Without it, a rebuild fixture would make the pinned ref look deleted and the
    # test would pass for the wrong reason.
    known_file.write_text(json.dumps(sorted(set(registry.values()) | set(extra_digests))))
    log = workdir / "crane.log"
    log.write_text("")
    gh_out = workdir / "github_output"
    gh_out.write_text("")
    gh_sum = workdir / "github_step_summary"
    gh_sum.write_text("")

    full_env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "FAKE_REGISTRY": str(reg_file),
        "FAKE_DIGESTS": str(known_file),
        "FAKE_CONFIGS": json.dumps(configs or {}),
        "CRANE_LOG": str(log),
        "GITHUB_OUTPUT": str(gh_out),
        "GITHUB_STEP_SUMMARY": str(gh_sum),
        "HOME": str(workdir),
        **{k: str(v) for k, v in env.items()},
    }
    proc = subprocess.run(["bash", "-c", script], cwd=workdir, env=full_env,
                          capture_output=True, text=True, timeout=120)
    return {
        "code": proc.returncode,
        "out": proc.stdout,
        "err": proc.stderr,
        "registry": json.loads(reg_file.read_text()),
        "crane_calls": [l for l in log.read_text().splitlines() if l],
        "github_output": gh_out.read_text(),
        "summary": gh_sum.read_text(),
    }


def run_retry_loop(workdir: Path, attempts: list, retries: int = 2,
                   require_tests: str = "") -> dict:
    """Execute qa-gate.yml's live-test step against a scripted attempt sequence.

    `attempts` is a list of {"code": int, "raw": dict} — what test_template.py
    returns on attempt 1, 2, ... (the last entry repeats if the loop runs on).
    Returns the recorded exit_code/attempts outputs plus how many times the
    client was actually invoked, which is the number the guard cares about:
    a `block` must cost exactly one attempt, no matter how many retries are
    budgeted.
    """
    import sys

    workdir.mkdir(parents=True, exist_ok=True)
    bindir = workdir / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "python").write_text(_PYTHON_STUB)
    (bindir / "python").chmod(0o755)

    plan = workdir / "plan.json"
    plan.write_text(json.dumps(attempts))
    count = workdir / "count"
    count.write_text("0")
    gh_out = workdir / "github_output"
    gh_out.write_text("")

    script = step_script_resolved(QA_GATE, "qa", "Run live test (DESTROY the instance after)", {
        "inputs.retries": str(retries),
        "inputs.retry_delay": "0",
        "inputs.require_tests": require_tests,
        "inputs.require_floor": "false",
        "inputs.label": "base-image-qa-test",
        "inputs.max_price": "1.0",
        "inputs.timeout": "60",
        "steps.create.outputs.hash": "deadbeef",
    })

    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "REAL_PYTHON": sys.executable,
        "ATTEMPT_PLAN": str(plan),
        "ATTEMPT_COUNT": str(count),
        "TM": str(REPO / "tools/template_manager"),
        "GITHUB_OUTPUT": str(gh_out),
        "LOG_PATHS": "",
        "EXTRA_ENV": "",
        "HOME": str(workdir),
    }
    proc = subprocess.run(["bash", "-c", script], cwd=workdir, env=env,
                          capture_output=True, text=True, timeout=120)
    out = gh_out.read_text()
    return {
        "code": proc.returncode,
        "out": proc.stdout,
        "err": proc.stderr,
        "invocations": int(count.read_text()),
        "exit_code": _kv(out, "exit_code"),
        "attempts": _kv(out, "attempts"),
    }


def _kv(text: str, key: str) -> str:
    vals = [l.split("=", 1)[1] for l in text.splitlines() if l.startswith(key + "=")]
    return vals[-1] if vals else ""


def requires_tools():
    """jq is a hard dependency of every script under test."""
    return shutil.which("jq") is not None


# --- fixtures shared by the behavioural tests ------------------------------

PROD = "prod"
STAGING = "staging"
DATE = "2026-08-07"

# Two cuda configs is the minimum that exercises the interesting cases: one
# flipping and one holding in the same run, plus a distinct Phase A anchor.
CONFIGS = [
    {"key": "cuda-12.9-24", "tag_template": "cuda-12.9.2-cudnn-devel-ubuntu24.04",
     "default_python": "3.12"},
    {"key": "cuda-13.3-24", "tag_template": "cuda-13.3.1-cudnn-devel-ubuntu24.04",
     "default_python": "3.12"},
]


def build_registry(target_digests: dict, auto_digests: dict) -> dict:
    """A staging namespace holding every python of every config, plus prod autos."""
    reg = {}
    for c in CONFIGS:
        tgt = target_digests[c["key"]]
        for py in ("3.10", "3.11", "3.12", "3.13", "3.14"):
            pt = py.replace(".", "")
            d = tgt if py == c["default_python"] else f"sha256:other-{c['key']}-{pt}"
            reg[f"{STAGING}/base-image:{c['tag_template']}-py{pt}-{DATE}"] = d
    for cuda_ver, digest in auto_digests.items():
        reg[f"{PROD}/base-image:cuda-{cuda_ver}-auto"] = digest
    return reg


PYS = ("3.10", "3.11", "3.12", "3.13", "3.14")


def py_digests(cfg, target):
    """Every python pinned, exactly as resolve-digests records them."""
    out = {}
    for py in PYS:
        pt = "py" + py.replace(".", "")
        out[pt] = target if py == cfg["default_python"] else f"sha256:other-{cfg['key']}-{pt[2:]}"
    return out


def manifest(target_digests: dict, current_digests: dict) -> dict:
    return {"configs": [
        {"key": c["key"], "tag_template": c["tag_template"],
         "default_py": f"py{c['default_python'].replace('.', '')}",
         "default_python": c["default_python"],
         "auto_version": c["tag_template"].split("-")[1],
         "target_digest": target_digests[c["key"]],
         "current_digest": current_digests.get(c["key"], ""),
         "py_digests": py_digests(c, target_digests[c["key"]])}
        for c in CONFIGS], "mini": []}


def decisions(verdicts: dict, target_digests: dict) -> list:
    out = []
    for c in CONFIGS:
        out.append({
            "key": c["key"], "tag_template": c["tag_template"],
            "default_python": c["default_python"],
            "auto_version": c["tag_template"].split("-")[1],
            "target_digest": target_digests[c["key"]],
            "decision": verdicts[c["key"]],
            "reason": "synthetic",
        })
    return out


def promote_env() -> dict:
    return {
        "NAMESPACE_STAGING": STAGING,
        "NAMESPACE_PROD": PROD,
        "STAGING_DATE": DATE,
        "CONFIGS": json.dumps(CONFIGS),
        "ALL_CONFIGS": json.dumps(CONFIGS),
        "MINI_TAGS": json.dumps([]),
    }


def auto_tag(cuda_ver: str) -> str:
    return f"{PROD}/base-image:cuda-{cuda_ver}-auto"


def dedent(s: str) -> str:
    return textwrap.dedent(s)
