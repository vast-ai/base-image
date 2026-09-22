# ADR 0044 — vLLM-Omni gains serverless wiring, gated on a text-model cell

- **Status:** Accepted
- **Date:** 2026-09-21
- **Decision owner:** Rob Ballantyne
- Related: ADR 0005 (live-GPU QA gate), ADR 0029 (a failing cell redraws before it
  blocks), ADR 0031 (inference images gate on contract, worker and capability),
  ADR 0038 (serverless mode is declared by the platform), L067, L079

## Context

`external/vllm-omni` is the only engine image in the catalogue with no serverless wiring:
no `BACKEND`, no `MODEL_LOAD_LOG_MSG`, no `EXPOSE 3000`, and no `qa-serverless` job. Its
QA template says so in as many words and anticipates this change: *"If omni gains
serverless support, the 3000 mapping and a qa-serverless cell come with it."* So this
executes a plan the repo already recorded rather than superseding one.

Why now: the serverless OpenAI worker gained `/v1/audio/speech`, `/v1/images/generations`,
`/v1/images/edits` and `/v1/images/variations`. **No engine in this catalogue serves any
of them.** vLLM, SGLang and llama.cpp implement none; vLLM-Omni implements image
generation and TTS behind `--omni` in `VLLM_ARGS`. Four routes of the nine therefore have
no engine behind them anywhere, and cannot be proven live, because the one image that
could run them cannot be launched serverless at all.

The constraint that shapes this decision is in the same QA template: omni modality is
opt-in, and *"the smallest clean image model is ~24 GB against this model's ~1 GB, which
moves the disk and VRAM floors and thins the offer pool."* A cell that proves the omni
routes is a different, much more expensive thing than a cell that proves the wiring.

## Options considered

**A. Wiring only, gated by a tiny-text-model serverless cell. CHOSEN.**
The image gets the same serverless block as `external/vllm`, and `build-vllm-omni.yml`
gains a `qa-serverless` job that mirrors vLLM's: same template, `SERVERLESS=true`, the
same 1 GB chat model, `vllm-omni.d/20-serverless-pyworker` in the required set.
Proves: the worker boots under `SERVERLESS=true`, `BACKEND=vllm` selects the right
adapter, `MODEL_LOAD_LOG_MSG` matches what this engine actually logs, :3000 is mapped and
listening, and a benchmark score is reached. Proves nothing about omni modality.

**B. Wiring gated by an omni-model serverless cell. REJECTED for now.**
Would prove `/v1/audio/speech` and `/v1/images/generations` end to end, which is the
motivation for the whole endpoint-coverage effort. Rejected because it pays that cost on
EVERY build: a ~24 GB model moves the VRAM and disk floors, thins the offer pool each run
draws from, and lengthens the cell — against ADR 0029's concern that a redrawn cell is
expensive. It also buys a fact that does not change between builds: whether an omni model
serves those routes is a property of the MODEL and the engine version, not of our image.

**C. Wiring with no serverless cell at all. REJECTED.**
The wiring's three realistic failure modes — wrong backend adapter, wrong load-log line,
unmapped port — are silent: the worker simply never becomes ready, and nothing else in
the build notices. ADR 0031 exists because an image that looks healthy while serving
nothing is the failure this gate is for. Shipping the wiring ungated would be exactly
that.

**D. A separate omni-route QA template, run on a schedule rather than per build.
DEFERRED, not rejected.** The right home for option B's claim. Out of scope here because
it needs a model choice, a VRAM floor and an offer-pool study of its own.

## Decision

Take option A.

1. `external/vllm-omni/Dockerfile` gains the serverless block, identical in shape to
   `external/vllm`: `ENV BACKEND=vllm`, `ENV MODEL_LOAD_LOG_MSG="Application startup
   complete."`, `EXPOSE 3000`.
2. `MODEL_LOG=/var/log/portal/vllm-omni.log` is baked too, and it is the one thing
   `BACKEND=vllm` does NOT carry over. `workers/vllm`'s EngineDefaults tail
   `/var/log/portal/vllm.log`, named for the supervisor program — which here is
   `vllm-omni`, so that file never exists. Measured on the first run of this cell: the
   worker bound :3000, reported `MODEL_LOG:` empty and `Using worker backend: vllm`, the
   engine logged `Application startup complete.` to `vllm-omni.log`, and the worker never
   saw it — no benchmark in 1800s, on three hosts in a row. The other engine images pass
   on a coincidence of naming between their program and the worker's expected filename;
   this one cannot, which makes explicit what was always true: the image is the layer
   that knows where its engine logs.
3. `BACKEND=vllm` rather than a new adapter. vLLM-Omni IS vLLM — its launcher runs the
   same `vllm serve` and reads the same `VLLM_MODEL`/`VLLM_ARGS`, so `workers/vllm`'s
   engine defaults apply unchanged. A new adapter would be a second copy of the same
   values, free to drift.
4. `vllm-omni.d/` gains `20-serverless-pyworker.sh`, copied from `vllm.d/` unchanged:
   the file contains nothing engine-specific, and its `is_serverless` guard keeps it
   dormant on the existing on-demand cell (L067).
5. The QA template maps 3000 and its header note is corrected, since it currently states
   the opposite as a deliberate decision.
6. `build-vllm-omni.yml` gains a `qa-serverless` job mirroring vLLM's, with
   `vllm-omni.d/*` test names and this image's log paths.
7. The cell is ORDERED ahead of the promotion approval and does NOT block it, which
   requires `!cancelled()` on `merge-manifests` and the blocking results named
   explicitly. An `if:` with no status function leaves GitHub's implicit `success()` on
   every `needs` entry, so the first implementation of this decision did the OPPOSITE of
   what this paragraph says: a red serverless cell would have skipped the promotion
   silently, on a twice-daily schedule, withholding upstream releases and security
   rebuilds from on-demand customers. Caught by adversarial review before approval,
   because a GREEN run behaves identically under both wirings and could never have
   shown it. A `qa-serverless` arm was added to the notify headline at the same time:
   an advisory cell that fails without naming itself is not advisory, it is ignored — ADR 0006
   condition 2's ramp, which every other engine went through. It becomes gating after
   two consecutive green runs whose greens are checked for vacuity
   (`base/85-serverless-services` and `vllm-omni.d/20-serverless-pyworker` both PASSED,
   not skipped). Advisory is right for a cell nothing has ever run: the wiring is
   strictly additive, since before it serverless did not work here at all, so an early
   red should tell us rather than block an image that is no worse than yesterday's.

## Binding conditions

- **The omni routes remain unproven, and the docs must say so.** This ADR licenses no
  claim about `/v1/audio/speech` or `/v1/images/*` working through the worker. Option D
  or a manual live test is what would license it.
- **The cell must be able to fail.** If `MODEL_LOAD_LOG_MSG` is wrong for this engine,
  the first run of this cell must go red rather than skip. `20-serverless-pyworker`'s
  `is_serverless` guard is a skip only when the mode is off; the cell sets it on.

## Consequences

- vLLM-Omni becomes launchable serverless, which is what unblocks proving the four
  unproven routes at all — by hand first, per the binding condition above.
- One more live-GPU cell per omni build, on the same offer pool and model as the existing
  one, so the added cost is one instance rather than a new class of instance.
- **The cheap cell earned itself on its first run.** It was built to catch a wrong
  `MODEL_LOAD_LOG_MSG`; what it actually caught was a wrong log PATH, a failure mode
  nobody had listed. The log line turned out to be identical to stock vLLM's — the
  engine printed it — and the worker was reading a file that does not exist on this
  image. Option C would have shipped that silently: serverless would have been wired,
  advertised, and dead.
- **Open question this raises beyond omni.** vllm, sglang and llama all rely on their
  supervisor program name matching a filename hardcoded in another repo, and none bakes
  `MODEL_LOG`. They work today by coincidence, not by contract. Making the image own it
  everywhere is a separate change against three working images, so it is recorded here
  rather than smuggled in.
- **A missing invariant surfaced while building this and is now gated (L097).** The
  serverless cell declares its required tests in the WORKFLOW, because the template is
  shared with the on-demand cell that must not require them — and L057/L059/L072 all
  read the template. Deleting `vllm-omni.d/20-serverless-pyworker.sh` therefore left the
  workflow requiring a test that did not exist, with every static check clean; the gate
  would have failed on a rented GPU over a fact visible in the repo. L097 closes it for
  every workflow, not just this one.
- `EXPOSE 3000` on an on-demand launch stays unbound: `pyworker.sh` runs the worker only
  under `SERVERLESS=true` (ADR 0038). The port is reserved, not occupied.
