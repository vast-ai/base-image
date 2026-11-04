# Tasks

A task tells `train_grpo.py` what to train on: the data, how a prompt looks, and how an answer is scored. The trainer does everything else (model loading, GRPO, before/after scoring, saving, `results.json`).

Built in:

| Task | File | What it teaches |
|------|------|-----------------|
| `math` (default) | `math_task.py` | Grade-school math word problems (GSM8K, or `--dataset dapo-math`) |
| `toolcall` | `toolcall_task.py` | Answer a request with the right JSON function calls (xLAM) |

## Run a task

```bash
python train_grpo.py --task toolcall                        # built-in
python train_grpo.py --task ./my_task.py                    # a file on the instance
python train_grpo.py --task https://example.com/my_task.py  # downloaded at start
```

`--task` is also the `GRPO_TASK` environment variable, so an agent can rent an instance with `GRPO_TASK=<url>` and `GRPO_AUTORUN=1` and never log in. Each run copies its task file to `<run>/task.py` and records where it came from (`task_source`, `task_sha256` in `results.json`).

A task URL is code that runs on your instance. Only use URLs you trust.

## Write a task

Copy `toolcall_task.py` and change it. A task file is a plain Python file with four functions:

```python
def load(split, settings):
    """Rows for split "train" or "test", as a Hugging Face Dataset. Each row needs an
    "answer" column; keep whatever else prompt() needs. settings holds every
    train_grpo.py setting (settings["dataset"], settings["seed"], ...)."""

def prompt(row):
    """The chat messages for one row: [{"role": "system", ...}, {"role": "user", ...}]."""

def rewards(tokenizer):
    """A list of reward functions in TRL's format:
        def my_reward(completions, answer, **kwargs) -> list[float]
    completions[i][0]["content"] is the model's i-th reply, answer[i] its row's answer.
    Other dataset columns arrive as keyword arguments. The rewards are added together."""

def check(reply, answer):
    """Is this reply right? Used for the before/after score on held-out rows. Return a
    bool, or a dict with "correct" plus anything else to record; numeric and bool fields
    are averaged into results.json (e.g. "valid_json" -> valid_json_before/after)."""
```

Optional settings in the same file:

| Name | What it does |
|------|--------------|
| `CHAT_TEMPLATE` | A Jinja chat template. Base models need one; both built-in tasks end it with the tag the answer should start with |
| `STOP` | Extra text that ends a reply (the end-of-text token always does), e.g. `["</tool_call>"]` |
| `MAX_ANSWER_TOKENS` | Cap on reply length, for short answers |
| `DATASET` | Name recorded in `results.json` |
| `warmup_reply(row)` | A worked answer, for `--warmup-sft-steps` |

A task can only use libraries already in the image (`datasets`, `json`, `re`, and so on). Install anything else first with `pip install`.

## Rewards that work

- **Give partial credit.** If every answer to a prompt gets the same score, GRPO learns nothing from it. Reward a valid format and a partly right answer, not only a fully right one.
- **Watch `frac_reward_zero_std`** in `log_history.json`. If it stays near 1.0, the rewards are too strict for the starting model.
- **Check the starting score.** `accuracy_before` near 0% usually means the model can't follow the answer format. Make the format easier to hit (a stop string, a template that starts the answer for it) before training longer.
- **Use a program, not a judge.** Exact checks (a number, a JSON match, a test passing) are fast and can't be gamed by persuasive text.
