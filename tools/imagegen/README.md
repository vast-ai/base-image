# imagegen

Static invariant-linter for base-image Docker images (the generator comes later).
Scope and rationale: [docs/adr/0001](../../docs/adr/0001-image-scaffolding-tooling.md),
verified rules: [docs/invariants.md](../../docs/invariants.md).

**The linter is a FAST gate, not a correctness gate.** It checks file *shape* with
no build/GPU/registry. The real `docker build` (+ smoke test) is the correctness
check — green lint means "ready to build", never "correct" (ADR 0001 condition 1).

## Run

```bash
# from the repo root — lint every image (errors gate, exit non-zero on any)
PYTHONPATH=tools/imagegen python3 -m imagegen.cli lint --all

# include advisory warnings
PYTHONPATH=tools/imagegen python3 -m imagegen.cli lint --all --warn

# a single image by name or path
PYTHONPATH=tools/imagegen python3 -m imagegen.cli lint comfyui
```

Once installed (`pip install -e tools/imagegen`) the `imagegen` console script works
directly.

## Tests

```bash
cd tools/imagegen
PYTHONPATH=. python3 -m pytest -q          # CI / where pytest is available
PYTHONPATH=. python3 tests/test_linter.py  # stdlib fallback (no pytest needed)
```

The suite has one **mutant per invariant** (proves each check has teeth) plus a
**regression net** that lints the whole real repo and asserts it's clean.

## Checks (gated = ERROR; advisory = WARN)

Checks are **instruction-aware** (a small Dockerfile parser in `dockerfile.py`):
keywords in comments, across `\` continuations, or in the wrong position do not
produce false passes.

| Code | Sev | Applies | Rule |
|---|---|---|---|
| L001 | ERROR | all | exactly 3 LABEL instructions **with the required keys** |
| L002 | ERROR | all | `env-hash > /.env_hash` is the **final RUN** (not commented/stale) |
| L003 | ERROR | all | local `COPY ./ROOT /` present |
| L004 | ERROR | all (not base) | FROM matches class; external: `vast_base_image` **first**, vast base identity, graft present |
| L010 | ERROR | all w/ ROOT | every `[program:NAME]`: PROC_NAME + `command=/opt/.../NAME.sh` (basename==name); file stem is a program |
| L011 | ERROR | all w/ ROOT | sourced utils are an ordered subsequence of canonical order |
| L020 | ERROR | pytorch-nested | torch-drift guard with a real `pre == post` comparison that `exit 1`s |
| L021 | ERROR | pytorch-nested/external | no `--torch-backend auto` except inside a real `sed` substitution |
| L022 | WARN | pytorch-nested | prefer `uv pip` over bare `pip install` |
| L030 | WARN | all (not base) | a `build-<name>.yml` exists (not universal — see invariants §1) |

The **base image** (repo root) is linted too (class `base`: L001/L002/L003/L010/L011).

Documented exceptions live in `EXCEPTIONS` in `imagegen/linter.py`, **scoped to a
message substring** (not a whole check) so a different future break of the same
code is not silently suppressed. `test_no_stale_exceptions` fails if an exception
stops triggering. Currently: `aio-studio` L004/L020 (custom base + per-app venvs).

## Why "clean baseline" is trustworthy
The regression net alone is vacuous (would pass if every check were a no-op). The
suite therefore includes **mutation-against-real-files** tests: each corrupts a
real image and asserts the matching code fires. Neutering any check breaks its
mutation test.

## Not yet covered (deferred)
- CI **job-shape** parsing (5-job DAG, matrix, MATRIX_ID) — only existence (L030) so far.
- `PORTAL_CONFIG` port checks — intentionally **not** gated (`+10000` is not a real
  invariant; see invariants §3).
- docs⟷linter agreement test; class-sanity check; cross-file `exit_portal` label ↔
  PORTAL_CONFIG match (invariants §4).
