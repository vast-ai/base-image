#!/usr/bin/env python3
"""Chat-content probe: system message + earlier turns must reach the model.

Catches the 'chat template only reads string content' failure, where vLLM sends
content as a list and the template renders it empty (prompt shrinks, history 400s).
Usage: chat_probe.py <instance_id>
"""
import json, os, ssl, sys, time, urllib.request

# The launcher modules (test_template.VastAPI) are the parent directory of probes/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_template import VastAPI  # noqa: E402

CTX = ssl._create_unverified_context()
api = VastAPI(os.environ["VAST_ADMIN_API_KEY"])
inst = api._request("GET", f"/api/v0/instances/{sys.argv[1]}/")
inst = inst.get("instances", inst)
ip, port, tok = inst["public_ipaddr"].strip(), inst["ports"]["8000/tcp"][0]["HostPort"], inst.get("jupyter_token") or ""


def call(path, body=None):
    for scheme in ("https", "http"):
        req = urllib.request.Request(f"{scheme}://{ip}:{port}{path}", data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                                     method="POST" if body else "GET")
        try:
            with urllib.request.urlopen(req, timeout=600, context=CTX) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.read().decode(errors="replace")[:300]}
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
    return 0, {"error": last}


st, m = call("/v1/models")
model = m["data"][0]["id"]
print(f"/v1/models -> {st} model={model}")
results = []


def probe(name, messages, expect):
    t = time.time()
    st, r = call("/v1/chat/completions", {"model": model, "messages": messages, "max_tokens": 2048, "temperature": 0})
    if st != 200:
        print(f"[FAIL] {name}: HTTP {st} {r.get('error')}")
        results.append(False)
        return
    msg = r["choices"][0]["message"]
    text = msg.get("content") or ""
    ok = expect.lower() in text.lower()
    rkeys = [k for k in ("reasoning", "reasoning_content") if msg.get(k)]
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {time.time()-t:.1f}s prompt_tokens={r.get('usage', {}).get('prompt_tokens')} reasoning_fields={rkeys or 'none'}")
    print("    answer:", " ".join(text.split())[:200])
    results.append(ok)


probe("single user turn reaches model",
      [{"role": "user", "content": "Reply with exactly this code and nothing else: MARKER-ALPHA-731"}], "MARKER-ALPHA-731")
probe("system message + user turn",
      [{"role": "system", "content": "You are a terse assistant. Answer in as few words as possible."},
       {"role": "user", "content": "Reply with exactly this code and nothing else: GREEN-OTTER-42"}], "GREEN-OTTER-42")
HIST = [{"role": "system", "content": "You are a terse assistant."},
        {"role": "user", "content": "Remember this code word: BLUE-HERON-58."},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": "What was the code word? Reply with just the code word."}]
withr = [dict(m) for m in HIST]
withr[2]["reasoning_content"] = "The user wants me to remember BLUE-HERON-58."
probe("history, assistant turn carries reasoning_content", withr, "BLUE-HERON-58")
probe("history, plain OpenAI-style assistant turn (informational)", HIST, "BLUE-HERON-58")
print(f"SUMMARY {sum(results)}/{len(results)} chat-content probes passed")
