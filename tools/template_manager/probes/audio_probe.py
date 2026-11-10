#!/usr/bin/env python3
"""Audio probes for a kept model-library instance (Caddy :8000, bearer = jupyter_token).

  audio_probe.py <instance_id> tts <out.wav>   -> /v1/audio/speech, validate WAV, then the
                                                  /v1/completions crash check, then speech again
  audio_probe.py <instance_id> asr <in.wav>    -> /v1/audio/transcriptions, check transcript
"""
import io, json, math, os, ssl, struct, sys, time, urllib.request, uuid, wave

# The launcher modules (test_template.VastAPI) are the parent directory of probes/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_template import VastAPI  # noqa: E402

IID, MODE, PATH = sys.argv[1], sys.argv[2], sys.argv[3]
SAY = "Hello from Vast. The secret number is seven three one nine."
CTX = ssl._create_unverified_context()
api = VastAPI(os.environ["VAST_ADMIN_API_KEY"])
inst = api._request("GET", f"/api/v0/instances/{IID}/")
inst = inst.get("instances", inst)
BASE_HOST, PORT = inst["public_ipaddr"].strip(), inst["ports"]["8000/tcp"][0]["HostPort"]
TOKEN = inst.get("jupyter_token") or ""


def call(path, body=None, ctype="application/json", timeout=600, raw=False):
    for scheme in ("https", "http"):
        req = urllib.request.Request(f"{scheme}://{BASE_HOST}:{PORT}{path}", data=body,
                                     headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": ctype},
                                     method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                data = r.read()
                return r.status, (data if raw else json.loads(data or b"{}"))
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace")[:500]
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
    return 0, last


st, models = call("/v1/models")
model = (models.get("data") or [{}])[0].get("id") if isinstance(models, dict) else None
print(f"/v1/models -> {st} model={model}")


def speech(label):
    t = time.time()
    st, data = call("/v1/audio/speech", json.dumps({"model": model, "input": SAY, "response_format": "wav"}).encode(), raw=True)
    if st != 200 or not isinstance(data, bytes):
        print(f"[FAIL] {label}: HTTP {st} {str(data)[:300]}")
        return None
    try:
        w = wave.open(io.BytesIO(data))
        frames = w.readframes(w.getnframes())
        dur = w.getnframes() / w.getframerate()
        n = len(frames) // 2
        rms = math.sqrt(sum(s * s for s in struct.unpack(f"<{n}h", frames[: n * 2])) / max(n, 1)) if w.getsampwidth() == 2 else -1
        ok = 1.0 < dur < 30 and rms > 100
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {time.time()-t:.1f}s  {w.getframerate()} Hz  {dur:.1f}s audio  rms={rms:.0f}")
        return data if ok else None
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {label}: not a WAV ({type(e).__name__}: {e}); first bytes {data[:16]!r}")
        return None


if MODE == "tts":
    audio = speech("speech #1")
    if audio:
        open(PATH, "wb").write(audio)
        print(f"    saved {PATH}")
    st, r = call("/v1/completions", json.dumps({"model": model, "prompt": "Hello", "max_tokens": 8}).encode())
    print(f"[INFO] POST /v1/completions -> HTTP {st} {str(r)[:200]}")
    time.sleep(5)
    st, _ = call("/health")
    print(f"[INFO] /health after completions -> {st}")
    again = speech("speech #2 (after /v1/completions)")
    print("CRASH CHECK:", "OK - still serves speech" if again else "BROKEN - completions call killed TTS")
else:
    boundary = uuid.uuid4().hex
    wav = open(PATH, "rb").read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{model}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"probe.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n").encode() + wav + f"\r\n--{boundary}--\r\n".encode()
    t = time.time()
    st, r = call("/v1/audio/transcriptions", body, ctype=f"multipart/form-data; boundary={boundary}")
    text = r.get("text", "") if isinstance(r, dict) else str(r)
    low = text.lower()
    ok = st == 200 and "vast" in low and any(k in low for k in ("7319", "seven", "three", "nine", "7 3 1 9"))
    print(f"[{'PASS' if ok else 'FAIL'}] transcription: HTTP {st} {time.time()-t:.1f}s")
    print("    expected:", SAY)
    print("    got:     ", " ".join(text.split())[:300])
