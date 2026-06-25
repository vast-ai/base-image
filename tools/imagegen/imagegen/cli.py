"""CLI: `imagegen lint [--all] [paths...]`. Exit non-zero on any ERROR finding."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from .discover import discover, find_repo_root, Image
from .linter import lint_image, ERROR, WARN, EXCEPTIONS, rules_markdown
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
    try:
        d = generate(repo, name=args.name, cls=args.cls, label=args.label,
                     port=args.port, upstream=args.upstream)
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
    new.set_defaults(func=cmd_new)

    rules = sub.add_parser("rules", help="print the generated lint-rules reference (docs/lint-rules.md)")
    rules.set_defaults(func=lambda a: (print(rules_markdown(), end=""), 0)[1])

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
