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

| Code | Sev | Applies | Rule |
|---|---|---|---|
| L001 | ERROR | all | exactly 3 LABEL lines |
| L002 | ERROR | all | `env-hash > /.env_hash` trailer present |
| L003 | ERROR | all | `COPY ./ROOT /` present |
| L004 | ERROR | all | FROM matches class (base via inline pin or `ARG VAST_BASE`) |
| L010 | ERROR | all w/ ROOT | conf.d ↔ script ↔ program-name triple + PROC_NAME |
| L011 | ERROR | all w/ ROOT | sourced utils are an ordered subsequence of canonical order |
| L020 | ERROR | pytorch-nested | torch-drift guard present |
| L021 | ERROR | pytorch-nested/external | no surviving `--torch-backend auto` |
| L022 | WARN | pytorch-nested | prefer `uv pip` over bare `pip install` |
| L030 | WARN | all | a `build-<name>.yml` workflow exists |

Documented, verified exceptions live in `EXCEPTIONS` in `imagegen/linter.py`
(currently `aio-studio` L004/L020 — custom base + per-app venvs).

## Not yet covered (deferred)
- CI **job-shape** parsing (5-job DAG, matrix, MATRIX_ID) — only existence (L030) so far.
- `PORTAL_CONFIG` port checks — intentionally **not** gated (`+10000` is not a real
  invariant; see invariants §3).
- docs⟷linter agreement test; class-sanity check; cross-file `exit_portal` label ↔
  PORTAL_CONFIG match (invariants §4).
