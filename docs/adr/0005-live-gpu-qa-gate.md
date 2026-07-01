# ADR 0005 — Live-GPU QA gate (template publish + instance test in CI)

- **Status:** Accepted (conditional — see Binding conditions; build sequenced from condition 9, first template ComfyUI)
- **Date:** 2026-06-26
- **Decision owner:** Rob Ballantyne
- **Process:** idea brief → critical review → scope collapse → synthesis → **final review of the written plan** (caught the selection-mechanism/code mismatch and the exit-code enforceability gaps, now folded into the Decision and conditions). The original "gate the whole fleet" framing was judged to manufacture false confidence and is a rejected alternative below.

## Context

CI today gates on **image build** only: a `build-<x>.yml` runs
`preflight → build (matrix base_image × arch → arch-suffixed tags in the STAGING
namespace) → merge-manifests (crane-assemble the multi-arch index at the
PRODUCTION tag)`. Nothing boots the image on a GPU before it is promoted to
users. We want a second tier — a **FULL pass** that means "the published artifact
actually ran on real hardware" — without manufacturing false confidence or
runaway GPU spend.

The machinery mostly exists (CON-1585): an in-instance harness
(`ROOT/opt/instance-tools/tests/`, SSE on port 10199, base health + the ADR-0006
exposure gate + per-image `<name>.d` functional tests), a stdlib client
`tools/template_manager/test_template.py` that launches a Vast instance from a
template hash, streams results, and tears the instance down, and
`tools/template_manager/create.py` that creates a template from
`<name>/{template.yml, README.md}`. Tooling scope/terms are in [ADR 0008](0008-template-publish-tooling.md);
the in-instance exposure check is [ADR 0006](0006-inadvertent-exposure-gate.md).

A critical review of the obvious design ("every image gets a live GPU test that gates
promotion") found it net-negative as framed:

- **Coverage cliff.** Only **5** images have a functional `*.d` test
  (`pytorch`, `comfyui`, `llama-cpp`, `external/vllm`, `external/sglang`); the
  other ~25 get boot-and-curl health checks. A whole-fleet gate spends GPU money
  to assert "QA'd" when it means "booted."
- **Skip-as-pass.** `external/vllm/.../vllm.d/10-vllm-serving.sh` *skips itself*
  when `VLLM_MODEL` is unset, and the runner still reports pass. The richest test
  in the repo is a no-op unless the QA template pins a real model.
- **Overloaded exit codes.** `test_template.py` emits `0/1/2/3`. `2` conflates
  genuine spot-market flake with template-not-found, API errors, and
  instance-crashed-mid-test (`error` final state). "All of 2 → inconclusive →
  promote" silently promotes real breakage.
- **No reaper.** `--destroy` runs inside the very process a runner
  cancellation/timeout kills; nothing out-of-band knows the instance ID, so paid
  instances leak.
- **Arch blind spot.** Vast GPU hosts are overwhelmingly x86; the gate validates
  only the amd64 staging tag while `merge-manifests` promotes a multi-arch index
  including the untested arm64 manifest.
- **Flake → rubber-stamp.** 2h default timeouts, 25 launch attempts, and a thin
  spot market make a whole-fleet gate slow and noisy enough to be ignored or
  overridden — worse than no gate.

## Options considered

- **A. Whole-fleet live-GPU gate.** Every image's promotion blocked on a live
  instance test. **Rejected:** for ~25 of ~30 images the harness is boot-and-curl,
  so the gate launders "booted" into "QA'd"; renting a GPU to run `curl localhost`
  is unfavorable vs a CPU/static check; the spend and latency invite rubber-stamping.
- **B. Static/container-only checks (no live GPU).** Lint + `docker run` smoke on
  a CPU runner. **Rejected as the *whole* answer:** cannot catch runtime
  regressions that need a GPU (a CUDA pin that breaks inference, a model that
  loads but generates garbage, an OOM under real load). Kept as the *floor* for
  the non-allowlist images (see Decision).
- **C. Narrow allowlist live gate + non-gating smoke (chosen).** Live GPU test
  *gates* promotion only for an allowlist of images whose `<name>.d` test exercises
  the product (and loads a real model for inference images), amd64-only; all other
  images emit a separate, weaker, **non-gating** smoke signal that does not claim
  "QA passed."

## Decision

Add a `qa` job on the seam between `build` and `merge-manifests`
(`merge-manifests` gains `needs: [..., qa]`). It runs on a cheap `ubuntu-latest`
runner — the GPU is the rented Vast instance, not the runner. The job:
`create.py` makes a **private, SHA-named, TTL-labelled** throwaway template
pointing at the freshly-built **amd64 STAGING** tag; `test_template.py` launches an
instance, runs the harness, streams results, destroys the instance; the job maps
the exit code to a verdict; then deletes the throwaway template.

**Scope is an allowlist, not the fleet.** The gate *blocks promotion* only for
images on a `qa-gating` allowlist — those whose `<name>.d` functional test
exercises the product. Inference images on the allowlist **must** have their QA
template pin a small real model, and the test must **fail (not skip)** if that
precondition is absent. Every other image emits a distinct, **non-gating** smoke
status (base health + exposure) that must not be presented as "QA passed."

**Verdict mapping (as shipped — distinct exit codes; auto-promote requires a
positive signal).** `test_template.py` emits a distinct code per outcome and a
`--raw` JSON carrying `state` + `got_result_event`; the workflow Verdict step keys
on both:

| exit | state | verdict |
|---|---|---|
| `0` **and** `got_result_event == true` | `passed` | **FULL pass** → allow promote |
| `0` **and** `got_result_event == false` | `passed` | **BLOCK** — a pass without a result event is not trustworthy |
| `1` | `failed` | **BLOCK** — a real in-instance test failed |
| `5` | `instance_error` | **BLOCK** — instance crashed mid-test (treat as the image) |
| `4` | `config_error` | **HARD ERROR** — template-not-found / bad `--env` / non-`vastai` image / auth / API error (CI bug — fix, do not promote) |
| `2` | `no_offers` | **inconclusive** — thin market (no box in the arch/VRAM band) |
| `3` | `bad_instance` | **inconclusive** — exhausted launch attempts |
| `130` | `interrupted` | treated as BLOCK (a cancellation, not a pass) |

"Inconclusive" (`2`/`3`) **never auto-promotes on the gating path** (manual
dispatch / post-merge): the Verdict step **holds** (exit 1) so a human looks. Only
the **unattended `schedule` path soft-passes** an inconclusive, so a thin 00:00
market doesn't block the nightly promotion of an unchanged image. A soft-pass is
**not silent**: the gate sets a `soft_pass` output and a `notify-soft-pass` job
pings Slack (`notify-slack.yml`), so a gate that chronically can't run (a too-tight
floor, a thin market) surfaces instead of decaying to a no-op on the release train.

**As shipped (cron gates too).** The narrower "non-gating smoke for non-allowlist
images" in option C below is *not yet built* — the only gate wired is the comfyui
allowlist gate, and it runs on **both** the `schedule` build and `workflow_dispatch`,
gating `merge-manifests` in each. So a scheduled build *does* gate promotion; the
only schedule-vs-dispatch difference is the inconclusive handling above (cron
soft-passes a thin market; dispatch holds). When non-allowlist images gain their
own non-gating smoke, that split lands; until then "cron = non-gating only" does
not apply.

**Offer selection — smallest viable box above mandatory floors (built).** Every
template declares a `compute_cap` floor (enforced by the template linter — a
template that omits it fails validation, so there is no floor-less path and no
default to be silently wrong). The tester restricts to amd64 (`cpu_arch ==
"amd64"`, a confirmed Vast offer field), then selects the **smallest VRAM above the
declared floor, then the lowest `compute_cap`** above its floor — the cheapest box
that still has the capacity and features the image needs. The VRAM search is
**hard-bounded above** at `VRAM_CEILING_MULTIPLIER × floor` (default 3×): an
"`>=8GB`" claim is never tested on a 96GB box (a template may set its own
`gpu_total_ram` upper bound to override). 3× admits the abundant 24GB consumer
tier to keep the market from going thin while still excluding the 40/80GB
datacenter cards — the band, not a price filter, is the cost control.

Generalisation is **directional — state it honestly:**
- *Upward in VRAM it holds (the primary axis).* More VRAM is strictly safer for OOM,
  so a pass at the (headroom-adjusted) VRAM floor implies bigger boxes pass — which
  is why VRAM dominates selection and the search is bounded close to the floor.
- *Across compute capability it holds because GPUs are backward-compatible* (sm_90
  runs sm_70 code): testing at the lowest capability **at or above the declared
  floor** generalises up to every higher one. The floor must encode the image's
  **feature target** (e.g. `compute_cap >= 890` for FP8/sm_89): a floor declared
  *too low* would test below the features the image actually uses and leave that
  kernel path promoted-but-untested. Mandatory declaration removes the floor-less
  hole; floor *correctness* is enforced separately (condition 10 — validated by a
  real passing run). (`compute_cap` is an integer ×100: 700 = sm_70, 890 = sm_89/
  FP8, 900 = H100, 1200 = B200.)

VRAM floors carry **headroom** (footprint × margin), not the model's resting size —
a closest-fit box OOMs on KV-cache/graph-capture. If no offer meets the floor the run
is a HARD ERROR (under-provisioned test), never a skip. Because every template carries
explicit floors, even the non-gating smoke selects a determinate, bounded GPU class
rather than a random-generation box, so it does not flap red for hardware reasons.
(The narrow VRAM band trades some availability for representativeness — a thin market
in the band surfaces as "no offers"/inconclusive, not a silent jump to a huge box.)

## Binding conditions

Non-negotiable; the gate must not be enabled until all hold. Each maps to a
surviving review finding.

1. **Out-of-band reaper exists and is proven.** A mechanism independent of the
   test process reaps paid instances after a simulated runner kill — a scheduled
   sweep that destroys QA-account instances older than N minutes **and/or** a
   server-side instance TTL/auto-destroy label set at launch. Without this the
   design leaks billable instances and is void.
2. **Verdict read from machine-readable output, not `$?` alone.** Today the tool
   collapses *instance-error*, *config/CI error*, *no-offers* and *interrupt* all
   into a single exit `2`, and `got_result_event` is in neither the exit code nor
   `--raw` — so the 4-way table is **not implementable against the current
   surface**. Before the gate is enabled the tool must emit distinct,
   machine-readable outcomes (distinct exit codes and/or a `--raw` JSON carrying
   `state` + `got_result_event` on *every* exit path, including the early
   `sys.exit(2)` config-error paths). CI reads that JSON: an `error` final state
   and config/CI errors are **hard-stops** (not "inconclusive"); auto-promote
   requires `passed` **with** `got_result_event == true`. (Tool change tracked in
   condition 9.)
3. **Allowlist gating + fail-not-skip.** The gate blocks promotion only for the
   `qa-gating` allowlist; inference images on it pin a real model and the test
   **fails** when the model precondition is unmet. Non-allowlist images get a
   non-gating smoke check that does not claim "QA passed."
4. **amd64-only, enforced.** The offer search filters `cpu_arch == "amd64"`; the
   tool never silently lands on an arm host. arm GPU instances are beginning to
   appear on Vast but their availability and reliability are not yet characterised,
   so arm64 is QA-untested at promotion and the status check must not claim arm
   coverage. An opt-in arm path (`--arch`/allowlist) is revisited once arm
   availability/reliability is understood — not before.
5. **Fork-PR safety verified, not just intended.** Live QA never runs on
   `pull_request`/`pull_request_target` from forks. Triggers are post-merge `main`,
   nightly schedule, or maintainer-approved dispatch — verified on the trigger,
   not asserted in prose. The QA `VAST_API_KEY` is a dedicated QA-account key with
   a **capped balance** (blast radius = balance) since Vast key scoping may be
   coarse.
6. **Concurrency cap on the QA account.** QA launches are serialized/staggered so
   a shared cron across ~22 workflows cannot self-DoS the account into 429s (which
   would otherwise become exit-2 inconclusive → mass silent promotion). The gating
   path is opt-in per promotion, not fanned out unattended.
7. **Cost ceiling.** Every launch carries `--timeout` and two complementary spend
   bounds: offer selection is bounded to a near-floor VRAM band
   (`apply_vram_ceiling`, 3× the declared floor) so a `>=8GB` claim is never tested
   on a 96GB box — the band bounds box *size* — and a per-run `--max-price`
   (`dph_total`) cap bounds the `$/hr` rate (VRAM size ≠ price). The cap is
   deliberately **generous** (default `$2.00`, was `$0.50`): a too-tight cap mostly
   produced `no_offers` inconclusives (→ schedule soft-passes) without lowering
   real spend, so it is set high enough to admit the near-floor band while still
   backstopping a runaway-priced offer. The gating smoke config is cheap (no large
   model download where avoidable); a per-run and per-day spend ceiling on the QA
   account is set and monitored as the blunt backstop.
8. **Plaintext-channel acknowledged.** The harness auth token is the instance
   `jupyter_token` streamed over `http://` on a public IP — accepted only because
   the box is a short-lived throwaway; the QA key is never round-tripped through an
   instance `--env` (the `*_pass/_token/_key/_secret` redactor would not catch a
   `VAST_API_KEY`-named value).
9. **Prerequisite work shipped first.** Landed: the `cpu_arch == "amd64"` filter and
   the VRAM-primary + bounded `compute_cap`-floor selection (condition 10), with unit
   tests. Still to build before the gate is enabled: `create.py`/`models.py`
   `--tag`/`--image` override (point a template at a staging tag); a re-added
   **scoped** template delete; the template-linter check that rejects a template
   missing a `compute_cap` floor; **distinct machine-readable outcomes** (exit codes
   and/or `--raw` `state` + `got_result_event` on every exit path, condition 2); the
   reaper (condition 1); and the per-arch **staging tag exported as a `build` job
   output** and consumed by `qa` — never recomputing the date-derived tag downstream
   (`STAGING_TAG_BASE` is built from `date -u` and can roll across midnight into a
   404). The gate is not enabled until these land with tests.

10. **Mandatory floors; smallest-viable bounded selection; floors validated.**
    Every template declares a `compute_cap` floor — the template linter rejects one
    that omits it, so there is no floor-less path and no default to be silently
    wrong. Selection is **VRAM-primary**: the smallest VRAM above the floor, then the
    lowest `compute_cap` above its floor; the VRAM search is **bounded above** at
    `VRAM_CEILING_MULTIPLIER × floor` so a small claim is not tested on a huge box.
    The `compute_cap` floor must encode the image's **feature target** (e.g.
    `compute_cap >= 890` for FP8), not just VRAM; a floor declared too low under-tests
    newer-arch kernels. VRAM floors carry headroom (footprint × margin). Each
    allowlist floor is **validated by a real passing run at that floor before gating
    is enabled** (a too-tight floor OOM-BLOCKs; a too-high floor or too-narrow VRAM
    band HARD-ERRORs/no-offers on the thin amd64 market — both brick the gate). An
    unmeetable floor is a hard error, not a skip.

11. **Each condition is merge-blocking, not prose.** Before `qa` gates an image,
    every condition above has a concrete enforcing artifact — the reaper a
    SIGKILL-and-assert-destroyed test (condition 1); fork-PR safety a CI assertion
    that the `qa` trigger excludes fork `pull_request` (condition 5); each allowlist
    floor a recorded passing run (condition 10). A condition without its artifact
    keeps the gate disabled for that image; conditions must not degrade silently
    into aspirations.

If any condition is refused, this decision is void.

## Consequences

- A genuine **FULL pass** tier for the handful of images where it means something
  (inference products run a real model on a real GPU on the actual published
  artifact), gating their promotion — while the rest of the fleet keeps an honest,
  cheap, non-gating smoke signal instead of a green check that overclaims.
- Real but bounded GPU spend, concentrated on the allowlist; a reaper and spend
  ceiling cap the downside.
- arm64 remains QA-untested at promotion (accepted, stated) until a separate path
  exists; consistent with the current amd64-pinned reality of many derivatives.
- New CI surface and a QA account to operate; orphan/spend monitoring becomes an
  ongoing cost.
- **Honest marginal value.** Over the cheaper option B (CPU `docker run` + static
  import/CUDA-availability checks + the existing boot-and-curl), the live gate's
  *only* additional catch is the genuinely-runtime-GPU class — "model loads but
  OOMs or emits 0 tokens under real load" (the vLLM test does assert
  `completion_tokens > 0`) — for the ~5 allowlist images. That is real signal, but
  it is a lot of standing surface (QA account, reaper, monitoring, ~6 tool
  features, per-model floor tuning) for a handful of images. If the team will not
  also commit to keeping floors tuned and the reaper tested, this rots into a
  flaky red check that gets `[skip qa]`'d — strictly worse than option B. The
  go/no-go on building it should weigh that explicitly.

## Rollout to other images — scale-readiness

The comfyui gate is the first consumer; extending it to other images (the ones with
a real `<name>.d` functional test — vllm, sglang, llama-cpp, pytorch) must satisfy
these, or the gate's safety properties invert at scale:

- **Reaper ceiling scales with the allowlist.** `--max-reap` is a runaway backstop,
  **not** the primary guard — the `--label` scope is (only QA-stamped instances are
  ever candidates). The default (30) is sized above full-rollout concurrency
  (images × matrix cells × in-flight); raise it as images are gated. A ceiling sized
  for one image fail-closes into a leak once concurrency exceeds it.
- **One QA account → serialize launches.** All qa jobs share the
  `concurrency: qa-vast-account` group so concurrent builds can't 429-storm the
  single capped key — a 429 maps to `no_offers`/inconclusive, which the schedule
  path soft-passes, i.e. silent promotion. Onboarded images **must** join the group
  **and** stagger their build crons (the group alone drops excess matrix cells under
  contention → those images held, not tested).
- **Smoke model ≠ production model.** Mirror the production template's *launch path*
  (runtype, ports, PORTAL_CONFIG, args) but **not** its model/floors: production
  models are large (e.g. llama-cpp's default is a ~35B GGUF) and would brick the
  small-box selector (OOM or no-offers). Each image pins a deliberately tiny model
  with its **own** `compute_cap`/VRAM/`cuda_max_good` floor + disk, validated by a
  real passing run (condition 10). Note: vllm/sglang/llama deliver the model via a
  `*_MODEL` env + serve-time HF download, not comfyui's `PROVISIONING_COMFYUI_WORKFLOWS`
  mechanism — the onboarding step is per-image, not a uniform "mirror the template."
- **Multi-GPU sub-tests are skipped by the single-box selector.** `pytorch.d`'s NCCL
  test only runs on `num_gpus > 1`; the smallest-viable-box selection gives one GPU,
  so it self-skips. Either pin `num_gpus >= 2` for that image (cost) or scope its gate
  as single-GPU-only and state it (same honesty as the amd64-only caveat) — do not let
  a multi-GPU regression promote as a FULL pass.
- **Generalise from two, not one.** Extract a reusable `qa-gate.yml` workflow only
  after a second, structurally-different consumer exists (build matrices differ:
  comfyui's static `base_image` list vs vllm/sglang's dynamic `{tag,cuda}` JSON vs
  llama's weekly single-base) — generalising from comfyui alone yields a leaky
  abstraction.

## What would reverse this

- The harness gaining real functional tests across the fleet (so the allowlist
  stops being tiny) — then a broader gate becomes defensible and this narrow scope
  is revisited.
- Evidence the gate catches little that a cheaper CPU `docker run` + static checks
  miss — then collapse to option B and drop the live GPU spend.
- Vast offering first-class ephemeral/TTL instances or scoped keys that make the
  reaper and capped-balance conditions trivial — simplifies, doesn't reverse.

## Note on ADR numbering

ADRs 0002–0008 are spread across CON-1585 feature branches not yet merged
together (see [ADR 0008](0008-template-publish-tooling.md)); reconcile the
sequence when they land.
