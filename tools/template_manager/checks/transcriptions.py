"""Transcriptions: the template serves /v1/audio/transcriptions (speech to text)."""

from __future__ import annotations

import os
import re
import uuid

from . import (ANSWER, OPENAI_ENGINE, ROUTE, STRUCTURAL, ResultName, TestType,
               _no_response_pair)

ASR_CHECK = "external/transcriptions"
ASR_ANSWER_CHECK = "external/transcriptions-accuracy"
# Speech we generated ourselves (VoxCPM2), committed with the probes.
ASR_FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "probes", "fixtures", "voxcpm2-speech.wav")
ASR_REFERENCE = "Hello from Vast. The secret number is seven three one nine."
# Word error rate at or under this passes. The clip is short and clean, so a correct
# model is at or near 0; 0.25 allows two wrong words in this eleven-word sentence.
ASR_MAX_WER = 0.25

_DIGIT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
                "nine")


def _words(text):
    """Lowercase words, punctuation dropped, each digit spoken as a word, so "7319"
    and "seven three one nine" compare equal."""
    text = re.sub(r"\d", lambda m: f" {_DIGIT_WORDS[int(m.group())]} ", text.lower())
    return re.findall(r"[a-z']+", text)


def word_error_rate(reference, hypothesis):
    """Word-level edit distance divided by the reference length."""
    ref, hyp = _words(reference), _words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def _multipart(fields, file_field, filename, file_bytes, file_type):
    boundary = uuid.uuid4().hex
    parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
             .encode() for k, v in fields.items()]
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                  f"filename=\"{filename}\"\r\nContent-Type: {file_type}\r\n\r\n").encode()
                 + file_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def check_asr(endpoint, audio=None, reference=ASR_REFERENCE):
    """Speech-to-text checks: one structural, one answer check (ADR 0059, D4).

    Structural (can gate): /v1/audio/transcriptions accepts a multipart WAV and
    returns HTTP 200 with a non-empty `text`.
    Answer (model-library approval): the transcript matches the reference sentence
    with a word error rate at or under ASR_MAX_WER.
    """
    def fail(detail):
        return [{"name": ASR_CHECK, "state": "failed", "kind": "structural", "detail": detail},
                {"name": ASR_ANSWER_CHECK, "state": "skipped", "kind": "answer",
                 "detail": "structural check failed"}]

    if audio is None:
        with open(ASR_FIXTURE, "rb") as f:
            audio = f.read()
    status, models = endpoint.call("/v1/models")
    if status == 0:
        return _no_response_pair(ASR_CHECK, ASR_ANSWER_CHECK, "/v1/models", models)
    if status != 200 or not isinstance(models, dict) or not models.get("data"):
        return fail(f"/v1/models returned HTTP {status}: {str(models)[:200]}")
    model = models["data"][0].get("id")
    body, ctype = _multipart({"model": model}, "file", "probe.wav", audio, "audio/wav")
    status, r = endpoint.call_raw("/v1/audio/transcriptions", body, ctype)
    if status == 0:
        return _no_response_pair(ASR_CHECK, ASR_ANSWER_CHECK, "/v1/audio/transcriptions", r)
    if status != 200:
        return fail(f"/v1/audio/transcriptions returned HTTP {status}: {str(r)[:200]}")
    text = r.get("text") if isinstance(r, dict) else None
    if not isinstance(text, str) or not text.strip():
        return fail(f"no transcript text in the response: {str(r)[:200]}")
    wer = word_error_rate(reference, text)
    return [{"name": ASR_CHECK, "state": "passed", "kind": "structural",
             "detail": f"model {model}, {len(text)} chars"},
            {"name": ASR_ANSWER_CHECK, "kind": "answer",
             "state": "passed" if wer <= ASR_MAX_WER else "failed",
             "detail": f"WER {wer:.2f} (max {ASR_MAX_WER}): {text.strip()[:120]!r}"}]


def _run(ctx):
    return check_asr(ctx.endpoint)


TYPE = TestType(
    name="transcriptions",
    axis=ROUTE,
    requires=("transcriptions",),
    images=OPENAI_ENGINE,
    results=(ResultName(ASR_CHECK, STRUCTURAL, OPENAI_ENGINE, True),
             ResultName(ASR_ANSWER_CHECK, ANSWER, OPENAI_ENGINE, True)),
    fixtures=(ASR_FIXTURE,),
    unrun=(ASR_CHECK, ASR_ANSWER_CHECK),
    order=50,
    run=_run,
)
