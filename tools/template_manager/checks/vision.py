"""Vision: the model behind /v1/chat/completions takes an image (ADR 0068).

A feature of the chat model, not a route: nothing in a template declares it, so the
caller names it with --features. Two results. The structural one shows the image reached
the model (the request is accepted and costs more prompt tokens than the same text
alone). The answer one shows the model read it (a known number drawn in the fixture).
"""

from __future__ import annotations

import base64
import os

from . import (ANSWER, FEATURE, OPENAI_ENGINE, STRUCTURAL, ResultName, TestType,
               _no_response_pair)

VISION_CHECK = "external/vision"
VISION_READ_CHECK = "external/vision-read"

# Drawn by fixtures/vision/generate.py: VISION_TOKEN in block digits, black on white,
# 448 x 224. Only its bytes are read here; no imaging library is needed at run time.
VISION_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "fixtures", "vision", "token.png")
VISION_TOKEN = "7319"

VISION_PROMPT = "Reply with one word."
VISION_READ_PROMPT = "What number is written in the image? Reply with the number only."
VISION_MAX_TOKENS = 8
VISION_READ_MAX_TOKENS = 64

# How many more prompt tokens the request with the image must cost than the same text
# alone. The point is that the image was tokenised, not dropped. An engine that drops
# it adds nothing, or at most the few tokens of a placeholder left in the prompt as
# text. An engine that encodes it adds one token per image patch group: hundreds for a
# fixed-grid encoder, and on the order of a hundred for a dynamic-resolution encoder at
# this fixture's size. 16 sits well above the first case and well below the second, so
# it does not depend on which encoder is used.
VISION_MIN_IMAGE_TOKENS = 16

# The read is asked at temperature 0 and retried this many times before it fails.
VISION_READ_RETRIES = 2


def _squash(text):
    """Lower case, with all whitespace removed."""
    return "".join(str(text).split()).lower()


def _assistant_message(r):
    """The assistant message of a chat response, or None."""
    choices = r.get("choices") if isinstance(r, dict) else None
    msg = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
    return msg if isinstance(msg, dict) and msg.get("role") == "assistant" else None


def _prompt_tokens(r):
    """usage.prompt_tokens of a chat response, or None when it is not reported."""
    usage = r.get("usage") if isinstance(r, dict) else None
    tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    return tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None


def _with_image(prompt, image_uri):
    return [{"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_uri}}]


def check_vision(endpoint, image=None):
    """Vision checks: one structural, one answer check.

    Structural: VISION_PROMPT is sent twice, alone and with the fixture attached as
    an `image_url` data URI. Both must return HTTP 200 with an assistant message, and
    the second must report at least VISION_MIN_IMAGE_TOKENS more `usage.prompt_tokens`
    than the first. A refusal of the image request (a 4xx) fails; so does a response
    without `usage`, because then nothing shows the image was used.
    Answer: the model is asked for the number in the image and its reply must contain
    VISION_TOKEN, ignoring case and whitespace.
    """
    def fail(detail):
        return [{"name": VISION_CHECK, "state": "failed", "kind": "structural", "detail": detail},
                {"name": VISION_READ_CHECK, "state": "skipped", "kind": "answer",
                 "detail": "structural check failed"}]

    def unanswered(what, body):
        return _no_response_pair(VISION_CHECK, VISION_READ_CHECK, what, body)

    if image is None:
        with open(VISION_FIXTURE, "rb") as f:
            image = f.read()
    image_uri = "data:image/png;base64," + base64.b64encode(image).decode()

    status, models = endpoint.call("/v1/models")
    if status == 0:
        return unanswered("/v1/models", models)
    if status != 200 or not isinstance(models, dict) or not models.get("data"):
        return fail(f"/v1/models returned HTTP {status}: {str(models)[:200]}")
    model = models["data"][0].get("id")

    def chat(content, max_tokens):
        return endpoint.call("/v1/chat/completions", {
            "model": model, "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens, "temperature": 0})

    tokens = {}
    for what, content in (("text-only", VISION_PROMPT),
                          ("image", _with_image(VISION_PROMPT, image_uri))):
        status, r = chat(content, VISION_MAX_TOKENS)
        if status == 0:
            return unanswered(f"chat ({what} request)", r)
        if status != 200:
            return fail(f"the {what} request returned HTTP {status}: {str(r)[:200]}")
        if _assistant_message(r) is None:
            return fail(f"no assistant message in the {what} response: {str(r)[:200]}")
        tokens[what] = _prompt_tokens(r)
        if tokens[what] is None:
            usage = r.get("usage") if isinstance(r, dict) else None
            return fail(f"the {what} response reports no usage.prompt_tokens (usage: {usage!r}), "
                        f"so it cannot be shown that the image was tokenised")
    added = tokens["image"] - tokens["text-only"]
    counts = (f"{tokens['text-only']} prompt tokens without the image, {tokens['image']} "
              f"with it")
    if added < VISION_MIN_IMAGE_TOKENS:
        return fail(f"the image added {added} prompt tokens (min {VISION_MIN_IMAGE_TOKENS}): "
                    f"{counts}; the image was not tokenised")
    structural = {"name": VISION_CHECK, "state": "passed", "kind": "structural",
                  "detail": f"model {model}, {counts}"}

    want, detail = _squash(VISION_TOKEN), ""
    attempts = 1 + VISION_READ_RETRIES
    for attempt in range(1, attempts + 1):
        status, r = chat(_with_image(VISION_READ_PROMPT, image_uri), VISION_READ_MAX_TOKENS)
        if status == 0:
            return [structural, {"name": VISION_READ_CHECK, "state": "error", "kind": "answer",
                                 "detail": f"chat (read request): no HTTP response ({str(r)[:200]})"}]
        msg = _assistant_message(r) if status == 200 else None
        if msg is None:
            detail = f"HTTP {status}: {str(r)[:160]}"
            continue
        reply = msg.get("content")
        if want in _squash(reply if reply is not None else ""):
            return [structural, {"name": VISION_READ_CHECK, "state": "passed", "kind": "answer",
                                 "detail": f"read {VISION_TOKEN!r} (attempt {attempt} of {attempts})"}]
        detail = f"reply {str(reply)[:120]!r}"
    return [structural, {"name": VISION_READ_CHECK, "state": "failed", "kind": "answer",
                         "detail": f"{VISION_TOKEN!r} not read in {attempts} attempts; last: {detail}"}]


def _run(ctx):
    return check_vision(ctx.endpoint)


TYPE = TestType(
    name="vision",
    axis=FEATURE,
    requires=("chat",),
    images=OPENAI_ENGINE,
    results=(ResultName(VISION_CHECK, STRUCTURAL, OPENAI_ENGINE, True),
             ResultName(VISION_READ_CHECK, ANSWER, OPENAI_ENGINE, True)),
    fixtures=(VISION_FIXTURE,),
    unrun=(VISION_CHECK, VISION_READ_CHECK),
    order=65,
    run=_run,
)
