#!/usr/bin/env python3
"""Embeddings probe: /v1/embeddings returns vectors, and a paraphrase scores closer than an
unrelated sentence. Usage: embed_probe.py <instance_id> [expected_dim]"""
import json, math, os, ssl, sys, urllib.request
# The launcher modules (test_template.VastAPI) are the parent directory of probes/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_template import VastAPI  # noqa: E402
CTX = ssl._create_unverified_context()
inst = VastAPI(os.environ["VAST_ADMIN_API_KEY"])._request("GET", f"/api/v0/instances/{sys.argv[1]}/")
inst = inst.get("instances", inst)
ip, port, tok = inst["public_ipaddr"].strip(), inst["ports"]["8000/tcp"][0]["HostPort"], inst.get("jupyter_token") or ""
def call(path, body=None):
    last = ""
    for scheme in ("https", "http"):
        req = urllib.request.Request(f"{scheme}://{ip}:{port}{path}", data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                                     method="POST" if body else "GET")
        try:
            with urllib.request.urlopen(req, timeout=300, context=CTX) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, {"error": e.read().decode(errors="replace")[:300]}
        except Exception as e: last = f"{type(e).__name__}: {e}"
    return 0, {"error": last}
st, m = call("/v1/models"); model = m["data"][0]["id"]; print(f"/v1/models -> {st} model={model}")
texts = ["The cat sat on the warm windowsill in the sun.", "A kitten was lying on a sunny window ledge.",
         "Quarterly revenue grew because of strong GPU rental demand."]
st, r = call("/v1/embeddings", {"model": model, "input": texts})
if st != 200: print(f"[FAIL] embeddings: HTTP {st} {r.get('error')}"); print("SUMMARY 0/1"); sys.exit()
vecs = [d["embedding"] for d in r["data"]]
def cos(a, b): return sum(x*y for x, y in zip(a, b)) / (math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(y*y for y in b)))
dim = len(vecs[0]); want = int(sys.argv[2]) if len(sys.argv) > 2 else None
s_par, s_unrel = cos(vecs[0], vecs[1]), cos(vecs[0], vecs[2])
ok = len(vecs) == 3 and (want is None or dim == want) and s_par > s_unrel
print(f"[{'PASS' if ok else 'FAIL'}] embeddings: dim={dim} cos(paraphrase)={s_par:.3f} cos(unrelated)={s_unrel:.3f}")
print(f"SUMMARY {int(ok)}/1 embedding probes passed")
