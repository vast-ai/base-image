"""Speech: the template serves /v1/audio/speech (text to speech)."""

from __future__ import annotations

import io
import json
import math
import struct
import wave

from . import (ANSWER, OPENAI_ENGINE, ROUTE, STRUCTURAL, ResultName, TestType,
               _no_response_pair)

TTS_CHECK = "external/speech"
TTS_ANSWER_CHECK = "external/speech-audio"
TTS_TEXT = "Hello from Vast. This is a short test of text to speech."
# Speech runs at roughly 2.5 words per second; accept a wide band around that.
TTS_SECONDS_PER_WORD = (0.15, 1.5)
# 16-bit PCM RMS below this is silence or near-silence.
TTS_MIN_RMS = 100


def _wav_stats(data):
    """(sample_rate, seconds, rms or None) for a PCM WAV, or an error string."""
    if not isinstance(data, (bytes, bytearray)) or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        head = data[:16] if isinstance(data, (bytes, bytearray)) else str(data)[:60]
        return f"not a WAV file (starts {head!r})"
    try:
        w = wave.open(io.BytesIO(data))
        n, rate, width = w.getnframes(), w.getframerate(), w.getsampwidth()
        frames = w.readframes(n)
    except (wave.Error, EOFError) as e:
        return f"WAV does not decode: {e}"
    if n == 0 or rate == 0:
        return "WAV has no audio frames"
    rms = None
    if width == 2:
        count = len(frames) // 2
        samples = struct.unpack(f"<{count}h", frames[: count * 2])
        rms = math.sqrt(sum(x * x for x in samples) / max(count, 1))
    return rate, n / rate, rms


def check_tts(endpoint, text=TTS_TEXT):
    """Text-to-speech checks: one structural, one answer check (ADR 0059, D4).

    Structural (can gate): /v1/audio/speech returns HTTP 200 with a WAV that decodes
    and holds audio frames.
    Answer (model-library approval): the audio is speech-like — not silent, and a
    plausible length for the text. A round trip through speech recognition is the
    stronger check and needs an ASR endpoint; it is not done here.
    """
    def fail(detail):
        return [{"name": TTS_CHECK, "state": "failed", "kind": "structural", "detail": detail},
                {"name": TTS_ANSWER_CHECK, "state": "skipped", "kind": "answer",
                 "detail": "structural check failed"}]

    status, models = endpoint.call("/v1/models")
    if status == 0:
        return _no_response_pair(TTS_CHECK, TTS_ANSWER_CHECK, "/v1/models", models)
    if status != 200 or not isinstance(models, dict) or not models.get("data"):
        return fail(f"/v1/models returned HTTP {status}: {str(models)[:200]}")
    model = models["data"][0].get("id")
    body = json.dumps({"model": model, "input": text, "response_format": "wav"}).encode()
    status, data = endpoint.call_raw("/v1/audio/speech", body, "application/json", binary=True)
    if status == 0:
        return _no_response_pair(TTS_CHECK, TTS_ANSWER_CHECK, "/v1/audio/speech", data)
    if status != 200:
        return fail(f"/v1/audio/speech returned HTTP {status}: {str(data)[:200]}")
    stats = _wav_stats(data)
    if isinstance(stats, str):
        return fail(stats)
    rate, seconds, rms = stats
    words = len(text.split())
    lo, hi = words * TTS_SECONDS_PER_WORD[0], words * TTS_SECONDS_PER_WORD[1]
    problems = []
    if not lo <= seconds <= hi:
        problems.append(f"{seconds:.1f}s is implausible for {words} words ({lo:.1f}-{hi:.1f}s)")
    if rms is not None and rms < TTS_MIN_RMS:
        problems.append(f"near-silent (rms {rms:.0f} < {TTS_MIN_RMS})")
    level = f"rms {rms:.0f}" if rms is not None else "rms n/a (not 16-bit PCM)"
    return [{"name": TTS_CHECK, "state": "passed", "kind": "structural",
             "detail": f"model {model}, WAV {rate} Hz, {seconds:.1f}s"},
            {"name": TTS_ANSWER_CHECK, "kind": "answer",
             "state": "failed" if problems else "passed",
             "detail": "; ".join(problems) if problems else f"{seconds:.1f}s for {words} words, {level}"}]


def _is_music(ctx):
    """SPEECH_KIND=music: the route returns a song, which the speech check cannot judge."""
    return (ctx.env_value("SPEECH_KIND") or "speech").strip().lower() == "music"


def _unrun(ctx):
    return () if _is_music(ctx) else (TTS_CHECK, TTS_ANSWER_CHECK)


def _run(ctx):
    if _is_music(ctx):
        return [{"name": TTS_CHECK, "state": "skipped",
                 "detail": "SPEECH_KIND=music: the song variant of the speech "
                           "check is not written yet"}]
    return check_tts(ctx.endpoint)


TYPE = TestType(
    name="speech",
    axis=ROUTE,
    requires=("speech",),
    images=OPENAI_ENGINE,
    results=(ResultName(TTS_CHECK, STRUCTURAL, OPENAI_ENGINE, True),
             ResultName(TTS_ANSWER_CHECK, ANSWER, OPENAI_ENGINE, True)),
    unrun=_unrun,
    order=60,
    run=_run,
)
