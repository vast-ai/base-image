# ADR 0010 — Unify the launch template and the QA template

- **Status:** Accepted
- **Date:** 2026-07-03
- **Decision owner:** Rob Ballantyne
- **Process:** surfaced by the whole-feature design review of ADR 0009 (see its addendum) as
  the highest-value structural fix. Supersedes ADR 0009's deferral of the unify and adjusts
  the separate-`<name>-qa` convention of ADR 0005.

## Context

The generator scaffolded **two** templates per image: `templates/default/template.yml` (the
public launch template a user actually runs) and `templates/<name>-qa/template.yml` (the
live-GPU gate's target). In practice they were **near-duplicate files kept in sync by hand** —
compare a scaffolded image's two: identical ports, `PORTAL_CONFIG`, env, runtype, and `compute_cap`
floor; differing only in `image`, the `private`/`readme_visible` flags, and a comment.

The consequence undermines the gate's meaning: the QA gate proves the **`-qa`** template boots
and serves, but a user launches the **`default`** template — a *different file*. So "QA
passed" drifts from "the thing users launch passed," which is exactly the confidence ADR 0009
condition 1 ("live-green is a hypothesis") exists to protect. It also left the fleet
inconsistent — only newly-scaffolded images even had a `default`; the images actually gated
(comfyui, vllm) shipped only `-qa`.

## Options considered

- **Keep both + a linter rule to enforce they stay in sync.** Still two files, still
  hand-maintained; the rule can only check the fields it's told about, and the gate still
  isn't testing the *same object*. Rejected — it treats the symptom.
- **Derive `-qa` from `default` at build time.** Needless indirection; a generated file to
  keep coherent with its source. Rejected.
- **One template — the gate tests the launch template (chosen).** The launch spec lives once,
  in `templates/default/`; the QA gate boots *that*, overriding only what's inherently
  run-specific (the staging `image`/`tag`) at launch. The functional test is not in the
  template at all — it's the image's baked `ROOT/opt/instance-tools/tests/<name>.d/` (run by
  the in-instance test runner), or provisioning injected via the workflow's `extra_env`.

## Decision

**One template per image: `templates/default/template.yml`, and the QA gate boots it.**

- The generator emits only `templates/default/template.yml` (drops `templates/<name>-qa/`).
- `imagegen qa` and the `build-<name>.yml` `qa` job point `template_dir` at
  `templates/default`. `create.py --image/--tag` overrides the image to the freshly-built
  staging tag at publish time (a transient copy, created and deleted per run — the real
  recommended template is unaffected). The run-specific `private` flag is irrelevant for a
  throwaway QA copy.
- The functional test is the image's own baked tests (or `extra_env` provisioning), never a
  template field — so the *same* template validates and launches.

**Migration (the existing fleet is live on main):** `imagegen qa` and `_find_image_dir`
**prefer `templates/default/` and fall back to `templates/<name>-qa/`** for legacy images, so
comfyui/vllm keep working unchanged. Backfilling a `default` for comfyui/vllm/tabbyapi and
retiring their `-qa` (and their build workflows' `template_dir`) is a follow-up, done per
image, not in this change.

## Consequences

- **"QA passed" now means "the template users launch passed"** — the entire point of a live
  gate. One source of truth for the launch spec; the drift risk is gone.
- One fewer file per image, one fewer thing to keep in sync, and the `qa-fix` fix-surface no
  longer points at a `default` that may not exist.
- Mixed state during migration (new images: `default`; legacy: `-qa`) is handled by the
  prefer-default/fallback-`-qa` resolution; it must not linger — the backfill is tracked.

## What would reverse this

- If a future need genuinely requires the gate to test a materially different config than the
  launch template (not just image/tag/private), that's a real second template — revisit then.
  Overriding image/tag/private at publish covers every case seen so far.

## Amendment (2026-07-07) — the QA gate supplies the VRAM floor

Unifying launch + QA into one template exposed a conflict for **multi-model hosts**: the
launch template must leave the VRAM floor **unset** (the user picks the model, so the image
must not over-constrain the rentable box — invariants §7), but the QA run still needs its
*test* model to fit the cheapest box that meets `compute_cap`. One template can't do both.

**Decision:** the QA gate supplies the VRAM floor at rent time, exactly as it already
overrides `image`/`tag`. `imagegen qa --min-vram <GB>` injects `gpu_total_ram: {gte: <GB×1024>}`
into the throwaway QA copy's `extra_filters` (never lowering a floor the template already
declares) before publish; `test_template.py`'s offer search already reads that floor. So:

- **single fixed/provisioned-model image** → the *launch* template sets a VRAM floor sized to
  its model (invariants §7); QA inherits it, no `--min-vram` needed.
- **multi-model host** → launch template leaves VRAM unset; QA passes `--min-vram` sized to the
  functional test's model. The launch template stays unconstrained; the gate still fits the box.

L054 gates the *format* of any VRAM floor that is set (valid key + numeric); presence/sizing
stays human judgment. The legacy `vllm-qa` / `comfyui-qa` templates that hand-set
`gpu_total_ram: 8192` are the pre-amendment manual form of this and fold into `--min-vram` once
migrated to the unified template.
