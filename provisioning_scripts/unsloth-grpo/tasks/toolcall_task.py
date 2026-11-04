"""Tool calling: answer a request with the right function calls, as JSON.

Built-in task "toolcall". Each prompt lists a few tools (name, description, parameters)
and a user request. The model must reply with a JSON list of calls, e.g.
    <tool_call>[{"name": "get_weather", "arguments": {"city": "Paris"}}]</tool_call>
A reply is correct when its calls match the reference exactly (same functions, same
arguments, any order). That check is the reward, so no judge model is needed.

Data: the 60k xLAM rows of argilla/apigen-function-calling (CC-BY-4.0, ungated).
"""

import json

DATASET = "argilla/apigen-function-calling"
CALL_START = "<tool_call>"
CALL_END = "</tool_call>"
SYSTEM_PROMPT = (
    "You can call these tools:\n{tools}\n\n"
    "Answer the user's request with the tool calls that fulfill it, as a JSON list of "
    '{{"name": ..., "arguments": {{...}}}} objects, between ' + CALL_START + " and "
    + CALL_END + ". Output nothing else."
)
EVAL_HOLDOUT = 1000

# Plain template for a base model; the reply is pre-filled with the opening tag.
CHAT_TEMPLATE = (
    "{% for message in messages %}"
    "{% if message['role'] == 'system' %}{{ message['content'] + eos_token }}"
    "{% elif message['role'] == 'user' %}{{ 'Request: ' + message['content'] + '\n' }}"
    "{% elif message['role'] == 'assistant' %}{{ message['content'] + eos_token }}"
    "{% endif %}{% endfor %}"
    f"{{% if add_generation_prompt %}}{{{{ '{CALL_START}' }}}}{{% endif %}}"
)
STOP = [CALL_END]
MAX_ANSWER_TOKENS = 512


def load(split, settings):
    """xLAM rows with parseable tools and answers. The last EVAL_HOLDOUT rows (after a
    fixed shuffle) are the test split."""
    from datasets import load_dataset

    ds = load_dataset(DATASET, split="train").filter(lambda x: x["origin"] == "xLAM")
    ds = ds.shuffle(seed=1234)

    def ok(x):
        try:
            calls = json.loads(x["answers"])
            json.loads(x["tools"])
        except (TypeError, ValueError):
            return False
        return isinstance(calls, list) and len(calls) > 0

    ds = ds.filter(ok)
    n = len(ds)
    ds = ds.select(range(n - EVAL_HOLDOUT, n)) if split == "test" else ds.select(range(n - EVAL_HOLDOUT))
    return ds.map(lambda x: {"question": x["query"], "tools": x["tools"], "answer": x["answers"]},
                  remove_columns=ds.column_names)


def prompt(row):
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(tools=row["tools"])},
        {"role": "user", "content": row["question"]},
    ]


# ---------------------------------------------------------------- scoring

_DECODER = json.JSONDecoder()


def parse_reply(text, eos_token=""):
    """(calls, clean): the JSON call list the reply starts with (None if it doesn't start
    with a list of {"name": str, "arguments": dict} objects), and whether nothing but the
    closing tag follows it. The opening tag is in the prompt."""
    body = text.lstrip()
    try:
        calls, end = _DECODER.raw_decode(body)
    except ValueError:
        return None, False
    if not isinstance(calls, list) or not all(
        isinstance(c, dict) and isinstance(c.get("name"), str)
        and isinstance(c.get("arguments", {}), dict) for c in calls
    ):
        return None, False
    rest = body[end:].strip()
    if eos_token:
        rest = rest.removesuffix(eos_token).strip()
    return calls, rest in ("", CALL_END)


def _norm(value):
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return value


def _key(call):
    return json.dumps({"name": call["name"], "arguments": _norm(call.get("arguments", {}))},
                      sort_keys=True)


def calls_match(predicted, reference):
    return sorted(map(_key, predicted)) == sorted(map(_key, reference))


def names_overlap(predicted, reference):
    """Share of reference calls whose function name appears in the prediction (multiset)."""
    left = [c["name"] for c in predicted]
    hits = 0
    for c in reference:
        if c["name"] in left:
            left.remove(c["name"])
            hits += 1
    return hits / len(reference)


def check(reply, answer):
    calls = parse_reply(reply)[0]
    return {"correct": calls is not None and calls_match(calls, json.loads(answer)),
            "valid_json": calls is not None}


def rewards(tokenizer):
    eos_token = tokenizer.eos_token

    def valid_json(completions, **kwargs):
        """+1 for a call list that ends cleanly at the closing tag, +0.5 for a call list
        followed by other text, -1 for no call list."""
        scores = []
        for c in completions:
            calls, clean = parse_reply(c[0]["content"], eos_token)
            scores.append(-1.0 if calls is None else 1.0 if clean else 0.5)
        return scores

    def right_functions(completions, answer, **kwargs):
        """Up to +2 for calling the right functions, -0.5 per extra call."""
        scores = []
        for c, truth in zip(completions, answer):
            calls, ref = parse_reply(c[0]["content"])[0], json.loads(truth)
            if calls is None:
                scores.append(0.0)
                continue
            extra = max(0, len(calls) - len(ref))
            scores.append(2.0 * names_overlap(calls, ref) - 0.5 * extra)
        return scores

    def exact_match(completions, answer, **kwargs):
        """+5 when the calls match the reference exactly, -1 when they don't."""
        scores = []
        for c, truth in zip(completions, answer):
            calls = parse_reply(c[0]["content"])[0]
            if calls is None:
                scores.append(0.0)
            else:
                scores.append(5.0 if calls_match(calls, json.loads(truth)) else -1.0)
        return scores

    return [valid_json, right_functions, exact_match]
