"""Kev decision-model UI: the Kev Hugging Face Space (jaredpalmer/kev, revision 46ada90ff4aca8424cc02cc80aeae0a29ec4dc1b,
Apache-2.0) adapted to call a running kev.serve over HTTP instead of loading the model in-process.

Changes from the Space:
  * no model in this process: every request goes to POST {KEV_API}/v1/systemone, so the UI adds no GPU memory;
  * the option-order check uses the server's POST /v1/systemone/permute instead of re-running the model locally;
  * one server serves one model, so the model picker ("Kev-4B / Kev-0.8B / Both") becomes a read-only label taken
    from GET /v1/models;
  * calibration is fixed by the server at start-up (KEV_TEMPERATURE), so the per-request toggle is replaced by a
    label showing the temperature the server applies;
  * date_facts is applied here, before the request is sent (kev.api.with_date_facts is pure Python);
  * the ZeroGPU `spaces` import and the MCP server are removed, and the bind address is explicit.
Rendering, presets and help text are the Space's own.
"""
import html, json, os, time

import gradio as gr
import httpx
from pydantic import ValidationError

from kev.api import SystemOneRequest, render, with_date_facts   # pydantic-only module; no torch import
from presets import PRESETS

KEV_API = os.environ.get("KEV_API", "http://127.0.0.1:18000").rstrip("/")
KEV_API_KEY = os.environ.get("KEV_API_KEY", "")
HOST = os.environ.get("KEV_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("KEV_UI_PORT", "7860"))

_client = httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0),
                       headers={"authorization": f"Bearer {KEV_API_KEY}"} if KEV_API_KEY else {})


# ------------------------------------------------------------------ server

def server_card():
    """The served model's card from GET /v1/models, or None while the server is still loading."""
    try:
        r = _client.get(f"{KEV_API}/v1/models"); r.raise_for_status()
        return r.json()["models"][0]
    except Exception:
        return None


def model_label():
    card = server_card()
    if card is None:
        return "**Model:** the Kev server is still loading (the first start downloads the weights). Refresh in a minute."
    return (f"**Model:** `{card.get('run', card.get('name'))}` on `{card.get('base', '?')}` · {card.get('dtype', '?')} · "
            f"temperature {card.get('temperature', 0):.2f} (set at server start; `KEV_TEMPERATURE=1.0` gives raw logits)")


def post(path, body):
    try:
        r = _client.post(f"{KEV_API}{path}", json=body)
    except httpx.HTTPError as e:
        raise gr.Error(f"Kev server unreachable at {KEV_API}: {e}. It may still be loading.") from None
    if r.status_code == 422:
        try: detail = r.json().get("detail")
        except ValueError: detail = r.text
        raise gr.Error(f"The server rejected the request: {detail}")
    if r.status_code >= 400:
        raise gr.Error(f"Kev server error {r.status_code}: {r.text[:300]}")
    return r.json()


# ------------------------------------------------------------------ inference

def parse_state(text):
    """Plain text, or a JSON object / array (same rule as the playground)."""
    t = (text or "").strip()
    if t[:1] in "{[":
        try: return json.loads(t)
        except json.JSONDecodeError: pass
    return text or ""


def build_request(state_text, questions_json):
    try: questions = json.loads(questions_json or "")
    except json.JSONDecodeError as e: raise gr.Error(f"Questions must be valid JSON: {e}") from None
    if not isinstance(questions, dict) or not questions:
        raise gr.Error('Questions must be a non-empty JSON object, e.g. {"topic": {"type": "choice", ...}}')
    try: return SystemOneRequest(state=parse_state(state_text), model="kev-latest", questions=questions)
    except ValidationError as e:
        first = e.errors()[0]
        raise gr.Error(f"Invalid question at `{'.'.join(str(x) for x in first.get('loc', ()))}`: {first.get('msg')}") from None


def systemone(req):
    """POST /v1/systemone; the response already carries answers, usage and latency_ms."""
    t0 = time.perf_counter()
    resp = post("/v1/systemone", req.model_dump(mode="json"))
    resp.setdefault("latency_ms", round((time.perf_counter() - t0) * 1000, 1))
    return resp


def stability(req, n_perm):
    """Re-run the first Choice question with 2+ options under shuffled option orders (server-side /v1/systemone/permute)."""
    target = next((qid for qid, q in req.questions.items() if q.type == "choice" and len(q.criteria) >= 2), None)
    if target is None: return "_No Choice question with two or more options to permute._"
    keys = list(req.questions[target].criteria)
    r = post("/v1/systemone/permute", {"request": req.model_dump(mode="json"), "question": target, "n_perm": max(2, n_perm)})
    runs, spread = r["runs"], r["spread"]
    lines = [f"**`{target}` under {len(runs)} option orders:** argmax {'stable' if r['argmax_stable'] else 'flips'}, "
             f"largest probability spread {max(spread.values()):.2f}", "",
             "| order | picked | " + " | ".join(f"`{k}`" for k in keys) + " |", "|---|---|" + "---|" * len(keys)]
    lines += [f"| {' → '.join(x['order'])} | `{x['choice']}` | " + " | ".join(f"{x['probabilities'][k]:.2f}" for k in keys) + " |" for x in runs]
    return "\n".join(lines)


# ------------------------------------------------------------------ rendering

CARD = "border:1px solid var(--border-color-primary);border-radius:10px;padding:12px 14px;margin-bottom:10px;background:var(--background-fill-secondary);"
TRACK = "position:relative;display:block;height:6px;border-radius:999px;background:var(--border-color-primary);overflow:hidden;"


def bar(label, p, top):
    fill = "var(--body-text-color)" if top else "var(--body-text-color-subdued)"; w = "600" if top else "400"
    return ('<div style="display:grid;grid-template-columns:minmax(0,11rem) minmax(0,1fr) 3rem;align-items:center;gap:0 12px;font-size:12px;line-height:22px">'
            f'<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:{w}" title="{html.escape(label)}">{html.escape(label)}</span>'
            f'<span style="{TRACK}"><span style="position:absolute;top:0;bottom:0;left:0;border-radius:999px;background:{fill};width:{max(0.0, min(1.0, p)) * 100:.1f}%"></span></span>'
            f'<span style="text-align:right;font-variant-numeric:tabular-nums;font-weight:{w}">{p:.2f}</span></div>')


def render_answers(req, resp):
    answers, usage = resp["answers"], resp["usage"]
    parts = ['<div style="font-size:12px;color:var(--body-text-color-subdued);margin:6px 0 10px">'
             f'{len(answers)} question(s) · {usage["input_tokens"]} input tokens · {resp["latency_ms"]:.0f} ms on the server · no text generated</div>']
    for qid, a in answers.items():
        q = req.questions.get(qid); instr = render(q.instructions) if q is not None else ""
        if a["type"] == "noul":
            p = a["noul"]; headline, detail = ("yes" if p >= 0.5 else "no"), f"p(yes) {p:.2f}"
            rows = bar("yes", p, p >= 0.5) + bar("no", 1 - p, p < 0.5)
        elif a["type"] == "choice":
            headline, detail = a["choice"], f"confidence {a['confidence']:.2f}"
            rows = "".join(bar(k, v, k == a["choice"]) for k, v in sorted(a["probabilities"].items(), key=lambda kv: -kv[1]))
        else:
            legend, dist = a["legend"], a["probabilities"]; top = max(dist, key=dist.get)
            headline, detail = f"{a['score']:.2f} of {len(legend) - 1}", f"confidence {a['confidence']:.2f}"
            rows = "".join(bar(f"{i}. {legend[str(i)]}", dist[str(i)], str(i) == top) for i in range(len(legend)))
        parts.append(f'<div style="{CARD}"><div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap">'
                     f'<span style="font-family:var(--font-mono);font-size:13px;font-weight:600">{html.escape(qid)}</span>'
                     f'<span style="font-size:11px;color:var(--body-text-color-subdued)">· {a["type"]}</span></div>'
                     f'<div style="font-size:13px;margin:2px 0 6px">{html.escape(instr)}</div>'
                     '<div style="display:flex;align-items:baseline;gap:10px;margin-bottom:8px">'
                     f'<span style="font-size:17px;font-weight:600">{html.escape(str(headline))}</span>'
                     f'<span style="font-size:12px;color:var(--body-text-color-subdued)">{detail}</span></div>{rows}</div>')
    return "".join(parts)


# ------------------------------------------------------------------ handler

def decide(state_text, questions_json, date_facts=False, check_stability=False, n_perm=4):
    """Answer typed questions about one document with the Kev model this server runs, in a single forward pass."""
    req = build_request(state_text, questions_json)
    if date_facts: req = req.model_copy(update={"state": with_date_facts(req.state)})
    resp = systemone(req)
    report = stability(req, int(n_perm or 4)) if check_stability else ""
    return render_answers(req, resp), resp, report


# ------------------------------------------------------------------ UI

def as_text(s): return s if isinstance(s, str) else json.dumps(s, indent=2)


EXAMPLES = [[as_text(p["state"]), json.dumps(p["questions"], indent=2)] for p in PRESETS]

INTRO = """# Kev

Small decision models: one document (the **state**) and a set of typed questions in, a probability for every option
out, in one forward pass. Nothing is generated. Pick an example or paste your own, then press **Decide**.
"""

ABOUT = """Kev is a LoRA adapter plus a pointer head on a Qwen3.5 base model. The pointer head scores each question's
`<decide>` token against its option spans; a block-causal layout means questions share the state but cannot read each
other. Three question types: `choice` (named options), `noul` (yes/no), `score` (ordered levels). Requests and
responses follow TypeSafe's `/v1/systemone` contract, so the same requests work from TypeSafe's SDKs against this
instance's Kev API.

This page is the [Kev Space](https://huggingface.co/spaces/jaredpalmer/kev) adapted to call the Kev server running on this
instance. Code, training recipe and frozen evaluation suites: [github.com/jaredpalmer/kev](https://github.com/jaredpalmer/kev).
"""

PLACEHOLDER = ('<div style="border:1px dashed var(--border-color-primary);border-radius:10px;padding:28px 16px;text-align:center;'
               'color:var(--body-text-color-subdued);font-size:13px">Answers appear here. One card per question, with a bar per option.</div>')

SCHEMA = """```jsonc
{
  "department": {                       // choice: named options, probabilities by name
    "type": "choice",
    "instructions": "Which team should handle this?",
    "criteria": { "returns": "Refunds and wrong items", "billing": "Charges and invoices" }
  },
  "escalate": {                         // noul: yes/no, answer is p(true)
    "type": "noul",
    "instructions": "Does this need urgent human attention?",
    "criteria": { "true": "optional description", "false": "optional description" }
  },
  "frustration": {                      // score: ordered levels, answer is the expected level
    "type": "score",
    "instructions": "How frustrated is the customer?",
    "criteria": ["Calm", "Frustrated", "Very angry"]
  }
}
```
Descriptions may be `null`, a string, or a nested JSON object/array. The state box takes plain text or a JSON object/array.
"""

FOOTER = ("Probabilities are not perfectly calibrated out of domain. Measure on your own inputs before relying on the numbers. "
          "Example inputs are the Kev repo's playground presets. Apache-2.0.")

OPTIONS_HELP = """- **date_facts**: Kev cannot subtract dates by itself. This appends the day count between every pair of absolute dates in the
  state before the model reads it (the server-side equivalent is `KEV_DATE_FACTS=1`). The *Return window* example is wrong without it and right with it.
- **Option-order check**: re-run the first Choice question under *n* shuffled option orders and report whether the argmax flips
  and how far each probability moves.
- **Temperature**: fixed when the server starts. The label under the title shows the value in use.
"""

CSS = """
.gradio-container { overflow: visible !important; }   /* the default overflow:hidden disables position:sticky */
#col-container { max-width: 1180px; margin: 0 auto; }
#controls { position: sticky; top: 0; z-index: 50; background: var(--body-background-fill); padding: 10px 0 12px;
            border-bottom: 1px solid var(--border-color-primary); gap: 6px; }
#controls .form, #controls .block { border: none !important; background: transparent !important; box-shadow: none !important; padding: 0 !important; }
#controls .form { gap: 0; }
#controls-top { align-items: end; gap: 16px; }
#controls-options { align-items: center; gap: 8px 28px; flex-wrap: wrap; }
#controls-options > .block { flex: 0 0 auto !important; width: auto !important; min-width: 0 !important; margin: 0 !important; }
#controls-options > .block:last-child { width: 140px !important; margin-left: auto !important; }
#controls-options label { margin: 0; }
#decide-btn { min-height: 44px; }
#examples .gallery { gap: 8px; }
#questions .cm-editor { max-height: 420px; }
"""

with gr.Blocks(title="Kev decision models") as demo:
    with gr.Column(elem_id="col-container"):
        gr.Markdown(INTRO)
        model_md = gr.Markdown(model_label())

        state_box = gr.Textbox(label="State (the document the model reads)", lines=6, max_lines=14, value=EXAMPLES[0][0],
                               placeholder="Paste a support message, a review, an article, or a JSON object.", render=False)
        questions_box = gr.Code(label="Questions (JSON)", language="json", lines=14, value=EXAMPLES[0][1], render=False, elem_id="questions")

        with gr.Column(elem_id="controls"):
            with gr.Row(elem_id="controls-top", equal_height=True):
                run_btn = gr.Button("Decide", variant="primary", size="lg", scale=1, min_width=160, elem_id="decide-btn")
            with gr.Row(elem_id="controls-options"):
                dates_cb = gr.Checkbox(label="date_facts", value=False, container=False)
                stability_cb = gr.Checkbox(label="Option-order check", value=False, container=False)
                n_perm_sl = gr.Dropdown([(f"{n} orders", n) for n in (2, 3, 4, 6, 8)], value=4, label="orders", container=False, filterable=False)

        gr.Examples(examples=EXAMPLES, example_labels=[p["name"] for p in PRESETS], inputs=[state_box, questions_box], label="Examples", elem_id="examples")

        with gr.Row():
            with gr.Column(scale=1):
                state_box.render()
                questions_box.render()
            with gr.Column(scale=1):
                answers_html = gr.HTML(value=PLACEHOLDER)
                stability_md = gr.Markdown()
                with gr.Accordion("Raw /v1/systemone response", open=False): raw_json = gr.JSON()

        with gr.Accordion("About Kev", open=False): gr.Markdown(ABOUT)
        with gr.Accordion("What the examples show", open=False):
            gr.Markdown("\n".join(f"- **{p['name']}**: {p['blurb']}" for p in PRESETS))
        with gr.Accordion("What the options do", open=False): gr.Markdown(OPTIONS_HELP)
        with gr.Accordion("Question schema", open=False): gr.Markdown(SCHEMA)
        gr.Markdown(FOOTER)

    demo.load(fn=model_label, outputs=model_md, api_visibility="private")   # refresh per page load: the server may finish loading after the UI
    run_btn.click(fn=decide, inputs=[state_box, questions_box, dates_cb, stability_cb, n_perm_sl],
                  outputs=[answers_html, raw_json, stability_md], api_name="decide")

if __name__ == "__main__":
    demo.launch(server_name=HOST, server_port=PORT, theme=gr.themes.Monochrome(), css=CSS)
