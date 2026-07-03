# imagegen

Generator + static invariant-linter + live-GPU QA loop for base-image Docker images.
Scope and rationale: [docs/adr/0001](../../docs/adr/0001-image-scaffolding-tooling.md)
(scaffold+lint) and [docs/adr/0009](../../docs/adr/0009-self-healing-qa-fix-loop.md)
(QA-fix loop); verified rules: [docs/invariants.md](../../docs/invariants.md).

**The linter is a FAST gate, not a correctness gate.** It checks file *shape* with
no build/GPU/registry. The real `docker build` (+ smoke test) is the correctness
check — green lint means "ready to build", never "correct" (ADR 0001 condition 1).

## Scaffold a new image

The generator emits the mechanical ~80% per class with fenced `>>> FILL: ... <<<`
markers for the judgment residue (install steps, app launch, base tag). **You pick
the class** — the generator never guesses it.

```bash
# from the repo root
PYTHONPATH=tools/imagegen python3 -m imagegen.cli new \
  --class pytorch-nested --name myapp --label "My App" --port 7860
# external images also need --upstream <image:tag>
```

It scaffolds the Dockerfile, ROOT/ overlay (supervisor script + .conf + capability
+ agent doc), README, and a CI skeleton, then lints the result. Output passes the
linter by construction (see `tests/test_generate.py` round-trip). Then: fill the
FILL markers, replace the `CHANGEME` base tag, `lint`, and run the real
`docker build` (the correctness gate).

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
directly. `new` and `lint` are pure-stdlib — no venv or deps needed, any Python 3.12.

## Live-GPU QA + fix loop (`imagegen qa`)

`imagegen qa <image>` rents a real GPU, boots the image from its `templates/<name>-qa`
template, runs the in-instance functional test, and — on a real failure — HOLDS the box so
the `qa-fix` skill can diagnose against it (human-gated). See
[docs/adr/0009](../../docs/adr/0009-self-healing-qa-fix-loop.md).

Unlike `new`/`lint`, `qa` shells `tools/template_manager` (`create.py`, `test_template.py`),
which need third-party deps **and** QA-account credentials.

**Setup (once)** — a project interpreter with the deps (this host ships no system `pip`):
```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r tools/template_manager/requirements.txt
```

**Credentials — `.env` at the REPO ROOT** (gitignored; the canonical location, also read by
`create.py`):
```
VAST_API_KEY=<QA-account key>              # the dedicated QA Vast account, NOT a personal one
DOCKERHUB_NAMESPACE_STAGING=<staging ns>   # only when --tag is a bare tag / omitted
# HF_TOKEN=<token>                         # only if the image pulls gated model weights
```

**Run** — use the venv interpreter (the launcher shells the tools via `sys.executable`, so
they inherit its env + deps):
```bash
PYTHONPATH=tools/imagegen .venv/bin/python -m imagegen.cli qa chatterbox \
  --tag robatvastai/chatterbox:latest        # full ref, or a bare tag against the staging ns

# tear down a box that was held for diagnosis, when done:
PYTHONPATH=tools/imagegen .venv/bin/python -m imagegen.cli qa-teardown chatterbox
```

The box is held only on a real functional failure (exit 1); every other verdict (pass /
no_offers / config_error / …) tears it down. A teardown ledger + the label-scoped scheduled
reaper are the money-safety backstops.

## Rules reference (single source of truth)

`RULES` in `imagegen/linter.py` is authoritative. `docs/lint-rules.md` is **generated**
from it — regenerate after changing checks:

```bash
PYTHONPATH=tools/imagegen python3 -m imagegen.cli rules > docs/lint-rules.md
```

Tests fail if the catalog, the codes the checks actually emit, and the generated doc
drift apart — so the docs can't silently disagree with the enforced rules.

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
