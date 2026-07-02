"""``imagegen qa`` — run the live-GPU QA smoke for an image, and on failure HOLD the rented
box so the ``qa-fix`` skill can diagnose against a live workbench (ADR 0009, human-gated).

Reuses ``tools/template_manager`` wholesale: ``create.py`` publishes the private
``<name>-qa`` template pointed at the staging image, ``test_template.py <hash> --keep``
rents+boots+streams+verdicts. This module only orchestrates, and — the load-bearing part —
guarantees teardown: it writes a teardown LEDGER the instant a box is held, tears the box
down itself on PASS, and stamps a ``base-image-qa-*`` label so the scheduled reaper
(``reap_orphans.py``) destroys any box a crash abandons. It never rounds the API key
through the box.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_TM = Path(__file__).resolve().parents[2] / "template_manager"   # tools/template_manager
_REPO = Path(__file__).resolve().parents[3]                      # repo root

# Label prefix: MUST stay under the scheduled reaper's scope (reap_orphans.py --label
# base-image-qa) so an abandoned held box is destroyed even if this process dies.
_LABEL_PREFIX = "base-image-qa-imagegen"


def _load_dotenv(repo: Path) -> None:
    """Populate os.environ from a gitignored repo-root .env (VAST_API_KEY for the QA
    account, DOCKERHUB_NAMESPACE_STAGING). Never overrides an already-set var."""
    env = repo / ".env"
    if not env.is_file():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _find_image_dir(repo: Path, name: str) -> Path:
    """Locate the image directory across the three class roots."""
    for rel in (f"derivatives/pytorch/derivatives/{name}", f"derivatives/{name}", f"external/{name}"):
        d = repo / rel
        if (d / "templates" / f"{name}-qa" / "template.yml").is_file():
            return d
    raise SystemExit(f"imagegen qa: no {name}/templates/{name}-qa/template.yml found under any class root")


def _staging_ref(name: str, tag: str | None) -> tuple[str, str]:
    """Return (image, tag) for the box under test. --tag may be a full ref
    (repo/name:tag) or a bare tag; otherwise use the staging-namespace convention."""
    if tag and "/" in tag:
        ref, _, t = tag.partition(":")
        return ref, (t or "latest")
    if tag:
        ns = os.environ.get("DOCKERHUB_NAMESPACE_STAGING") or _die_ns()
        return f"{ns}/{name}", tag
    ns = os.environ.get("DOCKERHUB_NAMESPACE_STAGING") or _die_ns()
    return f"{ns}/{name}", "latest"


def _die_ns():
    raise SystemExit("imagegen qa: DOCKERHUB_NAMESPACE_STAGING unset and --tag not a full ref; "
                     "set it in .env or pass --tag <repo/name:tag>")


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], text=True, **kw)


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
    """Written the instant a box is held — the contract the reaper and the interactive
    teardown both honour. Must precede any long-running hold."""
    qa_dir.mkdir(parents=True, exist_ok=True)
    (qa_dir / "teardown-ledger.json").write_text(json.dumps(
        {"instance_id": instance_id, "label": label, "held_at": int(time.time())}, indent=2))


def _teardown(api_key: str, instance_id, qa_dir: Path, log) -> None:
    """Destroy the held box and clear the ledger. Reuses test_template's API+destroy so the
    404-is-gone semantics are shared."""
    if instance_id is None:
        return
    sys.path.insert(0, str(_TM))
    import test_template as tt  # noqa: E402
    try:
        tt.VastAPI(api_key).destroy_instance(instance_id)
        log(f"  torn down instance {instance_id}")
    except Exception as e:  # best-effort; the reaper is the backstop
        log(f"  WARN: teardown of {instance_id} failed ({e}); reaper will reap by label")
    (qa_dir / "teardown-ledger.json").unlink(missing_ok=True)


def _write_bundle(qa_dir: Path, verdict: dict, name: str, img_dir: Path,
                  template_dir: Path, logs: list[str], staging: str) -> None:
    """The file-based handoff the qa-fix skill consumes: verdict, streamed logs, SSH coords,
    and the image's source-fix surface. Versioned so a launcher change can't feed stale shape."""
    qa_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema": 1,
        "image": name,
        "staging_ref": staging,
        "verdict": verdict,                       # state, exit_code, got_result_event, tests, stream_counts
        "instance_id": verdict.get("instance_id"),
        "ssh": verdict.get("ssh", {}),            # {host, port} — SSH into the live workbench
        "log_paths": logs,
        "fix_surface": {                          # the CLOSED set the skill may change (ADR 0009 cond 6)
            "dockerfile": str((img_dir / "Dockerfile").relative_to(_REPO)),
            "supervisor_scripts": str((img_dir / "ROOT/opt/supervisor-scripts").relative_to(_REPO)),
            "qa_template": str((template_dir / "template.yml").relative_to(_REPO)),
            "default_template": str((img_dir / "templates/default/template.yml").relative_to(_REPO)),
        },
    }
    (qa_dir / "bundle.json").write_text(json.dumps(bundle, indent=2))


def run(name: str, *, tag: str | None = None, logs: list[str] | None = None,
        max_price: str = "0.60", timeout: str = "1800", log=None) -> int:
    """Publish the QA template, run the live smoke holding the box, and on failure write the
    diagnosis bundle + teardown ledger. Returns the verdict exit code."""
    log = log or (lambda m: print(m, file=sys.stderr))
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
    py = sys.executable

    # 1. publish the throwaway QA template pointed at the staging image
    created_json = qa_dir / "create.json"
    qa_dir.mkdir(parents=True, exist_ok=True)
    log(f"imagegen qa: {name} → {image_ref}:{tag_ref}  (template {template_dir.relative_to(_REPO)})")
    cp = _run([py, _TM / "create.py", template_dir, "--image", image_ref, "--tag", tag_ref,
               "--emit-result", created_json])
    if cp.returncode != 0:
        raise SystemExit(f"imagegen qa: template publish failed (exit {cp.returncode})")
    created = json.loads(created_json.read_text())[0]
    tmpl_hash, tmpl_id = created["hash_id"], created["id"]

    # 2. run the live smoke, HOLDING the box (--keep) regardless of verdict
    args = [py, _TM / "test_template.py", tmpl_hash, "--keep", "--raw", "--force", "--label", label,
            "--max-price", max_price, "--timeout", timeout]
    for p in logs:
        args += ["--log", p]
    cp = _run(args, capture_output=True)
    sys.stderr.write(cp.stderr or "")
    verdict = _last_json(cp.stdout)
    exit_code = verdict.get("exit_code", cp.returncode)
    instance_id = verdict.get("instance_id")

    # 3. LEDGER FIRST — the instant a box is held, before any slow path. Then drop the template.
    if instance_id is not None:
        _write_ledger(qa_dir, instance_id, label)
    _run([py, _TM / "create.py", "--delete", tmpl_id], capture_output=True)  # box, not template, is the workbench

    # 4. route on the verdict
    passed = exit_code == 0 and verdict.get("got_result_event") is True
    if passed:
        log("\nQA PASSED — live-GPU smoke green.")
        _teardown(api_key, instance_id, qa_dir, log)
        (qa_dir / "bundle.json").unlink(missing_ok=True)
        return 0

    _write_bundle(qa_dir, verdict, name, img_dir, template_dir, logs, f"{image_ref}:{tag_ref}")
    ssh = verdict.get("ssh") or {}
    log("\n" + "=" * 70)
    log(f"QA BLOCKED (exit {exit_code}, state={verdict.get('state')}). Box HELD for diagnosis.")
    if instance_id is not None:
        log(f"  instance {instance_id}  label {label}")
    if ssh.get("host"):
        log(f"  ssh -p {ssh['port']} root@{ssh['host']}")
    log(f"  bundle: {(qa_dir / 'bundle.json').relative_to(_REPO)}")
    log("  next: run the qa-fix skill to diagnose + propose a fix (human-gated).")
    log(f"  teardown when done: imagegen qa-teardown {name}   (reaper reaps by label after TTL)")
    log("=" * 70)
    return exit_code


def teardown(name: str, log=None) -> int:
    """Tear down the held box recorded in the image's teardown ledger (the interactive
    counterpart to a crash being reaped by label)."""
    log = log or (lambda m: print(m, file=sys.stderr))
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
