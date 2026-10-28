# Unsloth GRPO (RL)
> **[Create an Instance](<<LAUNCH_LINK>>)**

## What is this template?

Train a reasoning model with reinforcement learning (GRPO) on a single GPU. GRPO is the method behind DeepSeek-R1: for each question the model writes several answers, a scoring function grades them, and training pushes the model toward the better ones. It needs only questions with checkable answers, not worked solutions.

The template runs [Unsloth](https://unsloth.ai) with vLLM for fast answer generation. The default run teaches **Qwen3-1.7B** grade-school math (GSM8K) in 8 to 16 minutes on a 24 GB card.

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
Open the **Instance Portal** (port 1111) and click **Jupyter**.

### Step 3: Train
Open `/workspace/grpo/grpo_quickstart.ipynb` and run the cells in order. Or, from a terminal:

```bash
cd /workspace/grpo
python train_grpo.py                                   # defaults: Qwen3-1.7B, GSM8K, 100 steps
python train_grpo.py --model unsloth/Qwen3-4B-Base --dataset dapo-math --max-steps 300
python train_grpo.py --help                            # every option
```

Every option is also an environment variable (`GRPO_MODEL`, `GRPO_MAX_STEPS`, ...). Set `GRPO_AUTORUN=1` to start a run as soon as the instance boots; the log is `/workspace/grpo/autorun.log`.

Each run writes to `/workspace/grpo-runs/<timestamp>/`: the LoRA adapter, `results.json` (accuracy before and after on held-out questions, time, peak memory, cost) and the training log. Model downloads are cached in `/workspace/.cache/huggingface`.

### Expected results

Default settings, 100 steps, accuracy on 200 held-out GSM8K questions, one run per card:

| GPU | Accuracy before → after | Total time |
|---|---|---|
| RTX 5090 | 53.5% → 65% | 8 min |
| RTX 4090 | 54% → 64% | 10 min |
| RTX 3090 | 51.5% → 62% | 13 min |
| RTX A6000 | 55% → 64% | 16 min |

vLLM reserves 90% of the card's memory for generation, so `nvidia-smi` shows the card nearly full; lower `--gpu-memory-utilization` if you need room for something else.

Your runs are in `/workspace`, which is lost when the instance is destroyed unless you attach a volume. Download or upload anything you want to keep.

---

## Key Features

| Access | What for |
|---|---|
| Jupyter (via the Instance Portal) | The notebook, a terminal, file browser |
| SSH | Scripted runs, `train_grpo.py` |
| Instance Portal (port 1111) | Links to every service, logs |

---

## Ports

| External | Internal | Service |
|---|---|---|
| 1111 | 11111 | Instance Portal |
| 8080 | 18080 | Jupyter (path `/`), Jupyter Terminal (`/terminals/1`) |

---

## Licenses

This template ships vendor application(s) under the following license(s):

- **Unsloth**: Apache-2.0 ([upstream](https://github.com/unslothai/unsloth))
- **vLLM**: Apache-2.0 ([upstream](https://github.com/vllm-project/vllm))
- **TRL**: Apache-2.0 ([upstream](https://github.com/huggingface/trl))

See `/LICENSES.md` in the image for license details and file locations.

## Need More Help?

- **Unsloth RL guide:** [docs](https://unsloth.ai/docs/get-started/reinforcement-learning-rl-guide)
- **Base Image Features:** [GitHub Repository](https://github.com/vast-ai/base-image/)
- **Instance Portal Guide:** [Vast.ai Instance Portal Documentation](https://docs.vast.ai/instance-portal)
- **Template Configuration:** [Vast.ai Template Guide](https://docs.vast.ai/templates)
- **Support:** Use the messaging icon in the Vast.ai console
