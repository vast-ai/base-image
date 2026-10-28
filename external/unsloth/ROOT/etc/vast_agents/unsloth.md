# Unsloth

Unsloth's official image (PyTorch, Unsloth, TRL and vLLM at matching versions) with the
Vast tooling on top. The Python environment is `/venv/main`, a mirror of the upstream
`/opt/unsloth-venv`; Jupyter's default kernel uses it.

## GRPO (reinforcement learning)

- Files: `/workspace/grpo/train_grpo.py` and `/workspace/grpo/grpo_quickstart.ipynb`.
- Run: `cd /workspace/grpo && python train_grpo.py --help`. Every flag is also a `GRPO_*`
  env var (`--max-steps` is `GRPO_MAX_STEPS`), so a template or `-e` flags can set it.
- Unattended: launch with `GRPO_AUTORUN=1`; supervisor program `grpo-autorun` runs
  `train_grpo.py` once per instance (extra flags in `GRPO_ARGS`). Log:
  `/workspace/grpo/autorun.log` and `/var/log/portal/grpo-autorun.log`.
- Output: `/workspace/grpo-runs/<timestamp>/` (or `GRPO_OUTPUT_DIR`) with `lora/`,
  checkpoints, `log_history.json` and `results.json` (accuracy before and after, run time,
  peak GPU memory, cost when `GRPO_PRICE_PER_HOUR` is set). Poll for `results.json`.
- Memory: vLLM reserves `GRPO_GPU_MEMORY_UTILIZATION` (default 0.9) of the card for
  generation. Lower it, `GRPO_NUM_GENERATIONS` or `GRPO_MAX_SEQ_LENGTH` on out-of-memory.

## Models and caches

Weights download at run time to `HF_HOME` (`/workspace/.cache/huggingface`); none are baked
in. Gated models need `HF_TOKEN`.
