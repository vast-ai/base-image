# Lint rules (generated)

> Generated from `tools/imagegen/imagegen/linter.py` (`RULES`). Do not edit by
> hand — run `imagegen rules > docs/lint-rules.md`. This is the authoritative
> rule list; `CONTRIBUTING.md` / `.github/AGENTS.md` must not contradict it.

| Code | Severity | Rule |
|---|---|---|
| L001 | ERROR | Exactly 3 LABEL key=value pairs, including the required keys |
| L002 | ERROR | `env-hash > /.env_hash` is the final RUN (executed shell, not heredoc data) |
| L003 | ERROR | A local `COPY ./ROOT /` is present |
| L004 | ERROR | FROM matches the declared class — structural base identity (registry+repo), incl. external stage order |
| L005 | ERROR | App base FROM is a CONCRETE pin — a dated tag or a digest, not `latest` and not untagged — so a rebuild can't jump to an untested base (pytorch-nested & derivative; ADR 0013). base-image/pytorch may float |
| L010 | ERROR | Each [program:NAME]: PROC_NAME + command=/opt/supervisor-scripts/NAME.sh; file stem is a program |
| L011 | ERROR | Sourced utils appear as an ordered subsequence of the canonical order |
| L020 | ERROR | torch-drift guard: a pre==post comparison wired to an exit on the same statement |
| L021 | ERROR | No `--torch-backend auto` except inside a real sed substitution |
| L022 | WARN | Prefer `uv pip install` over bare `pip install` |
| L030 | WARN | A build-<name>.yml workflow exists (not universal) |
| L040 | ERROR | No unfilled generator skeleton markers (CHANGEME / CHANGEPORT / >>> FILL) |
| L041 | ERROR | No hardcoded staging namespace in a new image's committed files — reference the DOCKERHUB_NAMESPACE_STAGING secret |
| L050 | ERROR | A shipped template.yml declares a compute_cap floor in extra_filters (ADR 0005) |
| L051 | ERROR | Supervisor launch scripts (ROOT/opt/supervisor-scripts/*.sh) are executable — the .conf execs them directly |
| L052 | ERROR | A shipped templates/*/README.md launch link uses the <<LAUNCH_LINK>> placeholder, not a hardcoded cloud.vast.ai ref link (ADR 0011) |
| L053 | ERROR | No baked model weights in a Dockerfile RUN — models arrive at runtime via provisioning / <APP>_MODEL (invariants §6) |
| L054 | ERROR | A template's VRAM floor, IF set, uses a valid key (gpu_ram / gpu_total_ram, MB) with a numeric value — presence is optional (multi-model hosts omit it; qa supplies it) |
| L055 | ERROR | External images set ENV TCLLIBPATH=/usr/lib/tcltk/default (they FROM upstream, not our base, so don't inherit it) — else the pty helper's unbuffer/Expect fails and the app launch dies at boot |
| L056 | ERROR | An image that source-builds Unsloth Studio's llama.cpp (`unsloth studio setup`) MUST carry a real post-build file-existence assertion for the CUDA backend (`test -f …libggml-cuda.so`; a bare mention of the name does not count) — setup.sh gates -DGGML_CUDA=ON on a runtime GPU probe absent in `docker build`, so without the assert it silently ships a CPU-only binary and every inference runs on CPU (ADR 0016) |
| L060 | ERROR | No credential-shaped secret committed in docs/adr/** — this repo is public; sensitive specifics live in the internal tracker, not the ADR (ADR 0012) |
| L061 | ERROR | No internal tracker ticket id (CON-/HOST-/CLN-…) in any public-repo file — it leaks the internal tracker and dangles for external readers; the internal issue links to the ADR/commit, not the reverse (ADR 0012) |
