"""``imagegen qa`` — run the live-GPU QA smoke for an image, and on a real failure HOLD the
rented box so the ``qa-fix`` skill can diagnose against a live workbench (ADR 0009,
human-gated).

Reuses ``tools/template_manager`` wholesale: ``create.py`` publishes the private
``<name>-qa`` template pointed at the staging image, ``test_template.py <hash> --keep``
rents+boots+streams+verdicts. This module orchestrates, and — the load-bearing part —
guarantees teardown of a billing box:

- it **streams the child's stderr and writes a teardown LEDGER the instant the box is
  created** (the ``QA-INSTANCE-CREATED`` marker), so recovery never depends on ``instance_id``
  reaching the final ``--raw`` verdict (which it doesn't, on an early-exit-after-launch);
- it **holds the box only on a real app-failure (exit 1)** and 404-safely tears the box down
  on every other verdict (pass / suspicious / no_offers / config_error / instance_error);
- the ``base-image-qa-imagegen-*`` label is the reaper's backstop for a crashed run.
It never rounds the API key through the box.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_TM = Path(__file__).resolve().parents[2] / "template_manager"   # tools/template_manager
_REPO = Path(__file__).resolve().parents[3]                      # repo root
_LABEL_PREFIX = "base-image-qa-imagegen"      # MUST stay under reap_orphans' base-image-qa scope
_CREATED_RE = re.compile(r"QA-INSTANCE-CREATED\s+(\S+)")


def _load_dotenv(repo: Path) -> None:
    """Populate os.environ from a gitignored repo-root .env. Tolerates `export KEY=VAL`,
    quotes, and ` #` inline comments (on unquoted values). Never overrides a set var."""
    env = repo / ".env"
    if not env.is_file():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v[:1] not in "\"'" and " #" in v:      # strip inline comment on unquoted values only
            v = v.split(" #", 1)[0].strip()
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _find_image_dir(repo: Path, name: str) -> Path:
    for rel in (f"derivatives/pytorch/derivatives/{name}", f"derivatives/{name}", f"external/{name}"):
        d = repo / rel
        if (d / "templates" / f"{name}-qa" / "template.yml").is_file():
            return d
    raise SystemExit(f"imagegen qa: no {name}/templates/{name}-qa/template.yml found under any class root")


def _staging_ref(name: str, tag: str | None) -> tuple[str, str]:
    """(image, tag) for the box under test. --tag may be a full ref (repo/name[:tag], incl.
    a registry:port) or a bare tag; otherwise the staging-namespace convention."""
    if tag and "/" in tag:
        if ":" in tag.rsplit("/", 1)[1]:          # a tag after the last '/', not a registry:port
            ref, _, t = tag.rpartition(":")
            return ref, t
        return tag, "latest"
    ns = os.environ.get("DOCKERHUB_NAMESPACE_STAGING")
    if not ns:
        raise SystemExit("imagegen qa: DOCKERHUB_NAMESPACE_STAGING unset and --tag not a full ref; "
                         "set it in .env or pass --tag <repo/name:tag>")
    return f"{ns}/{name}", (tag or "latest")


def _check_deps() -> None:
    """create.py / test_template.py are shelled with this same interpreter, so their
    third-party deps must be importable here. Fail with the install hint, not a traceback."""
    missing = [m for m in ("dotenv", "yaml", "pydantic") if importlib.util.find_spec(m) is None]
    if missing:
        req = (_TM / "requirements.txt").relative_to(_REPO)
        have_pip = importlib.util.find_spec("pip") is not None
        how = (f"  {Path(sys.executable).name} -m pip install -r {req}" if have_pip else
               f"  uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r {req}\n"
               f"then invoke with .venv/bin/python (this interpreter has no pip)")
        raise SystemExit(
            f"imagegen qa: template_manager deps not importable ({', '.join(missing)}). "
            f"Run with an interpreter that has them:\n{how}\n(see tools/imagegen/README.md)")


def _run(cmd: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], text=True, **kw)


def _ssh_reachable(ssh: dict) -> bool:
    """Probe whether the operator can actually SSH into the held box. Vast auto-injects the
    SSH keys registered on the *account that owns the instance* (the QA account) — so a key
    that's only on a personal account means qa-fix can't get in. Non-interactive, ~12s cap."""
    host, port = ssh.get("host"), ssh.get("port")
    if not host or not port:
        return False
    r = _run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
              "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=12",
              "-p", str(port), f"root@{host}", "true"], capture_output=True)
    return r.returncode == 0


def _last_json(stdout: str) -> dict:
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


def _write_ledger(qa_dir: Path, instance_id, label: str) -> None:
    qa_dir.mkdir(parents=True, exist_ok=True)
    (qa_dir / "teardown-ledger.json").write_text(json.dumps(
        {"instance_id": instance_id, "label": label}, indent=2))


def _run_test(args: list, qa_dir: Path, label: str, log) -> tuple[int, str, str | None]:
    """Run test_template.py, streaming its stderr live, and write a PROVISIONAL teardown
    ledger the instant the box is created (the QA-INSTANCE-CREATED marker) — so a billing
    box is recorded before any verdict, whatever way the child exits. stdout carries only
    the single --raw JSON line (test_template sends all human output to stderr), so reading
    it after draining stderr cannot deadlock. Returns (returncode, stdout, created_id)."""
    proc = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1)
    created_id = None
    for line in proc.stderr:
        sys.stderr.write(line)                    # live passthrough — the operator watches the run
        m = _CREATED_RE.search(line)
        if m and created_id is None:
            created_id = m.group(1)
            _write_ledger(qa_dir, created_id, label)
            log(f"  [ledger] box {created_id} recorded — teardown-ledger.json")
    out = proc.stdout.read()
    proc.wait()
    return proc.returncode, out, created_id


def _teardown(api_key, instance_id, qa_dir: Path, log) -> None:
    """Destroy the box; clear the ledger ONLY on confirmed destroy (destroy_instance is
    404-safe, so 'already gone' counts as success). A real failure (bad key, 5xx) keeps the
    ledger so `qa-teardown`/the reaper can still recover it."""
    if instance_id is None:
        return
    if not api_key:
        log(f"  WARN: no VAST_API_KEY — cannot tear down {instance_id}; ledger kept, reaper is the backstop")
        return
    sys.path.insert(0, str(_TM))
    import test_template as tt  # noqa: E402
    try:
        tt.VastAPI(api_key).destroy_instance(instance_id)   # 404-safe
        log(f"  torn down instance {instance_id}")
        (qa_dir / "teardown-ledger.json").unlink(missing_ok=True)
    except Exception as e:
        log(f"  WARN: teardown of {instance_id} failed ({e}); ledger KEPT, reaper will reap by label")


def _write_bundle(qa_dir: Path, verdict: dict, name: str, img_dir: Path,
                  template_dir: Path, logs: list, staging: str, instance_id) -> None:
    qa_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema": 1,
        "image": name,
        "staging_ref": staging,
        "verdict": verdict,
        "instance_id": instance_id,
        "ssh": verdict.get("ssh", {}),
        "log_paths": logs,
        "fix_surface": {          # the CLOSED set the skill may change (ADR 0009 cond 6)
            "dockerfile": str((img_dir / "Dockerfile").relative_to(_REPO)),
            "supervisor_scripts": str((img_dir / "ROOT/opt/supervisor-scripts").relative_to(_REPO)),
            "tests": str((img_dir / "ROOT/opt/instance-tools/tests").relative_to(_REPO)),
            "qa_template": str((template_dir / "template.yml").relative_to(_REPO)),
            "default_template": str((img_dir / "templates/default/template.yml").relative_to(_REPO)),
        },
    }
    (qa_dir / "bundle.json").write_text(json.dumps(bundle, indent=2))


_EXPLAIN = {
    0: "inconclusive — 'passed' but no result event (ADR 0005 cond 2); re-run.",
    2: "no GPU offers matched — re-run later or raise --max-price.",
    3: "bad instance — the box never came up cleanly; re-run.",
    4: "config error — the QA template / args / creds are wrong (a harness bug, NOT the image).",
    5: "instance died mid-test — re-run; if it recurs the image may crash on boot (check the logs).",
    130: "interrupted.",
}


def run(name: str, *, tag: str | None = None, logs: list | None = None,
        max_price: str = "0.60", timeout: str = "1800", log=None) -> int:
    """Publish the QA template, run the live smoke (holding the box), and route on the
    verdict. Holds the box for diagnosis ONLY on a real app-failure (exit 1)."""
    log = log or (lambda m: print(m, file=sys.stderr))
    _check_deps()
    _load_dotenv(_REPO)
    api_key = os.environ.get("VAST_API_KEY")
    if not api_key:
        raise SystemExit("imagegen qa: VAST_API_KEY unset (put the QA-account key in a gitignored .env)")

    img_dir = _find_image_dir(_REPO, name)
    template_dir = img_dir / "templates" / f"{name}-qa"
    image_ref, tag_ref = _staging_ref(name, tag)
    logs = logs or [f"/var/log/portal/{name}.log"]
    label = f"{_LABEL_PREFIX}-{name}"
    qa_dir = img_dir / ".qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    py = sys.executable

    # 1. publish the throwaway QA template pointed at the staging image
    created_json = qa_dir / "create.json"
    log(f"imagegen qa: {name} → {image_ref}:{tag_ref}  (template {template_dir.relative_to(_REPO)})")
    cp = _run([py, _TM / "create.py", template_dir, "--image", image_ref, "--tag", tag_ref,
               "--emit-result", created_json])
    if cp.returncode != 0:
        raise SystemExit(f"imagegen qa: template publish failed (exit {cp.returncode})")
    results = json.loads(created_json.read_text())
    if not results:
        raise SystemExit("imagegen qa: template publish returned no templates")
    created = results[0]
    tmpl_hash, tmpl_id = created.get("hash_id"), created.get("id")
    if tmpl_hash in (None, "N/A") or tmpl_id in (None, "N/A"):
        raise SystemExit(f"imagegen qa: publish returned no usable hash_id/id ({created})")

    # 2. run the live smoke, HOLDING the box; provisional ledger written on box-create
    args = [py, _TM / "test_template.py", tmpl_hash, "--keep", "--raw", "--force", "--label", label,
            "--max-price", max_price, "--timeout", timeout]
    for p in logs:
        args += ["--log", p]
    returncode, out, created_id = _run_test(args, qa_dir, label, log)
    verdict = _last_json(out)
    raw_code = verdict.get("exit_code")
    # Reconcile: prefer the payload, but NEVER report PASS on a nonzero process code.
    if raw_code is None:
        exit_code = returncode
    elif raw_code == returncode:
        exit_code = raw_code
    else:
        exit_code = raw_code or returncode
    instance_id = verdict.get("instance_id") or created_id

    # 3. drop the throwaway template (the reaper sweeps instances, not templates — warn on leak)
    dcp = _run([py, _TM / "create.py", "--delete", tmpl_id], capture_output=True)
    if dcp.returncode != 0:
        log(f"  WARN: template {tmpl_id} delete failed: {(dcp.stderr or '').strip()[:200]}")

    # 4. front-gate route
    if exit_code == 0 and verdict.get("got_result_event") is True:
        log("\nQA PASSED — live-GPU smoke green.")
        _teardown(api_key, instance_id, qa_dir, log)
        (qa_dir / "bundle.json").unlink(missing_ok=True)
        return 0

    if exit_code == 1:   # failed: booted, functional test failed → diagnosable, box kept alive
        _write_bundle(qa_dir, verdict, name, img_dir, template_dir, logs,
                      f"{image_ref}:{tag_ref}", instance_id)
        ssh = verdict.get("ssh") or {}
        log("\n" + "=" * 70)
        log("QA FAILED (functional test red). Box HELD for diagnosis.")
        if instance_id is not None:
            log(f"  instance {instance_id}  label {label}")
        if ssh.get("host"):
            log(f"  ssh -p {ssh['port']} root@{ssh['host']}")
            if _ssh_reachable(ssh):
                log("  ssh: reachable ✓ — qa-fix can diagnose on the box.")
            else:
                log("  ssh: NOT reachable ✗ — Vast injects the keys registered on the QA account")
                log("       (525202), not your personal account. Add your pubkey there, or qa-fix")
                log("       cannot diagnose. (Box still held; teardown below when you give up.)")
        log(f"  bundle: {(qa_dir / 'bundle.json').relative_to(_REPO)}")
        log("  next: run the qa-fix skill to diagnose + propose a fix (human-gated).")
        log(f"  teardown when done: imagegen qa-teardown {name}")
        log("=" * 70)
        return 1

    # 0-without-result / no_offers / bad_instance / config_error / instance_error / interrupted:
    # NOT a diagnosable image failure. Tear the box down (404-safe) and report.
    _teardown(api_key, instance_id, qa_dir, log)
    (qa_dir / "bundle.json").unlink(missing_ok=True)
    log(f"\nQA not run to a pass (exit {exit_code}, state={verdict.get('state')}): "
        + _EXPLAIN.get(exit_code, "see the verdict above."))
    return exit_code


def teardown(name: str, log=None) -> int:
    """Tear down the held box recorded in the image's teardown ledger (interactive
    counterpart to a crash being reaped by label)."""
    log = log or (lambda m: print(m, file=sys.stderr))
    _check_deps()
    _load_dotenv(_REPO)
    api_key = os.environ.get("VAST_API_KEY")
    qa_dir = _find_image_dir(_REPO, name) / ".qa"
    ledger = qa_dir / "teardown-ledger.json"
    if not ledger.is_file():
        log(f"imagegen qa-teardown: no held box for {name} (no ledger).")
        return 0
    rec = json.loads(ledger.read_text())
    _teardown(api_key, rec.get("instance_id"), qa_dir, log)
    return 0
