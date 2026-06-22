"""CLI: `imagegen lint [--all] [paths...]`. Exit non-zero on any ERROR finding."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from .discover import discover, find_repo_root, Image
from .linter import lint_image, ERROR, WARN, EXCEPTIONS


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="imagegen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lint = sub.add_parser("lint", help="run invariant checks")
    lint.add_argument("paths", nargs="*", help="image dirs or names (default: all)")
    lint.add_argument("--all", action="store_true", help="lint every image")
    lint.add_argument("--repo", help="repo root (default: autodetect from cwd)")
    lint.add_argument("--warn", action="store_true", help="also show WARN findings")
    lint.set_defaults(func=cmd_lint)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
