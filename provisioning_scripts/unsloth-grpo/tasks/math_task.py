"""Math word problems: the model shows its working, then gives a final number, and is
rewarded for the right format and the right number.

Built-in task "math" (the default). Datasets, picked with --dataset:
    gsm8k      grade-school math (openai/gsm8k, MIT), fast gains on small models
    dapo-math  harder competition math (open-r1/DAPO-Math-17k-Processed, Apache-2.0)
"""

import re

REASONING_START = "<start_working_out>"
REASONING_END = "<end_working_out>"
SOLUTION_START = "<SOLUTION>"
SOLUTION_END = "</SOLUTION>"
SYSTEM_PROMPT = (
    "You are given a problem.\n"
    "Think about the problem and provide your working out.\n"
    f"Place it between {REASONING_START} and {REASONING_END}.\n"
    f"Then, provide your solution between {SOLUTION_START}{SOLUTION_END}"
)

# Unsloth's GRPO recipe: the prompt ends with the reasoning tag, so the model always
# starts by thinking.
CHAT_TEMPLATE = (
    "{% if messages[0]['role'] == 'system' %}"
    "{{ messages[0]['content'] + eos_token }}"
    "{% set loop_messages = messages[1:] %}"
    "{% else %}"
    f"{{{{ '{SYSTEM_PROMPT}' + eos_token }}}}"
    "{% set loop_messages = messages %}"
    "{% endif %}"
    "{% for message in loop_messages %}"
    "{% if message['role'] == 'user' %}{{ message['content'] }}"
    "{% elif message['role'] == 'assistant' %}{{ message['content'] + eos_token }}"
    "{% endif %}{% endfor %}"
    f"{{% if add_generation_prompt %}}{{{{ '{REASONING_START}' }}}}{{% endif %}}"
)

DATASET = "gsm8k"
DATASETS = ("gsm8k", "dapo-math")
_NUMBER = re.compile(SOLUTION_START + r".*?([-]?[\d\.\,]+)", flags=re.MULTILINE | re.DOTALL)


def load(split, settings):
    name = settings.get("dataset") or DATASET
    if name not in DATASETS:
        raise ValueError(f"math task: --dataset must be one of {DATASETS}, got {name!r}")
    from datasets import load_dataset

    if name == "gsm8k":
        ds = load_dataset("openai/gsm8k", "main", split=split)
        return ds.map(lambda x: {
            "question": x["question"],
            "reasoning": x["answer"].split("####")[0].strip(),
            "answer": x["answer"].split("####")[-1].strip().replace(",", ""),
        }, remove_columns=ds.column_names)
    # dapo-math has no test split and no worked solutions; hold out the tail for eval.
    ds = load_dataset("open-r1/DAPO-Math-17k-Processed", "en", split="train")
    ds = ds.select(range(len(ds) - 500, len(ds))) if split == "test" else ds.select(range(len(ds) - 500))
    return ds.map(lambda x: {"question": x["prompt"], "reasoning": "", "answer": x["solution"].strip()},
                  remove_columns=ds.column_names)


def prompt(row):
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": row["question"]}]


def _final_number(text):
    found = _NUMBER.search(text)
    if not found:
        return None, None
    raw = found.group(1)
    try:
        return raw, float(raw.replace(",", "").rstrip("."))
    except ValueError:
        return raw, None


def check(reply, answer):
    raw, value = _final_number(reply)
    return {"correct": value is not None and abs(value - float(answer)) < 1e-6, "extracted": raw}


def rewards(tokenizer):
    match_format = re.compile(
        rf"{REASONING_END}.*?{SOLUTION_START}(.+?){SOLUTION_END}[\s]*"
        rf"(?:{re.escape(tokenizer.eos_token)})?[\s]*$",
        flags=re.MULTILINE | re.DOTALL,
    )

    def format_exact(completions, **kwargs):
        """+3 when the answer follows the format exactly."""
        return [3.0 if match_format.search(c[0]["content"]) else 0.0 for c in completions]

    def format_partial(completions, **kwargs):
        """+0.5 per tag used exactly once, -1 otherwise."""
        return [sum(0.5 if c[0]["content"].count(tag) == 1 else -1.0
                    for tag in (REASONING_END, SOLUTION_START, SOLUTION_END))
                for c in completions]

    def correct_answer(completions, answer, **kwargs):
        """+5 for the right number, -1.5 for a wrong one, -2.5 for no answer."""
        scores = []
        for c, truth in zip(completions, answer):
            raw, value = _final_number(c[0]["content"])
            if raw is None:
                scores.append(-2.5)
            elif value is None:
                scores.append(0.0)
            else:
                scores.append(5.0 if abs(value - float(truth)) < 1e-6 else -1.5)
        return scores

    return [format_exact, format_partial, correct_answer]


def warmup_reply(row):
    """A worked answer for --warmup-sft-steps (gsm8k only; dapo-math has no solutions)."""
    if not row.get("reasoning"):
        return None
    return f"{REASONING_START}{row['reasoning']}{REASONING_END}{SOLUTION_START}{row['answer']}{SOLUTION_END}"
