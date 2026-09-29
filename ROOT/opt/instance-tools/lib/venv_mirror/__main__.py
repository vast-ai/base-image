"""venv-mirror: build or check /venv/main's mirror of the engine's site-packages (ADR 0048).

    venv-mirror build  --venv /venv/main --engine-python "$VAST_ENGINE_PYTHON"
    venv-mirror verify --venv /venv/main --engine-import "$VAST_ENGINE_IMPORT"

`build` runs once, at image build time, on a fresh venv and before anything is installed
into it. `verify` only reads: it runs before `env-hash` in the image's last RUN, and from the
instance test. Nothing here runs at boot -- after first boot /venv/main is the user's.
"""

import argparse
import os
import sys
import time
from pathlib import Path

from . import (MirrorError, build, engine_info, engine_problems, shadow_problems,
               structural_problems)


def _build(args) -> int:
    venv = Path(args.venv)
    engine = engine_info(args.engine_python)
    mine = engine_info(str(venv / "bin" / "python"))
    if (mine["base"], mine["version"]) != (engine["base"], engine["version"]):
        raise MirrorError(f"{venv} runs on {mine['base']} ({mine['version']}) but the engine "
                        f"{args.engine_python} runs on {engine['base']} ({engine['version']}); "
                        "the mirror's compiled extensions would not load")
    own = os.path.realpath(venv)
    sources = [Path(s) for s in engine["sources"] if not s.startswith(own + os.sep)]
    if not sources:
        raise MirrorError(f"{args.engine_python} reports no site-packages to mirror")
    t = time.monotonic()
    res = build(venv, sources, engine_python=args.engine_python,
                venv_python=str(venv / "bin" / "python"))
    print(f"venv-mirror: {len(res.projects)} projects, {res.links} links, "
          f"{len(res.scripts)} launchers from {', '.join(map(str, sources))} "
          f"in {time.monotonic() - t:.1f}s; skipped {len(res.skipped)} "
          f"({', '.join(sorted(res.skipped)) or 'none'})")
    return 0


def _verify(args) -> int:
    venv = Path(args.venv)
    t = time.monotonic()
    problems = structural_problems(venv)
    if args.build:
        problems += shadow_problems(venv)
    if args.engine_import:
        problems += engine_problems(venv, args.engine_import.split())
    for p in problems:
        print(f"venv-mirror: FAIL {p}", file=sys.stderr)
    if problems:
        return 1
    print(f"venv-mirror: {venv} verified in {time.monotonic() - t:.1f}s")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="venv-mirror", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="mirror the engine's site-packages into a fresh venv")
    b.add_argument("--venv", default="/venv/main")
    b.add_argument("--engine-python", required=True)
    v = sub.add_parser("verify", help="read-only check of a built mirror")
    v.add_argument("--venv", default="/venv/main")
    v.add_argument("--build", action="store_true",
                   help="also fail on a mirrored project replaced during the image build")
    v.add_argument("--engine-import", default="",
                   help="space-separated modules that must import from the engine's files")
    args = ap.parse_args(argv)
    try:
        return _build(args) if args.cmd == "build" else _verify(args)
    except MirrorError as e:
        print(f"venv-mirror: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
