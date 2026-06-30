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
| L010 | ERROR | Each [program:NAME]: PROC_NAME + command=/opt/supervisor-scripts/NAME.sh; file stem is a program |
| L011 | ERROR | Sourced utils appear as an ordered subsequence of the canonical order |
| L020 | ERROR | torch-drift guard: a pre==post comparison wired to an exit on the same statement |
| L021 | ERROR | No `--torch-backend auto` except inside a real sed substitution |
| L022 | WARN | Prefer `uv pip install` over bare `pip install` |
| L030 | WARN | A build-<name>.yml workflow exists (not universal) |
| L040 | ERROR | No unfilled generator skeleton markers (CHANGEME / CHANGEPORT / >>> FILL) |
| L050 | ERROR | A shipped template.yml declares a compute_cap floor in extra_filters (ADR 0005) |
