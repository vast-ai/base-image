# ComfyUI QA template — SD1.5 smoke

The template the [live-GPU QA gate](../../../../../../docs/adr/0005-live-gpu-qa-gate.md)
launches to verify a freshly-built `vastai/comfyui` image on real hardware before
promotion. It is **not** a user-facing template — it is disposable and private.

## What it exercises

`comfyui.d/10-comfyui-serving.sh` waits for ComfyUI + the API wrapper, then POSTs
every provisioned workflow to `/generate/sync` and requires a real output. To make
that a genuine test (and not a skip-as-pass), this template provisions one real
workflow + model:

- **Workflow:** [`sd15-txt2img.json`](sd15-txt2img.json) — a standard SD1.5 512×512
  text-to-image graph. `PROVISIONING_COMFYUI_WORKFLOWS` points the
  `provisioner_comfyui` extension at it; the extension auto-discovers the checkpoint
  from the workflow's `properties.models[]`, downloads it, and
  `convert-workflows.sh` turns it into an API payload.
- **Model:** `v1-5-pruned-emaonly-fp16.safetensors` (2.1 GB) from the Comfy-Org
  archive, embedded in the workflow's checkpoint node.

## Launch mode

The template mirrors the **production** ComfyUI template
(`vast_landing/.../recommended_templates/160-comfyui`) — `runtype: jupyter`, the full
`PORTAL_CONFIG`, the production `COMFYUI_ARGS`, `COMFYUI_API_BASE` direct-to-ComfyUI,
the jupyter/ssh-direct flags — so QA exercises the real user launch path, not an
entrypoint-mode approximation. QA-only deltas: `private`, the SD1.5 smoke workflow
(prod ships SDXL-Turbo), and the selection floors below.

## Floors (ADR 0005)

`extra_filters` declares the gate's selection floors: `compute_cap >= 750` (sm_75,
matching production), `cuda_max_good >= 13.2` (host driver must satisfy the newer
matrix variant), and `gpu_total_ram >= 8192` (8 GB). The gate picks the smallest
viable amd64 box at/above these, bounded at 2× VRAM — so it smokes an ~8–16 GB box,
generalising upward.

## CI overrides

`create.py --tag <staging>` repoints `tag` at the image under test. The workflow URL
default resolves once this lands on `main`; for a pre-merge run the gate passes
`test_template.py --env PROVISIONING_COMFYUI_WORKFLOWS=<raw URL on the ref under test>`.
