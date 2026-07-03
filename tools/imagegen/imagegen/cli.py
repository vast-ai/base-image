"""CLI: `imagegen lint [--all] [paths...]`. Exit non-zero on any ERROR finding."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from .discover import discover, find_repo_root, Image
from .linter import lint_image, ERROR, WARN, EXCEPTIONS, rules_markdown
from .generate import generate, CLASSES


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

    qa = sub.add_parser("qa", help="run the live-GPU QA smoke; hold the box on failure for the qa-fix skill (ADR 0009)")
    qa.add_argument("name", help="image name (e.g. chatterbox)")
    qa.add_argument("--tag", help="staging image to test: a full repo/name:tag, or a bare tag (default: <STAGING_NS>/<name>:latest)")
    qa.add_argument("--log", dest="logs", action="append", help="in-instance log file to stream (repeatable; default /var/log/portal/<name>.log)")
    qa.add_argument("--max-price", default="0.60", help="max $/hr for the rented GPU")
    qa.add_argument("--timeout", default="1800", help="per-run timeout seconds")
    qa.set_defaults(func=lambda a: __import__("imagegen.qa", fromlist=["run"]).run(
        a.name, tag=a.tag, logs=a.logs, max_price=a.max_price, timeout=a.timeout))

    build = sub.add_parser("build", help="build the image locally (+ --push to staging) — the step qa-fix rebuilds with")
    build.add_argument("name", help="image name")
    build.add_argument("--ref", help="upstream ref for the <NAME>_REF build-arg (reused from the last build if omitted)")
    build.add_argument("--tag", help="image ref to tag: a full repo/name:tag, a bare tag under the staging ns, or default <ns>/<name>:latest")
    build.add_argument("--push", action="store_true", help="docker push after building (staging must be public for qa to pull it)")
    build.set_defaults(func=lambda a: __import__("imagegen.qa", fromlist=["build"]).build(
        a.name, ref=a.ref, tag=a.tag, push=a.push))

    qat = sub.add_parser("qa-teardown", help="tear down the held QA box recorded in the image's ledger")
    qat.add_argument("name", help="image name")
    qat.set_defaults(func=lambda a: __import__("imagegen.qa", fromlist=["teardown"]).teardown(a.name))

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
