# ADR 0051 — Recommended templates are admitted by where their compute and their state live

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

Many popular apps are **front ends**. SillyTavern, Open Notebook and AnythingLLM do
little computation themselves. They drive a model engine through an OpenAI-compatible
API and accumulate the user's data: chat histories, characters, notebooks, document
workspaces. Shipping one as a template puts a web app on a GPU bill, and puts long-lived
personal data on a machine the user is expected to throw away.

Two facts about the platform shape this:

- **Persistence.** An instance's filesystem survives stop and start but is lost when the
  instance is destroyed. `$WORKSPACE` can be backed by a volume that survives, but the
  volume is optional and many users never attach one.
- **Catalogue structure.** The recommended page is now **apps only**. Model-first
  offerings ("model X preloaded in ComfyUI") belong in the model library, where the user
  picks a model and it is launched in a suitable engine. Model-library entries are
  expected to work serverless, through an engine worker.

Open WebUI is already shipped, bundled with Ollama, and is exactly the case this
question is about.

## Options considered

**A. Admit any popular app with a browser UI.** The simplest rule, and it follows demand
directly. Rejected: it admits front ends whose only GPU need is a remote API, so the user
pays GPU rates to host a web page. It also places users' long-lived data on disposable
machines, where one destroy loses months of chats or characters. Popularity measures what
people want to *use*, not what belongs on a rented GPU.

**B. Admit only apps with no bundled front end; ship engines and expect users to bring
their own UI.** The tidiest boundary. Rejected: it abandons the target user. A one-click
complete app *is* the product for the non-expert, and an engine without a UI is what the
base image and model library already provide.

**C. Admit by where compute and state live (chosen).** Two tests, applied together. It
keeps GPU-native apps, including their own UIs, and sends state-heavy front ends to the
user's own machine, connected to a Vast endpoint. Its cost is the dissent recorded under
Consequences.

**D. Admit front ends, but require a `$WORKSPACE` volume so their state survives.**
Considered as a way to keep state-heavy front ends. Rejected for now: it adds a mandatory
step to the one-click path (the exact barrier the page exists to remove), still ties the
data to a region's storage instead of the user's machine, and does nothing about paying
GPU rates for an app that does not use the GPU.

## Decision

An app is admitted to the recommended templates page only if it passes both tests.

1. **Compute test.** The app itself needs the rented GPU **in the same box**: its model
   engine runs in-process, or is bundled in the image and driven by the app. An app whose
   only GPU need is calling a model elsewhere fails.
2. **State test.** The app is **not primarily a store of the user's long-lived personal
   data** (chat histories, characters, notebooks, document workspaces). Working files
   and outputs of a job (a trained model, a generated video) are not "long-lived personal
   data" in this sense. They are the results the user came for and downloads.

Apps that fail either test belong on the user's own machine, pointed at a Vast endpoint
through the model library's OpenAI-compatible routes. The model library documents that
route for the common front ends.

Model-first presets are never recommended-page entries. They go to the model library.

**Recorded exception: Open WebUI** (as shipped, bundled with Ollama). It passes the
compute test, since it is the chat window onto an engine in the same box. It fails the
state test, because its history, uploaded documents and users are lost on destroy. It
stays as the single one-click starter for local LLM chat. **It is not a precedent.** No
further app is admitted on the strength of this exception.

**Applied to the current shortlist:**

| App | Compute | State | Verdict |
|---|---|---|---|
| Applio (voice conversion and training) | In-process | Job outputs | Admit |
| Voice studio bundle (licence-clean TTS / cloning models) | In-process | Job outputs | Admit |
| KoboldCpp (engine with its built-in UI) | Is the engine | Light | Admit |
| OneTrainer | In-process | Job outputs | Admit |
| LLaMA-Factory | In-process | Job outputs | Admit |
| SoniTranslate (dubbing pipeline) | In-process | Job outputs | Admit |
| SillyTavern | Remote API | Characters, chats | Do not ship; laptop + endpoint |
| Open Notebook | Remote API | Notebooks | Do not ship; laptop + endpoint |
| AnythingLLM | Remote API | Workspaces | Do not ship; laptop + endpoint |

Admission under this rule is necessary, not sufficient. Each admitted app still needs a
licence screen (code **and** model weights) and its own design review before it is built.

## Binding conditions

1. **The laptop route must exist before it is used as a reason.** The "do not ship"
   verdicts send users to the model library's OpenAI-compatible routes. Those routes
   depend on changes not yet merged (vast-ai/vast-cli#517 and vast-ai/pyworker#93). Until
   they ship, a rejected front end is rejected *without* a supported alternative; that
   must be stated honestly wherever the verdict is communicated, and the verdicts are
   re-checked when the routes land.
2. **The model library publishes a connection guide** for at least the front ends this
   ADR turns away (SillyTavern, Open WebUI, AnythingLLM). Turning a user away with no
   instructions is not this decision.
3. **The exception list stays at one.** Admitting a second state-heavy front end requires
   a new ADR that supersedes this one, not an addition to this one.

## Consequences

**Positive**
- A single, checkable reason to say yes or no to a candidate app, so the catalogue grows
  by a rule instead of by enthusiasm.
- GPU rental is sold for what uses the GPU; users do not pay GPU rates to host a web page.
- Users' long-lived data stays on their own machine, where a destroyed instance cannot
  take it.
- The model library gains a clear second role: the endpoint that front ends on users'
  machines connect to.

**Accepted negative**
- Some of the most popular apps in the survey are excluded from the page, including
  SillyTavern, the default roleplay front end.
- Open WebUI remains a visible inconsistency, kept by deliberate exception.
- Until binding condition 1 is met, excluded front ends have no supported path at all.

**Unresolved objection, recorded as raised.** The target user is the person who will not
install anything locally. For them, "install SillyTavern and paste an endpoint URL" is
exactly the step that makes them leave, and a bundled one-click front end *is* the
product. If that describes most of the audience, this rule trades customers for
tidiness. This was not settled when the decision was taken. The evidence that would
settle it is template deploy counts: whether Open WebUI is among the most-deployed
templates (evidence that bundled front ends are what this audience wants, despite the
state problem) or not. Those counts are pending.

## What would reverse this

- **Deploy counts showing bundled front ends dominate demand** — specifically, Open WebUI
  among the most-deployed recommended templates. That would support option D (admit
  front ends, solve state) over this rule.
- **Volumes becoming the default** for recommended templates, which removes most of the
  state-test rationale. The compute test would still stand.
- **The OpenAI-compatible routes not shipping**, or shipping in a form common front ends
  cannot use. The laptop route is this decision's premise for every rejection.
