# Unsloth GRPO (RL)

Template README for **Unsloth GRPO (RL)**: Unsloth's official image (`unsloth/unsloth`) in Vast's Jupyter launch mode, with a GRPO recipe fetched at boot by `setup.sh` in this folder.

Template onstart:

```bash
export GRPO_SRC=https://raw.githubusercontent.com/vast-ai/base-image/<commit>/provisioning_scripts/unsloth-grpo
curl -fsSL --retry 3 -o /tmp/grpo-setup.sh "$GRPO_SRC/setup.sh" && bash /tmp/grpo-setup.sh \
  || echo "GRPO setup failed; see the onstart log" > /workspace/GRPO_SETUP_FAILED
```

Everything below is the README users see.

---

# Unsloth GRPO (RL)
> **[Create an Instance](<<LAUNCH_LINK>>)**

## What is this template?

Train a reasoning model with reinforcement learning (GRPO) on a single GPU. GRPO is the method behind DeepSeek-R1: for each question the model writes several answers, a scoring function grades them, and training pushes the model toward the better ones. It needs only questions with checkable answers, not worked solutions.

The template runs Unsloth's own Docker image ([`unsloth/unsloth`](https://hub.docker.com/r/unsloth/unsloth)), with vLLM for fast answer generation. The default run teaches **Qwen3-1.7B** grade-school math (GSM8K) in 8 to 16 minutes on a 24 GB card.

---

## What can I do with this?

- Run GRPO from a notebook: change the settings, run the cells, watch the reward climb, try the trained model.
- Run the same recipe as a script over SSH, or from an agent, with every setting as a flag or environment variable.
- Swap in a bigger model (Qwen3-4B or 8B) or a harder dataset (competition math).
- Keep the LoRA adapter, save a merged model, or upload it to Hugging Face.

---

## Quick Start Guide

### Step 1: Launch
Click **"[Rent](<<LAUNCH_LINK>>)"** on a 24 GB or larger card (RTX 3090, 4090, 5090, A6000).

### Step 2: Open Jupyter
Click **Open** on the instance card.

### Step 3: Train
Open `/workspace/grpo/grpo_quickstart.ipynb` and run the cells in order. Or, from a terminal or SSH:

```bash
cd /workspace/grpo
python train_grpo.py                                   # defaults: Qwen3-1.7B, GSM8K, 100 steps
python train_grpo.py --model unsloth/Qwen3-4B-Base --dataset dapo-math --max-steps 300
python train_grpo.py --help                            # every option
```

Every option is also an environment variable (`GRPO_MODEL`, `GRPO_MAX_STEPS`, ...). Set `GRPO_AUTORUN=1` to start a run as soon as the instance boots (add flags with `GRPO_ARGS`); the log is `/workspace/grpo/autorun.log` and the exit code lands in `/workspace/grpo/autorun.exit`.

Each run writes to `/workspace/grpo-runs/<timestamp>/`: the LoRA adapter, `results.json` (accuracy before and after on held-out questions, time, peak memory, cost) and the training log.

### Expected results

Default settings, 100 steps, accuracy on 200 held-out GSM8K questions, one run per card (October 2026):

| GPU | Accuracy before → after | Total time |
|---|---|---|
| RTX 5090 | 53.5% → 65% | 8 min |
| RTX 4090 | 54% → 64% | 10 min |
| RTX 3090 | 51.5% → 62% | 13 min |
| RTX A6000 | 55% → 64% | 16 min |

vLLM reserves 90% of the card's memory for generation, so `nvidia-smi` shows the card nearly full; lower `--gpu-memory-utilization` if you need room for something else.

Your runs are in `/workspace`, which is lost when the instance is destroyed unless you attach a volume. Download or upload anything you want to keep.

---

## Access

| Access | What for |
|---|---|
| Jupyter (the **Open** button) | The notebook, a terminal, file browser |
| SSH | Scripted runs, `train_grpo.py` |

---

## Licenses

This template runs Unsloth's image unmodified. Unsloth's components are under more than one license, and some of them are copyleft:

- **Unsloth** (`unsloth`): the package metadata says Apache-2.0, but many files in it carry **AGPL-3.0** headers, including the MoE grouped-GEMM kernels (`unsloth/kernels/moe/`, with their own AGPL `LICENSE`), several model loaders under `unsloth/models/`, and the prefix-grouper used in RL (`unsloth/utils/prefix_grouper*.py`). Some kernels and utilities carry **LGPL-3.0** headers (for example `unsloth/kernels/rope_embedding.py`, `unsloth/utils/packing.py`). The repository ships both `LICENSE` (Apache-2.0) and `COPYING` (AGPL-3.0). Check the header of any file you modify or redistribute. [Upstream](https://github.com/unslothai/unsloth)
- **Unsloth Zoo** (`unsloth_zoo`): **LGPL-3.0-or-later**. [Upstream](https://github.com/unslothai/unsloth-zoo)
- **Unsloth Studio** (in the image, not started by this template): **AGPL-3.0**.
- **vLLM**: Apache-2.0. [Upstream](https://github.com/vllm-project/vllm)
- **TRL**: Apache-2.0. [Upstream](https://github.com/huggingface/trl)

If you serve a modified AGPL-3.0 component over a network, AGPL-3.0 requires you to offer its source to those users. Model weights you download keep their own licenses (Qwen3: Apache-2.0).

## Need More Help?

- **Unsloth RL guide:** [docs](https://unsloth.ai/docs/get-started/reinforcement-learning-rl-guide)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console
