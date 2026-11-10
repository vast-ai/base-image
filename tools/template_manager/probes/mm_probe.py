#!/usr/bin/env python3
"""Multimodal probe for a kept model-library instance (OpenAI-compatible server).

Usage: mm_probe.py <instance_id> [--video]

Generates synthetic test media with known answers, sends them through the
instance's public Caddy front (port 8000, bearer = instance jupyter_token), and
grades the replies. The token is read from the Vast API and never printed.
"""
import base64, io, json, os, ssl, subprocess, sys, tempfile, time, urllib.request

# The launcher modules (test_template.VastAPI) are the parent directory of probes/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_template import VastAPI  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

IID = sys.argv[1]
DO_VIDEO = "--video" in sys.argv
CTX = ssl._create_unverified_context()


def font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def png_b64(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def img_shapes():
    im = Image.new("RGB", (640, 400), "white")
    d = ImageDraw.Draw(im)
    d.text((170, 20), "VAST 7319", fill="black", font=font(64))
    d.ellipse((60, 150, 260, 350), fill=(220, 20, 20))
    d.rectangle((400, 150, 600, 350), fill=(20, 40, 220))
    return png_b64(im)


def img_word(word, bg):
    im = Image.new("RGB", (480, 240), bg)
    ImageDraw.Draw(im).text((60, 80), word, fill="white", font=font(80))
    return png_b64(im)


def video_moving_square():
    tmp = tempfile.mkdtemp()
    for i in range(24):
        im = Image.new("RGB", (320, 240), "white")
        x = 10 + i * 11
        ImageDraw.Draw(im).rectangle((x, 90, x + 50, 140), fill=(220, 20, 20))
        im.save(f"{tmp}/f{i:03d}.png")
    out = f"{tmp}/v.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-framerate", "8", "-i", f"{tmp}/f%03d.png",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", out], check=True)
    return "data:video/mp4;base64," + base64.b64encode(open(out, "rb").read()).decode()


api = VastAPI(os.environ["VAST_ADMIN_API_KEY"])
inst = api._request("GET", f"/api/v0/instances/{IID}/")
inst = inst.get("instances", inst)
ip = inst["public_ipaddr"].strip()
port = inst["ports"]["8000/tcp"][0]["HostPort"]
token = inst.get("jupyter_token") or ""
HDR = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def call(path, body=None, timeout=900):
    last = None
    for scheme in ("https", "http"):
        url = f"{scheme}://{ip}:{port}{path}"
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                     headers=HDR, method="POST" if body else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.read().decode(errors="replace")[:600]}
        except Exception as e:  # noqa: BLE001 -- try the other scheme
            last = e
    return 0, {"error": f"{type(last).__name__}: {last}"}


st, models = call("/v1/models")
model = (models.get("data") or [{}])[0].get("id")
print(f"/v1/models -> {st} model={model}")
st, props = call("/props")
if st == 200:
    print(f"/props modalities={props.get('modalities')}")

results = []


def probe(name, content, checks, ordered=False, temperature=1.0):
    body = {"model": model, "messages": [{"role": "user", "content": content}],
            "max_tokens": 4096, "temperature": temperature, "top_p": 0.95}
    t = time.time()
    st, r = call("/v1/chat/completions", body)
    dt = time.time() - t
    if st != 200:
        print(f"[FAIL] {name}: HTTP {st} {str(r.get('error'))[:400]}")
        results.append((name, False))
        return
    msg = r["choices"][0]["message"]
    text = (msg.get("content") or "")
    low = text.lower()
    ok = all(any(alt in low for alt in c) for c in checks)
    if ok and ordered:
        pos = [min(low.find(a) for a in c if a in low) for c in checks]
        ok = pos == sorted(pos)
    usage = r.get("usage", {})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {dt:.1f}s prompt_tokens={usage.get('prompt_tokens')} "
          f"completion_tokens={usage.get('completion_tokens')} reasoning={'yes' if msg.get('reasoning_content') or msg.get('reasoning') else 'no'}")
    print("    answer:", " ".join(text.split())[:300])
    results.append((name, ok))
    if not ok and temperature != 0.0:
        results.pop()
        print(f"    retrying {name} at temperature 0")
        probe(name + " [temp 0 retry]", content, checks, ordered, temperature=0.0)


probe("image: OCR + colors",
      [{"type": "image_url", "image_url": {"url": img_shapes()}},
       {"type": "text", "text": "Look at the image. Reply with exactly three short lines: "
                                "the number written in the image, the color of the circle, "
                                "and the color of the square."}],
      [["7319"], ["red"], ["blue"]])

probe("multi-image: order",
      [{"type": "image_url", "image_url": {"url": img_word("APPLE", (20, 130, 40))}},
       {"type": "image_url", "image_url": {"url": img_word("RIVER", (110, 30, 140))}},
       {"type": "text", "text": "Two images are attached. What single word is written in the first "
                                "image, and what single word is written in the second? Answer as "
                                "'first: <word>, second: <word>'."}],
      [["apple"], ["river"]], ordered=True)

if DO_VIDEO:
    probe("video: motion direction",
          [{"type": "video_url", "video_url": {"url": video_moving_square()}},
           {"type": "text", "text": "In this video a colored square moves. What color is it, and does "
                                    "it move left-to-right or right-to-left? Answer in one sentence."}],
          [["red"], ["left-to-right", "left to right", "to the right", "rightward"]])

print(f"SUMMARY {sum(ok for _, ok in results)}/{len(results)} multimodal probes passed")
