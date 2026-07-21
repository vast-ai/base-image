"""CLI: `imagegen lint [--all] [paths...]`. Exit non-zero on any ERROR finding."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from .discover import discover, find_repo_root, Image
from .linter import lint_image, lint_repo, ERROR, WARN, EXCEPTIONS, rules_markdown
from .generate import generate, CLASSES
from .portal import parse_portal_config
from . import portal_smoke


def _gather(args) -> tuple[Path, list[Image]]:
    repo = find_repo_root(Path(args.repo) if args.repo else Path.cwd())
    images = discover(repo)
    if not args.all and args.paths:
        wanted = {Path(p).resolve() for p in args.paths}
        images = [i for i in images if i.dir.resolve() in wanted or i.name in args.paths]
    return repo, images


def cmd_lint(args) -> int:
    repo, images = _gather(args)
    if not images:
        print("no images found", file=sys.stderr)
        return 2

    total_err = total_warn = 0
    for img in sorted(images, key=lambda i: (i.cls, i.name)):
        findings = lint_image(img, repo)
        if not args.warn:
            findings = [f for f in findings if f.severity == ERROR]
        if not findings:
            continue
        print(f"\n{img.cls}/{img.name}")
        for f in sorted(findings, key=lambda f: (f.severity != ERROR, f.code)):
            mark = "✗" if f.severity == ERROR else "·"
            print(f"  {mark} [{f.code} {f.severity}] {f.path}: {f.msg}")
        total_err += sum(f.severity == ERROR for f in findings)
        total_warn += sum(f.severity == WARN for f in findings)

    # repo-level checks (not tied to a single image) run once per sweep
    repo_findings = lint_repo(repo)
    if not args.warn:
        repo_findings = [f for f in repo_findings if f.severity == ERROR]
    if repo_findings:
        print("\n(repo-level)")
        for f in sorted(repo_findings, key=lambda f: (f.severity != ERROR, f.code)):
            mark = "✗" if f.severity == ERROR else "·"
            print(f"  {mark} [{f.code} {f.severity}] {f.path}: {f.msg}")
        total_err += sum(f.severity == ERROR for f in repo_findings)
        total_warn += sum(f.severity == WARN for f in repo_findings)

    n_excepted = len(EXCEPTIONS)
    print(f"\n{'='*60}")
    print(f"{len(images)} images | {total_err} errors"
          + (f" | {total_warn} warnings" if args.warn else "")
          + f" | {n_excepted} documented exceptions suppressed")
    if total_err == 0:
        print("baseline CLEAN ✓ (all gated invariants hold across existing images)")
    return 1 if total_err else 0


def cmd_new(args) -> int:
    repo = find_repo_root(Path(args.repo) if args.repo else Path.cwd())
    base_tag = None
    if args.resolve_base:
        if args.cls != "pytorch-nested":
            print("error: --resolve-base is pytorch-nested only (ADR 0013)", file=sys.stderr)
            return 2
        if not (args.torch and args.cuda):
            print("error: --resolve-base needs --torch and --cuda", file=sys.stderr)
            return 2
        from . import basetag
        try:
            base_tag = basetag.resolve(torch=args.torch, cuda=args.cuda, py=args.py,
                                       mini=(args.variant == "mini"))
        except (RuntimeError, LookupError) as e:
            print(f"error: base resolve failed: {e}", file=sys.stderr)
            return 2
        print(f"resolved base: {base_tag}")
    try:
        d = generate(repo, name=args.name, cls=args.cls, label=args.label,
                     port=args.port, upstream=args.upstream, base_tag=base_tag)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"scaffolded {args.cls} image at {d.relative_to(repo)}")
    img = next((i for i in discover(repo) if i.dir.resolve() == d.resolve()), None)
    if img:
        errors = [f for f in lint_image(img, repo) if f.severity == ERROR]
        struct = [f for f in errors if f.code != "L040"]
        skel = [f for f in errors if f.code == "L040"]
        print("structure:", "valid ✓" if not struct else f"{len(struct)} ERRORS:")
        for f in struct:
            print(f"  ✗ [{f.code}] {f.path}: {f.msg}")
        print(f"skeleton:  {len(skel)} file(s) with FILL/CHANGEME markers to complete"
              " — NOT buildable yet")
    print("\nNext: fill the `>>> FILL: ... <<<` markers and CHANGEME tags, then"
          f"\n`imagegen lint {args.name}` (must be 0 errors) and run the real `docker build`.")
    return 0


def cmd_resolve_base(args) -> int:
    from . import basetag
    try:
        print(basetag.resolve(torch=args.torch, cuda=args.cuda, py=args.py,
                              mini=(args.variant == "mini")))
    except (RuntimeError, LookupError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_bump(args) -> int:
    from . import bump as _bump
    repo = find_repo_root(Path(args.repo) if args.repo else Path.cwd())
    return _bump.bump(args.name, repo=repo)


def _read_arg(val: str | None) -> str:
    """Return the literal value, or the file contents if given as `@path`."""
    if not val:
        return ""
    if val.startswith("@"):
        return Path(val[1:]).read_text(encoding="utf-8", errors="replace")
    return val


def cmd_bindcheck(args) -> int:
    """ADR 0002 condition 1: the runtime bind-address smoke gate. Consumes a dumped
    `ss -ltnp` and the rendered PORTAL_CONFIG string; fails on any public bind that
    isn't the Caddy auth front. Orchestrated by tools/imagegen/smoke/bind-check.sh."""
    ss_text = _read_arg(args.ss)
    if not ss_text:
        print("error: --ss (the `ss -ltnp` dump, literal or @file) is required", file=sys.stderr)
        return 2

    pc_text = _read_arg(args.portal_config)
    try:
        entries = parse_portal_config(pc_text) if pc_text else []
    except ValueError as e:
        print(f"error: bad PORTAL_CONFIG: {e}", file=sys.stderr)
        return 2

    # exposed ports: explicit override, else from the image/Dockerfile.
    if args.exposed:
        exposed = {int(t.split("/")[0]) for t in args.exposed.split() if t.split("/")[0].isdigit()}
    elif args.dockerfile:
        exposed = portal_smoke.exposed_ports(Path(args.dockerfile).read_text(encoding="utf-8", errors="replace"))
    elif args.image:
        repo = find_repo_root(Path(args.repo) if args.repo else Path.cwd())
        img = next((i for i in discover(repo) if i.name == args.image), None)
        if not img:
            print(f"error: image {args.image!r} not found", file=sys.stderr)
            return 2
        exposed = portal_smoke.exposed_ports(img.text)
    else:
        print("error: need one of --exposed / --dockerfile / --image", file=sys.stderr)
        return 2

    listeners = portal_smoke.parse_ss(ss_text)
    violations = portal_smoke.check_binds(listeners, exposed, entries)
    errors = [v for v in violations if v.severity == ERROR]
    warns = [v for v in violations if v.severity == WARN]

    label = args.image or args.dockerfile or "image"
    print(f"bind-check {label}: {len(listeners)} listeners | EXPOSE {sorted(exposed) or '∅'} "
          f"| {len(errors)} errors | {len(warns)} warnings")
    for v in sorted(violations, key=lambda v: (v.severity != ERROR, v.port)):
        mark = "✗" if v.severity == ERROR else "·"
        print(f"  {mark} [{v.severity} :{v.port}] {v.detail}")
    if not errors:
        print("bind-check PASS ✓ (nothing reachable is bound public without Caddy in front)")
    return 1 if errors else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="imagegen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lint = sub.add_parser("lint", help="run invariant checks")
    lint.add_argument("paths", nargs="*", help="image dirs or names (default: all)")
    lint.add_argument("--all", action="store_true", help="lint every image")
    lint.add_argument("--repo", help="repo root (default: autodetect from cwd)")
    lint.add_argument("--warn", action="store_true", help="also show WARN findings")
    lint.set_defaults(func=cmd_lint)

    new = sub.add_parser("new", help="scaffold a new image (human picks the class)")
    new.add_argument("--class", dest="cls", required=True, choices=CLASSES)
    new.add_argument("--name", required=True)
    new.add_argument("--label", required=True)
    new.add_argument("--port", type=int, required=True)
    new.add_argument("--upstream", help="upstream image:tag (external only)")
    new.add_argument("--repo", help="repo root (default: autodetect from cwd)")
    new.add_argument("--resolve-base", action="store_true",
                     help="resolve+pin PYTORCH_BASE from DockerHub instead of CHANGEME (pytorch-nested; ADR 0013)")
    new.add_argument("--torch", help="torch version for --resolve-base (e.g. 2.10.0)")
    new.add_argument("--cuda", help="cuda toolkit for --resolve-base (e.g. 12.9)")
    new.add_argument("--py", default="312", help="python for --resolve-base (default 312)")
    new.add_argument("--variant", default="mini", choices=["mini", "full"],
                     help="base variant for --resolve-base (default mini)")
    new.set_defaults(func=cmd_new)

    rb = sub.add_parser("resolve-base", help="print the newest-dated vastai/pytorch tag for a (torch,cuda,py) tuple (ADR 0013)")
    rb.add_argument("--torch", required=True, help="torch version, e.g. 2.10.0")
    rb.add_argument("--cuda", required=True, help="cuda toolkit, e.g. 12.9")
    rb.add_argument("--py", default="312", help="python (default 312)")
    rb.add_argument("--variant", default="mini", choices=["mini", "full"], help="base variant (default mini)")
    rb.set_defaults(func=cmd_resolve_base)

    bmp = sub.add_parser("bump", help="re-resolve a pytorch-nested image's base pin(s) to the newest date (ADR 0013)")
    bmp.add_argument("name", help="image name")
    bmp.add_argument("--repo", help="repo root (default: autodetect from cwd)")
    bmp.set_defaults(func=cmd_bump)

    rules = sub.add_parser("rules", help="print the generated lint-rules reference (docs/lint-rules.md)")
    rules.set_defaults(func=lambda a: (print(rules_markdown(), end=""), 0)[1])

    qa = sub.add_parser("qa", help="run the live-GPU QA smoke; hold the box on failure for the qa-fix skill (ADR 0009)")
    qa.add_argument("name", help="image name (e.g. comfyui)")
    qa.add_argument("--tag", help="staging image to test: a full repo/name:tag, or a bare tag (default: <STAGING_NS>/<name>:latest)")
    qa.add_argument("--log", dest="logs", action="append", help="in-instance log file to stream (repeatable; default /var/log/portal/<name>.log)")
    qa.add_argument("--max-price", default="0.60", help="max $/hr for the rented GPU")
    qa.add_argument("--timeout", default="1800", help="per-run timeout seconds")
    qa.add_argument("--min-vram", type=float, help="VRAM floor in GB the qa box must have — for a "
                    "multi-model host whose template leaves it unset (injected as gpu_total_ram)")
    qa.set_defaults(func=lambda a: __import__("imagegen.qa", fromlist=["run"]).run(
        a.name, tag=a.tag, logs=a.logs, max_price=a.max_price, timeout=a.timeout, min_vram=a.min_vram))

    build = sub.add_parser("build", help="build the image locally (+ --push to staging) — the step qa-fix rebuilds with")
    build.add_argument("name", help="image name")
    build.add_argument("--ref", help="upstream ref for the <NAME>_REF build-arg (reused from the last build if omitted)")
    build.add_argument("--tag", help="image ref to tag: a full repo/name:tag, a bare tag under the staging ns, or default <ns>/<name>:latest")
    build.add_argument("--push", action="store_true", help="docker push after building (staging must be public for qa to pull it)")
    build.set_defaults(func=lambda a: __import__("imagegen.qa", fromlist=["build"]).build(
        a.name, ref=a.ref, tag=a.tag, push=a.push))

    pub = sub.add_parser("publish", help="publish a PRIVATE, staging-pointed, idempotent dogfood template (ADR 0011)")
    pub.add_argument("name", help="image name")
    pub.add_argument("--tag", help="staging image to point at: a full repo/name:tag or a bare tag (default: the last `imagegen build` tag, else <STAGING_NS>/<name>:latest)")
    pub.set_defaults(func=lambda a: __import__("imagegen.qa", fromlist=["publish"]).publish(
        a.name, tag=a.tag))

    qat = sub.add_parser("qa-teardown", help="tear down the held QA box recorded in the image's ledger")
    qat.add_argument("name", help="image name")
    qat.set_defaults(func=lambda a: __import__("imagegen.qa", fromlist=["teardown"]).teardown(a.name))

    bc = sub.add_parser("bindcheck", help="runtime bind-address smoke gate (ADR 0002 cond. 1)")
    bc.add_argument("--ss", required=True, help="`ss -ltnp` output (literal or @file)")
    bc.add_argument("--portal-config", dest="portal_config",
                    help="rendered PORTAL_CONFIG string (literal or @file)")
    bc.add_argument("--exposed", help='EXPOSE ports, space-separated (e.g. "7860 8000")')
    bc.add_argument("--dockerfile", help="read EXPOSE ports from this Dockerfile")
    bc.add_argument("--image", help="read EXPOSE ports from this image's Dockerfile (by name)")
    bc.add_argument("--repo", help="repo root (default: autodetect from cwd)")
    bc.set_defaults(func=cmd_bindcheck)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
