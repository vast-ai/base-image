# ADR 0051 — A recommended template is an app that brings its own model serving

- **Status:** Accepted (conditional)
- **Date:** 2026-10-05
- **Decision owner:** Rob Ballantyne

## Context

The recommended templates page needs to grow, and the audience it serves is the
non-expert who wants a complete app in one click. Anyone comfortable with an API or an
install script is better served by the base image. A gap survey of popular self-hosted
AI apps (one-click launcher catalogues, other GPU clouds' template lists, Hugging Face
Spaces) produced a shortlist, and the shortlist surfaced a question this repo has never
answered: which kinds of app belong on a rented GPU at all?

Many popular apps are **clients**. SillyTavern, Open Notebook and AnythingLLM run no
model themselves: they connect to a model server someone else runs, through an
OpenAI-compatible API. A user without a GPU can install one on a laptop, point it at an
endpoint, and get the full experience. Shipping one as a template rents a GPU to host a
web page. Clients also tend to accumulate the user's data (chat histories, characters,
notebooks, workspaces) on a machine the user is expected to throw away.

Two facts about the platform shape this:

- **Persistence.** An instance's filesystem survives stop and start but is lost when the
  instance is destroyed. `$WORKSPACE` can be backed by a volume that survives, but the
  volume is optional and many users never attach one.
- **Catalogue structure.** The recommended page is now **apps only**. Model-first
  offerings ("model X preloaded in ComfyUI") belong in the model library, where the user
  picks a model and it is launched in a suitable engine. Model-library entries are
  expected to work serverless, through an engine worker.

## Options considered

**A. Admit any popular app with a browser UI.** The simplest rule, and it follows demand
directly. Rejected: it admits clients a user could run on any laptop, so the user pays
GPU rates to host a web page, and it places long-lived user data on disposable machines.
Popularity measures what people want to *use*, not what belongs on a rented GPU.

**B. Admit only engines; expect users to bring their own UI.** The tidiest boundary.
Rejected: it abandons the target user. A one-click complete app *is* the product for the
non-expert, and an engine without a UI is what the base image and model library already
provide.

**C. Admit by state: reject any app that is primarily a store of the user's long-lived
data.** An earlier draft of this ADR made this the deciding test. Rejected on review: it
cannot be decided ("primarily" has no criterion), and it mis-sorts apps that clearly
belong. text-generation-webui (oobabooga) and KoboldCpp keep chats, characters and
stories, yet both ship their own model serving and are exactly what a GPU rental is for.
A rule that rejects them is the wrong rule. State matters, but as an obligation on
admitted apps, not as the gate.

**D. Admit clients, but require a `$WORKSPACE` volume so their state survives.**
Rejected: it adds a mandatory step to the one-click path (the barrier the page exists to
remove), and does nothing about renting a GPU to run an app that does not need one.

**E. Admit by whether the app brings its own model serving (chosen).** One question,
answerable from the app's design: can a user without a GPU run this app standalone, by
pointing it at a model server it does not run? It is a property of the app, not of how
we package it — bundling an engine beside a client does not change what the client is.

## Decision

**The test.** An app is admitted to the recommended templates page only if it **brings
its own model serving**: it runs its models in-process, or ships and manages its own
model server (oobabooga's loaders, KoboldCpp's engine, the ComfyUI backend SwarmUI
installs and manages itself on first run). An app that is a **client of a model server it does not run** fails, even if
we bundle a server beside it — a user without a GPU could run that client on their own
machine against an endpoint.

The test asks what the app *is*, not what hardware it can run on. An app that runs its
own models passes even if those models can run on a CPU (Whisper, Kokoro): it is still
doing the model work itself.

Clients that fail belong on the user's own machine, pointed at a Vast endpoint through
the model library's OpenAI-compatible routes. Model-first presets are never
recommended-page entries; they go to the model library.

**State is an obligation, not a gate.** An admitted app that accumulates user-authored
content across sessions (chats, characters, stories, galleries, voice profiles) must keep
that content under `$WORKSPACE`, so that an attached volume preserves it, and its
template readme must say plainly that the content is lost on destroy without a volume.
Downloadable results of a job (a trained model, generated audio or video) carry no such
obligation beyond normal `$WORKSPACE` output paths.

**Recorded exceptions.** These fail the test and stay by decision. **They are not
precedents**; adding one requires a new ADR that supersedes this one.
- **Open WebUI** (as shipped, bundled with Ollama). Open WebUI is a client; the Ollama
  beside it is our packaging. It stays as the one-click starter for local LLM chat.
- **Langflow** (as shipped, bundled with Ollama). Langflow is a client of model servers.
  It stays for now and is **expected to be retired**; when it is, it leaves this list,
  and nothing replaces it here.

**Applied to the current catalogue:**

| Template | Brings its own serving? | Verdict | State obligation |
|---|---|---|---|
| ComfyUI, A1111, Forge, Fooocus, InvokeAI, Wan2GP | Yes, in-process | Stays | InvokeAI: boards/gallery |
| SwarmUI | Yes, installs and manages its own ComfyUI backend | Stays | — |
| Kohya, FluxGym, ostris AI Toolkit, Unsloth Studio | Yes, training in-process | Stays | Unsloth Studio: chat |
| oobabooga (text-generation-webui) | Yes, its own loaders | Stays | Chats, characters |
| ACE-Step, Voicebox, Whisper WebUI | Yes, in-process | Stays | Voicebox: voice profiles |
| AIO Studio | Yes (a bundle of the above) | Stays | As its apps |
| vLLM, SGLang, Ollama, llama.cpp | They are the serving | Stays | — |
| Linux desktop, Unreal Pixel Streaming, PyTorch/TensorFlow/Jupyter | Yes, GPU work on the box | Stays | — |
| **Open WebUI** | **No — client of the bundled Ollama** | **Recorded exception** | Chats, documents |
| **Langflow** | **No — client of model servers** | **Recorded exception; retirement expected** | Flows |

**Applied to the current shortlist:**

| App | Brings its own serving? | Verdict |
|---|---|---|
| Applio (voice conversion and training) | Yes | Admit |
| Voice studio bundle (licence-clean TTS / cloning models) | Yes | Admit |
| KoboldCpp | Yes, it is an engine with its own UI | Admit (state obligation: stories, chats) |
| OneTrainer | Yes | Admit |
| LLaMA-Factory | Yes | Admit. A previous image passed QA and its source was deleted only because it existed to exercise the image tooling, not for any fault. |
| SoniTranslate (dubbing pipeline) | Yes | Admit |
| SillyTavern | No, client | Not admitted; laptop + endpoint |
| Open Notebook | No, client | Not admitted; laptop + endpoint |
| AnythingLLM | No, client | Not admitted; laptop + endpoint |

Admission under this rule is necessary, not sufficient. Each admitted app still needs a
licence screen (code **and** model weights) and its own design review before it is built.

**Where this is applied.** At design review of a new image in this repo, and at catalogue
review when a template is published to the recommended page. There is no linter rule:
"brings its own serving" is a judgement about an app's design, not a static property of a
Dockerfile.

## Binding conditions

1. **The laptop route must exist before it is used as a reason.** The "not admitted"
   verdicts send users to the model library's OpenAI-compatible routes, which depend on
   changes not yet merged (vast-ai/vast-cli#517 and vast-ai/pyworker#93). Until they
   ship, a client is "not admitted" with no supported alternative, and must be described
   that way. The decision owner re-checks the client verdicts when those changes merge.
2. **The model library publishes a connection guide** for the clients this ADR turns
   away: SillyTavern, Open Notebook and AnythingLLM.
3. **The exception list is the two named above, and it only shrinks.** Retiring Langflow
   removes it; adding any app requires a new ADR that supersedes this one.
4. **The state obligation is verified, not assumed,** for each admitted app that
   accumulates user content, the next time its image is changed. This ADR does not claim
   the existing images already comply.

## Consequences

**Positive**
- One question decides admission, and it is answerable from the app's design rather than
  our packaging.
- GPU rental is sold for apps that need it; clients go where they run best, on the
  user's machine.
- Engines with their own UIs (oobabooga, KoboldCpp) are admitted, which a state-based
  rule would have wrongly rejected.
- The model library gains a clear second role: the endpoint that clients on users'
  machines connect to.

**Accepted negative**
- Some of the most popular apps in the survey are excluded, including SillyTavern, the
  default roleplay front end.
- Open WebUI and Langflow remain visible inconsistencies, kept by deliberate exception;
  Langflow is expected to be retired.
- Until binding condition 1 is met, excluded clients have no supported path at all.
- Admitted apps can still lose user content on destroy; the state obligation reduces this
  only for users who attach a volume.

**Unresolved objection, recorded as raised.** The target user is the person who will not
install anything locally. For them, "install SillyTavern and paste an endpoint URL" is
exactly the step that makes them leave, and a bundled one-click client *is* the product.
If that describes most of the audience, this rule trades customers for tidiness. It was
not settled when the decision was taken. Template deploy counts are the evidence, but a
raw count cannot settle it alone: Open WebUI's deploys mix demand for the chat UI with
demand for the Ollama bundled in it. The comparison that can is Open WebUI against the
Ollama-only template, by deploys and by median instance lifetime.

## What would reverse this

- **Open WebUI deploying at least twice as often as the Ollama-only template,** with
  comparable instance lifetimes. That is demand for the bundled client itself, and would
  support admitting clients with a state solution (option D) over this rule.
- **The OpenAI-compatible routes not shipping,** or shipping in a form common clients
  cannot use (for example, without working streaming). Every client verdict rests on
  that route.
- **Volumes becoming the default** for recommended templates. This removes the state
  obligation's rationale; the serving test would still stand.
