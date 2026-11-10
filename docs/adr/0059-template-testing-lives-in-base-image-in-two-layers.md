# ADR 0059 — Template testing lives in base-image, in two layers

- **Status:** Proposed
- **Date:** 2026-10-29 (updated 2026-11-09 with what was built and decided while building it;
  revised 2026-11-09: inference and the contract check leave the image; the test client
  must know what it is testing for)
- **Decision owner:** Rob Ballantyne
- Related: ADR 0005 (live-GPU QA gate), ADR 0031 (inference images gate on contract,
  worker and capability), ADR 0032 (QA tests may be client-delivered — Proposed),
  ADR 0044 (vLLM-Omni serverless wiring)
- Evidence: a blind review panel (architect, test-engineer, ops-reviewer, red-team);
  its briefs and reports are held outside this repository

## Context

Every image the model library ships is built here (`vastai/{vllm,vllm-omni,sglang,
llama-cpp,comfy,base-image}`), but template testing is split three ways:

1. **In-instance suites baked into the images** — `ROOT/opt/instance-tools/tests/`
   (`base/`, `vllm.d`, `sglang.d`, `llama.d`, `vllm-omni.d`, `comfyui.d`), run by
   `runner.sh` when `INSTANCE_TEST=true`, gating promotion through `qa-gate.yml` and
   `require_tests`.
2. **A launcher, `tools/template_manager/test_template.py`**, with a vendored fork in
   vast_landing (`scripts/template_manager/test_template.py`) and a third copy under
   `pcl/`. The vast_landing fork has drifted by about 1,040 diff lines: it lacks `MAX_STREAM_RECONNECTS` (added here 2026-08-07,
   `cb8d9fa9`), `QA_POLL_TIMEOUT`, and atexit cleanup. Each copy has its own unit tests.
3. **Ad-hoc probes** written during model-library reviews and never committed: image,
   multi-image and video understanding; TTS and ASR; multi-turn chat with
   `reasoning`/`reasoning_content`; embeddings.

The probes in (3) found the defects that mattered in 2026-10: a llama.cpp GGUF vision
misread (fixed with `--image-min-tokens`), and Qwen3-ASR on SGLang having no
`tokenizer.chat_template`. The latter surfaced only because the suite happened to send
chat requests. The 2026-10-28 review batch also hit harness defects:

- `12-vllm-contract` / `contract_check.py` sends chat and completions requests even
  when `VLLM_TEST_ENDPOINT=none` (embedding models fail the contract);
- the SGLang `contract-error-unknown-model` known deviation is stale (v0.5.22 now
  returns 404);
- `vllm-log-errors` counts transformers' `[ERROR] ... not documented` docstring lines
  from remote model code;
- runs dropped with "SSE error: connection lost". That was the vendored fork with no
  reconnect, plus a Wi-Fi switch on the client;
- "no stall detection while loading". This was evaluated and rejected under ADR 0005,
  because `status_msg` is not a liveness signal.

`contract_check.py` exists as three ~800-line copies (vllm.d, sglang.d, vllm-omni.d)
plus a llama copy. Only the vLLM copy has offline tests. The stale SGLang deviation is
the kind of drift that layout produces.

Forces:

- **Tests baked into an image only reach templates that pin a newer tag.** Model-library
  templates pin image tags. A probe fix in `vllm.d/` reaches a pinned template only
  after a rebuild, a promotion and a re-pin. ADR 0032 measured that lag at 67 days for
  derivatives.
- **Model capability is a fact about the model, not the image.** Vision, video, audio
  in/out, embeddings and reasoning fields are declared in vast_landing's
  `structure.yaml`. The image cannot infer them, and env flags such as
  `VLLM_TEST_ENDPOINT` have already selected tests wrongly.
- **ADR 0031 makes promotion gates deterministic.** No assertion may be made by
  sampling. The model-level oracles that find real bugs (did it read "7319", did it
  transcribe the clip) are content checks.
- **Non-chat templates are now arriving regularly.** Pyworker is being extended to the
  full OpenAI spec (speech, transcriptions, embeddings, image generation and edits;
  video deferred). A SGLang-Omni image and vLLM-Omni serverless wiring bring TTS, ASR
  and image templates. The vLLM QA gate cannot test non-chat endpoints at all: `VLLM_TEST_ENDPOINT` accepts only
  `chat` or `none`, so an embeddings or transcription template "passes" by being
  alive.
- **This repository is public.** vast_landing's template tooling carries host records
  (`verified_hosts.json`, `deverified_hosts.json`) and unreleased template hashes.

## Options considered

### 1. Move everything into the in-instance suites (`*.d`)

Port the probes into new baked scripts and delete vast_landing's tooling.

- Pro: one repo and one runner, following the existing pattern.
- **Rejected because:**
  - Fixes reach only images built after they land, so every pinned model-library
    template keeps the buggy suite.
  - Media fixtures would be baked into customer images.
  - Content checks would sit inside promotion gates, against ADR 0031.
  - Base-image still could not tell which modalities a model has.
  - Every probe tweak would cost an engine image build.
  - The architect, ops-reviewer and red-team each raised this independently.

### 2. Keep the status quo and upstream only the in-instance fixes

- Pro: the least churn.
- **Rejected because:**
  - It keeps a fork that has already diverged by about 1,040 lines, including the
    reconnect fix the 10-28 drops needed.
  - The probes that find model × engine bugs would stay uncommitted and run by hand.

### 3. Use ADR 0032 injection only

Deliver the modality probes into the instance at promotion time.

- Pro: decouples probe code from the image build.
- **Not chosen as the mechanism for model probes.** ADR 0032 injection still targets
  the promotion gate of a *candidate image*. The model-library question is whether
  *this template, with this model*, works on whatever tag it pins. That runs per
  template hash, outside image promotion, and needs semantic oracles that ADR 0031
  bars from gates.
- ADR 0032 remains the right channel for image-intrinsic checks that need not be
  baked. This ADR neither accepts nor rejects it.

### 4. Two layers, both owned here: the box checks what only it can see, the client checks every request/response (chosen)

The image proves what only the box can see — base/, supervisor and process state,
provisioning, engine readiness, log scans, port exposure. The launcher, which knows
what the template declares, proves everything observable as a request and a response:
inference, the OpenAI contract, and each template's model capabilities.

### 5. Keep inference and the contract check in the image (proposed, rejected)

Proposed on 2026-11-09 while building the external side: the in-image checks call the
engine on its own port (so an engine fault is not confused with a proxy fault), keep
the promotion gate free of network variance, stay re-runnable on a held box with
`runner.sh --manual`, and the contract check is mature (self-expiring deviations,
capability discovery, ADR 0031). The external layer would then be additive only, and
the in-image checks would read `OPENAI_ROUTES` to stop sending chat to non-chat
templates.

**Rejected, on the owner's reasoning: the test client has to know what it is testing
for.** An image cannot know which routes a template serves, so any request/response
test inside it either guesses or needs per-engine switches (`VLLM_TEST_ENDPOINT`,
`SGLANG_TEST_ENDPOINT`, `LLAMA_TEST_ENDPOINT`) to be turned off — and the contract check
had no switch at all, which is why it failed EmbeddingGemma 2. The launcher reads the
declaration (D2). Image promotion loses no inference coverage: CI tests each image with
a single chat-capable model, and the launcher checks chat for it. The advantages above
become accepted costs (Consequences).

## Decision

**All template test code lives in base-image, in two layers. The image checks what
only the box can see. The launcher — the client, which knows what the template
declares — checks every request and response: inference, the OpenAI contract, and the
template's model capabilities.**
Decided with the owner on 2026-10-29/30, one question at a time (D1–D9 below); option
5 (keep inference in the image) was proposed and rejected on 2026-11-09.

### Scope: on-demand only

A template test answers one question: *launched on demand, does this template serve
every capability it declares, with correct output, through the public endpoint?*

- Every template must work both on demand and in serverless mode. The **on-demand**
  test is what certifies the template as good.
- Serverless behaviour (pyworker routes, request parsing, benchmark generators, the
  score) is pyworker's to test, under its own tests.
- The existing in-image `20-serverless-pyworker` checks are ours to change when there
  is a real need. They are not redesigned as part of this work: one thing at a time.

### D1. Request/response checks run from the client; the image keeps what only the box can see

- **The client knows what it is testing for.** It reads the template's declaration
  (D2) and checks exactly those routes through the instance's public endpoint (Caddy
  and auth, the path users hit). An image cannot know what a template serves, so it
  does not test request/response behaviour at all.
- **Leaving the image:** the inference section of every `10-<engine>-serving` test,
  and the OpenAI contract check (`12-<engine>-contract`, D5). With them go the
  per-engine switches `VLLM_TEST_ENDPOINT`, `SGLANG_TEST_ENDPOINT` and
  `LLAMA_TEST_ENDPOINT`.
- **Staying in the image:** `base/`, supervisor and process state, provisioning,
  engine readiness ("the engine came up and answers on its own port"), the engine and
  sidecar log scans, port exposure, and serverless-worker internals.
- **Image promotion keeps an inference check.** CI tests each image with a single
  chat-capable model; the launcher checks chat (and the contract) for it, and those
  checks gate promotion once `require_tests` names them (D7 step 3.3).
- **The model-capability checks** (embeddings, transcription, speech, ComfyUI
  outputs) are the launcher's too; they need test media and answer checks, which never
  belong in an image.
- A launcher fix reaches every image tag on the next run, with no rebuild. Media
  fixtures stay in the repo, never in customer images.

### D2. Capabilities come from OPENAI_ROUTES — the declaration pyworker already reads

A template's capabilities are the routes it serves, and the template already declares
those for serverless: pyworker reads `OPENAI_ROUTES` (comma-separated routes) and
serves completions and chat when it is unset. The tests read the same variable, so
serverless and the tests cannot disagree about what a template is, and no second
declaration exists to drift.

- **Names are the routes'.** `/v1/chat/completions` → `chat`, `/v1/completions` →
  `completions`, `/v1/embeddings` → `embeddings`, `/v1/audio/transcriptions` →
  `transcriptions`, `/v1/audio/speech` → `speech`, and a name for every other route
  pyworker knows (`rerank`, `score`, `translations`, `images`, `image-edits`,
  `videos`, `audio-generate`, the batch routes). ComfyUI adds `comfyui-workflows`.
- **Resolution, first match wins:** `--capabilities` (a manual override); the
  template's `OPENAI_ROUTES` (an `--env` override beats the template); a ComfyUI
  image or `PROVISIONING_COMFYUI_WORKFLOWS` → `comfyui-workflows`; otherwise
  pyworker's default, `completions` and `chat`.
- **Every run says what it checked and why.** Its first result is
  `external/capabilities` (state `info`), e.g. `chat, completions (OPENAI_ROUTES
  unset: pyworker default)`.
- **Declaration problems are reported, never absorbed:** an unknown route or override
  name is an error; `VLLM_TEST_ENDPOINT=none` with no `OPENAI_ROUTES` is an error that
  says to declare the routes.
- **A refused-chat check is a guard, not a capability.** When chat is not declared,
  `external/undeclared/chat` sends one minimal chat request and expects a refusal:
  400, 404, 422 or 501 (D4).
- Inference was considered and rejected: scraping HF tags, `pipeline_tag` or
  `config.json` guesses wrong where it matters — HF tags misfiled EmbeddingGemma 2,
  and Qwen3-ASR has a chat template but SGLang cannot serve its chat while vLLM can.
  An earlier draft proposed a new explicit field in `structure.yaml`; `OPENAI_ROUTES`
  is that field, already carried into the template and already required for
  serverless.
- The static `ROOT/etc/vast_capabilities.d` fragments are not a source. They are
  wrong in both directions, because endpoint availability is a property of image +
  launch mode + loaded model.

### D3. One launcher, consumed at a pinned SHA

- vast_landing deletes its vendored `test_template.py` and consumes this repo's
  launcher and probes as a **`uv` git dependency at a pinned SHA**. The pin is bumped
  on purpose, in a one-line PR.
- That pin covers test tooling only. It does not change the rule that template
  provisioning URLs use base-image `main`.
- The `pcl/` copy is reconciled or removed in the same effort.

### D4. Structural checks gate promotion; answer checks gate model-library approval

- **Image promotion** gates on the in-image suite plus the launcher's structural
  checks — inference and the contract (D5) — once `require_tests` names them (D7 step
  3.3). All of them are deterministic, per ADR 0031. Answer checks never gate image
  promotion.
- **Model-library approval** requires every declared capability to pass its probes,
  answer checks included. A template cannot ship unless its declared capabilities
  pass.
- A reference-model cell that would let answer checks gate image promotion was
  considered and deferred until the probes have flake data.

On-demand coverage, built in the order templates are arriving:

| Capability | Structural check | Answer check |
|---|---|---|
| Chat / reasoning | contract as today | multi-turn history with `reasoning` returned separately from `content` |
| Vision, multi-image | an image request is accepted | reads a known token; uses every input, in order |
| Video input | a video request is accepted | describes the motion correctly |
| ASR | non-empty transcript | transcript fuzzy-matches the reference clip |
| TTS | decodable audio, non-zero length | ASR of the output matches the input text (round trip) |
| Embeddings | expected dimension; identical input gives an identical vector | a near pair ranks above a far pair |
| Image generation (OpenAI images API, ComfyUI) | valid image at the requested size | not blank or uniform; optionally a VLM caption contains the prompt subject |
| Video generation | valid container, expected frames and duration | not static |

- Every answer check has a positive and a negative offline fixture test.
- **Chat is checked both ways.** A template that declares chat must answer it. One
  that does not gets the `external/undeclared/chat` guard: one minimal request that
  must be refused with 400, 404, 422 or 501. A 2xx fails it; any other status, or no
  response, is an error, because it says nothing about chat. This keeps the signal
  that caught the Qwen3-ASR SGLang gap, which a plain skip would lose. The guard
  covers chat only and **always runs last**: probing an undeclared route can take a
  server down (seen live: one `/v1/completions` call killed a vLLM-Omni TTS server),
  and that must never cost the declared checks their result.
- **Declared but unchecked is skipped, never passed.** A declared route with no
  external check yet (`rerank`, `score`, `images`, …) is reported as skipped, as is
  `speech` on a song model (`SPEECH_KIND=music`) until its variant exists.
- **The launcher reports which probes ran, skipped or failed for each hash.**
- ComfyUI is covered by D8.
- This supersedes `VLLM_TEST_ENDPOINT` (`chat|none`) and closes the gap that the vLLM QA
  gate cannot test non-chat endpoints.

Built as of 2026-11-09 (all advisory, each verified live on a model-library template):

| Capability | Structural check | Answer check | Verified on |
|---|---|---|---|
| `chat` | `external/chat-inference`: HTTP 200, assistant message, completion tokens | — | Qwen3.5 0.8B, Qwen3-ASR (vLLM) |
| `completions` | `external/completions`: HTTP 200, text choice, completion tokens | — | Qwen3.5 0.8B |
| `embeddings` | `external/embeddings`: one vector per input, one dimension, no NaN or zero vectors, identical input → identical vector | `external/embeddings-ranking`: paraphrase above unrelated | EmbeddingGemma 2 |
| `transcriptions` | `external/transcriptions`: multipart WAV → non-empty `text` | `external/transcriptions-accuracy`: WER ≤ 0.25 against the committed reference clip | Qwen3-ASR (vLLM and SGLang) |
| `speech` | `external/speech`: a WAV that decodes and holds audio | `external/speech-audio`: not silent, plausible length for the text | VoxCPM2 |
| guard | `external/undeclared/chat`: 400/404/422/501 when chat is not declared (2xx fails, anything else is an error) | — | EmbeddingGemma 2, Qwen3-ASR (SGLang), VoxCPM2 |
| `comfyui-workflows` | D8 | D8 | FLUX.2 Klein, LTX-2.3 |

Not built yet: vision, multi-image and video-input answer checks for chat models;
the speech round trip through ASR (it needs a second endpoint); content checks for
generated video, audio and 3D.

### D5. One contract check, in the client, keyed on the declared routes

- The per-engine `contract_check.py` copies (vllm.d, sglang.d, vllm-omni.d, llama)
  are replaced by **one module in the launcher**, run through the public endpoint.
  Engine differences and known deviations become small data tables, each with
  offline tests; self-expiring deviations (ADR 0031 decision 6a) carry over.
- **Its assertions are ported, not dropped:** engine args, loopback binding,
  identity, token arithmetic, finish reason, prompt round trip, streaming, the
  completions route, error codes (unknown model, malformed body, context overflow),
  and capability discovery (tools, structured output). Each is keyed on the declared
  routes (D2): a template that does not serve chat is not sent chat.
- Assertions that genuinely need the box (loopback binding, the engine's own
  arguments) stay in the image. Loopback binding is checked today by `check_bind` in
  each engine's in-image contract suite; when an image's in-image contract copy is
  deleted, `check_bind` moves into that image's serving test in the same PR.
- The in-image copies stay until each image's `require_tests` has switched to the
  client's checks, in the same PR that deletes them, so no image is ungated mid-move.

### D6. Public/private line

base-image is a public repository.

- **Allowed here:** the launcher, the probes, the contract check, capability field
  names, and test media we generate ourselves. No third-party media with unclear
  licensing.
- **Never stored in any repo:** host data (host lists, machine IDs, machine records,
  suspect-host notes).
- **Kept in vast_landing:** unreleased template hashes, private test templates and
  `structure.yaml`. Capabilities cross over only as runtime arguments or template
  metadata.
- **Public CI logs: IP addresses are redacted.** Everything the launcher shows — its
  messages, test output, streamed engine logs (D9) — and its `--raw` JSON has every
  non-loopback IPv4/IPv6 address replaced with `[redacted-ip]`. The one exception is
  the raw JSON's `ssh` field, which the QA-fix loop reads to log in to a held
  (`--keep`) box and which CI (always `--destroy`) never produces.
- **Tokens:** GitHub Actions masks the repository secrets a workflow uses, wherever
  they appear; engine logs carry no credentials of their own, and the launcher never
  prints the instance token. Streaming engine logs in CI is therefore accepted — it is
  what a failed gate run needs to be diagnosed.

### D7. One thing at a time

Each step lands and is used before the next starts:

1. Commit the ad-hoc probes, unchanged, to the consolidation branch. They exist only
   in a session scratchpad today.
2. Ship two small fixes, each with a regression test that fails before the fix:
   remove the stale SGLang unknown-model deviation, and exclude transformers'
   docstring `[ERROR]` lines in `vllm-log-errors`.
3. Move inference to the launcher, then extend it:
   1. the launcher runs a chat inference check through the public endpoint alongside
      the in-image one, advisory only, until the two verdicts are seen to agree;
   2. capability-keyed checks for non-chat endpoints are added to the launcher, in
      this order: embeddings, ASR, TTS, then image generation. Video generation
      follows once its fixtures are written;
   3. each image's `require_tests` switches from the in-image inference and contract
      checks to the launcher's, in the same PR that deletes the in-image sections.
4. Switch launchers (D3), side by side, before the fork is deleted.
5. Port the contract check to the launcher (D5), then switch images one at a time
   (step 3.3). Before the first switch, add a launcher mode that runs the checks
   against an existing instance (`--instance <id>`), so the QA-fix loop can re-run
   them on a held box now that `runner.sh --manual` no longer covers them.
6. ~~Carry capabilities in the template~~ — superseded by D2: `OPENAI_ROUTES` is
   already carried in the template. The non-chat model-library templates still need it
   declared.

Progress (on the consolidation branch or local fix branches, none merged): step 1
done; step 2 done on two fix branches with regression tests and a new lint rule
(L117), plus Ray dashboard log capture; steps 3.1 and 3.2 built and verified live
(table under D4, D8, D9). Step 5's port is built: the contract runs from the client,
advisory, keyed on the declared routes, with prompt-roundtrip counted on the box over
a per-run SSH key. Since then, covered by offline tests and not yet re-run live: the
transport is hardened (http only when the port does not speak TLS, the scheme pinned
once it answers, nothing re-sent once it may have been delivered, redirects only to
the same origin, one deadline for the whole phase); a check that raises, or an SSH
session that cannot be set up, is reported as an error without stopping the rest;
and redaction covers addresses beside terminal escapes and inside JSON strings. The
`--instance` mode step 5 asks for is built. Still open before any check can gate:
step 3.3 (`require_tests` switches, once the in-image and launcher verdicts are seen to
agree) and step 4 (D3).

Preconditions for the gate switch. All five are required before any image's gate uses
external results:

1. **bind-loopback with a positive predicate in every engine — built.** The check
   lives in each engine's in-image contract suite (`<engine>.d/contract_check.py`, run
   by `12-<engine>-contract.sh`), not in the serving test. vLLM and vLLM-Omni already
   named the loopback addresses; SGLang and llama.cpp now do too. Lint L119 gates it.
2. **`--instance <id>` in the launcher — built.** It runs the in-image result
   retrieval and the external checks against an instance that is already running,
   and never stops or destroys it.
3. **`require_tests` cannot name a check that never runs — built for in-image tests,
   open for external checks.** L097 fails a `require_tests` entry naming an in-image
   test the image does not ship, and refuses an external check name, which
   `require_tests` can never be satisfied by. There is no workflow input yet that
   declares required external checks, so nothing exists to lint for them.
4. **`qa_verdict` reads external results — built, off by default.** With
   `--external-verdict`, failed and error block, skipped is listed, deviation is listed
   as documented, info is ignored, and a `--require-external` name that is absent or
   not passed blocks. No image or workflow uses it.
5. **Every run leaves a proof — built (D10).** Locally and in CI, each launcher run
   writes a proof directory; qa-gate uploads every attempt's proof for every caller.

### D8. ComfyUI: every workflow, every output node

If we ship a workflow, we verify it. `comfyui-workflows` runs **every** provisioned
payload (`/opt/comfyui-api-wrapper/payloads/`, read over the instance's Jupyter)
through the public API wrapper (`/generate/sync`, outputs inlined as base64), and
checks that each output node delivered what it is built to produce.

- **Every workflow, not a sample.** Running only one was considered and rejected:
  a template that ships five workflows is promising five. While the in-image suite
  still generates too, each workflow runs twice; that ends when the gate switches.
- **Output nodes are discovered, not listed.** ComfyUI's `/object_info` marks every
  output node (`output_node: true`), custom packs included. Each node's expected kind
  comes from a known table: `SaveImage` image; `SaveVideo`/`SaveWEBM` video;
  `SaveAnimatedWEBP`/`PNG` animation; `SaveAudio*` audio; `SaveGLB` 3D;
  `VHS_VideoCombine` from its `format` input; `Preview*` nothing returned (the wrapper
  drops preview files). An output node not in the table is an **error naming its
  class**, never a pass. If `/object_info` cannot be read, the table is used and the
  gap is reported.
- **Each node must deliver its kind**, matched by the `node_id` the wrapper puts on
  every file. A video node that delivers an image fails; a save node that delivers
  nothing fails; a video node fed audio (on the node, or via `CreateVideo`) must carry
  an MP4 sound track. Typing by file signature alone was rejected: it passes a video
  workflow that wrongly emits an image.
- **Content:** PNGs are decoded and must not be blank or uniform (luminance spread);
  other image formats use a compression-ratio proxy. Video, audio and 3D content
  checks are reported as skipped ("not written yet"), never passed. Outputs too large
  to inline are fetched over Jupyter.
- **Modifier payloads** (the wrapper's examples that name a built-in workflow instead
  of carrying one) are checked against the base workflow the modifier loads; an
  unresolvable one is an error.
- Old images and their bundled examples are out of scope: current images carry the
  base tests and provision their workflows through `PROVISIONING_COMFYUI_WORKFLOWS`.

### D9. One command

`test_template.py <hash>` is the whole interface; flags are overrides.

- The external checks always run, selected by D2. `--capabilities` overrides.
- **Readiness:** when the image has an engine serving test, the external checks run
  once it has run (passed or failed — a log-line failure must not hide a working
  engine). When the image has none at all (old images: `vastai/comfy:v0.18.2` ships
  no ComfyUI suite and "passed" without generating anything), the checks wait for the
  engine themselves — `/v1/models`, or the wrapper's `/health` plus a non-empty
  payload directory — up to 30 minutes; a timeout is a failure, not a skip.
- **Logs on by default.** The engine's log is streamed to stderr, chosen from the
  template's image (`/var/log/portal/<program>.log` for vLLM, SGLang, llama.cpp,
  vLLM-Omni; ComfyUI plus its API wrapper). An explicit `--log` wins; `--no-logs`
  streams nothing; an image with no known engine log streams nothing and says so.
- Results are reported in the log and as `external_checks` in `--raw` output,
  outside `tests`, so they do not change a verdict or exit code until D7 step 3.3
  switches an image to them. A model-library review reads them as required (D4).

### D10. Every run leaves a proof

A test run that leaves nothing behind cannot be checked afterwards. Every launcher run
that resolves its template writes a proof directory, on every exit path, from the
same atexit hook that guarantees teardown: `proof.json` (the record, with a schema
version), `summary.md` (a short table) and the streamed logs as streamed.

- **What it records:** the launcher's commit, the run's times, exit code and state as
  `--raw` reports them, the argv with secrets masked, the CI run it belongs to when
  there is one, the template and image (and digest when the instance reports one),
  descriptive hardware, capabilities and their source, and every in-image, external
  and contract result.
- **No host data, ever.** The record is built from an allowlist of fields, never from
  the `--raw` payload. No IP address, machine, host or offer id, SSH coordinates,
  mapped port, hostname or location appears in any proof file: every file, logs
  included, goes through the IP redaction and is scrubbed, as substrings, of the API
  key, the instance token (and any 8-character or longer prefix of it) and the host's
  identifying values. A test builds a proof from a run carrying all of them, including
  the in-image tests' own lines, and asserts none survives.
- **The instance id is recorded.** It is ephemeral, not host data (owner ruling), and
  it ties the proof to the run: `run.instance_id` and every attempt's id.
- **Integrity:** the sha256 of every file and of the canonical record;
  `verify_proof.py` recomputes them and refuses a schema it does not know.
- **CI:** qa-gate, the one path every gate runs through, uploads every attempt's proof
  for every caller (`qa-proof-<cell>-<inputs hash>-attempt<N>`, kept 90 days) and
  appends each summary to the job summary. The verdict-evidence artifact the promote
  workflows read (`qa-raw.json`) is uploaded as a copy without `machine_id`, `ssh` or
  token-named keys; the job's own copy stays whole, because the redraw keeps off a
  failed host with its machine id.

## Binding conditions

1. **Probes are committed before anything else** (D7 step 1).
2. **Launcher switch, side by side.** Before the vast_landing fork is deleted:
   - port or reconcile its unit tests (`test_template_launcher.py`), so offer sizing
     and disk-tolerance behaviour is not lost;
   - check that the flags vast_landing relies on exist (`--offer`, `--keep`, `--raw`,
     `--max-price`, `--env`, `--gpu`);
   - run both launchers on one model-library batch and compare verdicts;
   - run a client-vs-runner compatibility check against the oldest image tag any
     published model-library template pins.
3. **Public/private line** as in D6, including the IP-redaction tests.
4. **Gate hygiene:**
   - Launcher checks enter `require_tests` as advisory and become gating per image only
     after a clean-run streak.
   - A `require_tests` entry is renamed or moved only in the same change that updates
     it; a CI lint fails when an entry names a test that does not exist, so a move
     cannot silently ungate an image.
5. **Stall detection stays rejected (ADR 0005).** Slow loads are handled by tuning
   `QA_POLL_TIMEOUT` per workload unless a real liveness signal appears.
6. **Budget:** each capability's probe set names its GPU tier, expected minutes and
   cost ceiling before it runs in CI. Video and omni models must fit under the job cap.

## Consequences

**Positive**

- One source of truth for test code. The ~1,040-line fork and the per-engine
  `contract_check` copies go away.
- The test that knows what a template serves is the one that tests it: no per-engine
  switches, and no image test fails a template for a route it never declared.
- Model × engine defects are caught by design, not by luck.
- Probe fixes reach every pinned image tag at once.
- Non-chat templates (embeddings, ASR, TTS, image and video generation) are certified
  by their real output instead of "the process is alive".
- Promotion gates stay deterministic (ADR 0031).
- Media fixtures stay out of customer images.

**Accepted negative**

- **Two kinds of check to read.** A failure has to be read as "image" (structural) or
  "model on this template" (answer check).
- **A contract between repos.** vast_landing and base-image share the capability field
  names and the pinned-SHA bump. Changes to either need coordination.
- **Gate checks cross the network.** Inference and the contract check run through
  Caddy over the public internet; stream reconnects and ADR 0029 redraws absorb most
  of that.
- **Engine and proxy faults look alike from outside.** The in-image readiness check
  and log scans still point at the engine when it is the cause.
- **The contract check has to be ported**, assertion by assertion, with its
  deviations — real work, done once (D5).
- **A held box cannot fully test itself.** `runner.sh --manual` no longer covers
  inference or the contract; `--instance <id>` covers them from the client (D7 step 5).
- **Probe payloads cross the public internet.** Image, audio and video fixtures must
  stay small.
- **Answer checks carry a flake budget.** Video, TTS and image outputs are not fully
  deterministic. They need tolerance-based assertions and a retry policy, which is
  why they gate model-library approval and not image promotion.
- **The model library depends on a public repo for its test tooling.** Anything
  sensitive must be passed at run time, never committed.

## Owner rulings on the questions the build raised

- **Older pinned images are not tested.** Every image in the recommended set and the
  model library is rebuilt on release, and nothing older than the latest tag on merge
  day is tested. The engine table therefore stays keyed by image repository at the
  latest tag: no per-tag or version-range deviations.
- **`OPENAI_ROUTES` is the whole truth.** There is no separate signal for "the engine
  can chat but the template does not serve it". A template that does not declare chat
  and still answers it fails `external/undeclared/chat`; it either declares chat or
  turns it off.
- **Publication.** The branch is pushed when it is ready and fully tested locally,
  squashed first.
- **Model-library templates without `OPENAI_ROUTES`** (embedding, transcription and
  speech templates) get their routes declared in a follow-up, together with their
  serverless-ready flag. Until then they are tested with `--capabilities`.

## Open issues carried from the panel

- **Pass conditions still to be specified.** Test-engineer (`critical-gaps`): pass
  conditions now exist and are tested for chat, completions, embeddings,
  transcriptions, speech (structural plus a speech-like answer check) and ComfyUI
  image output (D4, D8). Still unwritten: the speech round trip through ASR, vision
  and video-input answer checks, and content checks for generated video, audio and 3D.
- **Public repo.** Red-team F3 (major): condition 4 addresses it, but nobody has yet
  audited what vast_landing's tooling would carry over.
- **Which "known gaps" are already fixed upstream.** Red-team F1 (major): this ADR
  records the SSE and stall answers, and replaces the `VLLM_TEST_ENDPOINT=none` quick
  fix with the capability-keyed checks (D4). The two remaining quick fixes were
  checked against `main` (both still present) and are on their own branches (D7
  step 2).
- **ADR 0032 is still Proposed.** If it is accepted, some of the in-image checks that
  remain under D1 may move from baked to injected. That does not change the external
  probes.

## What would reverse this

- **The network makes the gate unreliable.** If gate failures through Caddy outrun
  what reconnects and redraws absorb, or engine faults routinely cannot be told from
  proxy faults, moving the structural part of the contract back into the image
  (option 5) would be worth reconsidering.
- **Gates become content-tolerant.** If ADR 0031 is superseded to allow sampled
  assertions in gates, answer checks could gate image promotion directly, for example
  through the deferred reference-model cell (D4).
- **Ownership changes.** If the model-library catalogue moved into base-image, or test
  tooling moved to a private repo, the public/private condition would be redrawn.
- **The split costs more than it saves.** If, after adoption, answer-check failures
  are routinely misread as image failures, revisit it.
- **Pyworker stops declaring routes.** D2 rests on `OPENAI_ROUTES` being what
  pyworker serves from. If pyworker moves to a different declaration, the tests
  follow it; they must never keep a second one of their own.
- **Serverless becomes the certifying path.** If a template's serverless run, rather
  than on demand, becomes what certifies it, the scope section must be redrawn with
  pyworker.
