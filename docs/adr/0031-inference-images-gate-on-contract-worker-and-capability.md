# ADR 0031 — An inference image's gate asserts the contract, the worker and the declared capabilities

## Status

Accepted. Extends ADR 0005 (live-GPU QA gate) and ADR 0006 (advisory-then-gating
ramp). Supersedes nothing. Records the outcome of a design review held while the
serverless worker was being split per engine.

## Date

2026-08-21

## Context

The vLLM image is promoted on the strength of one live-GPU cell. That cell's only
behavioural claim is vacuous.

`vllm.d/10-vllm-serving.sh` sends three chat prompts and fails **only if every one
of them produced zero completion tokens**. One token from one prompt passes the
cell. Nothing asserts the shape of a response, the error semantics, streaming,
token accounting, or the bind interface. A model being served that is *not* the
one requested is a WARN, and the check behind it is
`any(want in mid or mid in want ...)` — a bidirectional substring test that
matches a base model served under an instruct name.

That bar was not designed. `git show 2c0a51a` shows it as collateral of a fix for
reasoning models, which only needed the `content` -> `completion_tokens` change;
the "any one of N" weakening came with it.

Three structural facts make this worse than it reads:

1. **The gate has no fail-not-skip protection at either layer.**
   `build-vllm.yml` passes no `require_tests`, and `templates/vllm-qa` declares no
   `INSTANCE_TEST_REQUIRE_PASS`. Line 15 of the test is
   `[[ -n "${VLLM_MODEL:-}" ]] || test_skip`. The whole test can vanish and the
   image promotes green. ADR 0005 named this file, by name, as the canonical
   skip-as-pass hole in June 2026; base and pytorch closed it, vLLM never did.
   `linter.py` scopes L057 to `img.cls == "base"` with a comment recording that
   comfyui-qa and vllm-qa have the same hole.

2. **`retries: 0`.** The vLLM and comfyui gates never opted into ADR 0029's
   redraw, so the rule that ADR 0029 condition 5 says must not be forked per
   image is already forked per image.

3. **The serverless mode has never executed anywhere.** No template sets
   `SERVERLESS=true`, so `vllm.d/20-serverless-pyworker.sh` and
   `base/85-serverless-services.sh` have run zero times. This was recorded in
   ADR 0006 and deferred pending ADR 0019's fail-not-skip work, which has landed.

And the thing that prompted the review: the serverless worker is being split from
one shared `openai` worker into per-engine workers. Reading it shows the split is
not cosmetic — `workers/openai/worker.py` hardcodes **our** image's internals:

    MODEL_SERVER_PORT          = 18000
    MODEL_LOG_FILE             = '/var/log/portal/vllm.log'
    MODEL_LOAD_LOG_MSG         = ["Application startup complete."]
    MODEL_ERROR_LOG_MSGS       = ["INFO exited: vllm", "RuntimeError: Engine", ...]

That is a two-sided contract — a port, one of our log paths, and our supervisord
message strings, matched as literals — with no test on either side. If we rename
the log, the supervisor program, or the internal port, the worker silently stops
detecting load and error, and nothing in either repo notices.

## Options considered

### A. Harden the existing prompt loop (rejected as sufficient, adopted as a first step)

Raise the bar from "any of N" to "all of N", turn the model mismatch into a
failure, add the missing required-pass wiring.

Cheap, no new surface, and it closes the two embarrassing holes. Rejected as the
destination because it still asserts nothing about what the image actually
provides: an embedding model runs with `VLLM_TEST_ENDPOINT=none` and asserts
literally nothing, and a broken tool parser, structured-output backend or
multimodal preprocessor all promote green.

### B. Behavioural gate on the deployed model (rejected)

Richer prompts and quality heuristics against whatever `VLLM_MODEL` names.

Rejected because there is no oracle. Every assertion degrades to a threshold
nobody can defend, and a non-deterministic assertion on an all-or-nothing gate
that redraws on any failure is a machine for laundering flakes — ADR 0029
condition 1 already accepts that one lucky redraw ends the loop, and this would
add to it. It has also already failed here once: `2c0a51a` is the exemption cycle
in a single commit, and the file now carries three separate opt-out mechanisms
(`VLLM_TEST_ENDPOINT`, a log-error exclusion regex, the weakened threshold).

### C. Declaration-driven capability matrix (rejected)

The template declares `VLLM_CAPS`; each named capability runs its tier.

Rejected because it fails in the direction that matters. An author who forgets to
declare `tools` after enabling `--enable-auto-tool-choice` gets a green gate over
an untested feature — skip-as-pass recreated one level up. Declarations rot
toward the empty set, because the empty set is always green.

### D. Contract, worker, and discovered capabilities (chosen)

Assert the three things that are genuinely properties of THIS IMAGE, each by a
mechanism that is deterministic by construction.

## Decision

**An inference image's gate asserts (1) the API contract, (2) the serverless
worker reaching a score, and (3) capabilities discovered on the box — and it
asserts none of them by sampling.**

### 1. Deterministic assertions only

Every required assertion is *forced*: token arithmetic, `max_tokens=1`, a
grammar, a named tool, a status code, a socket address. Explicitly refused, and
these refusals are binding:

- content matching ("2+2" -> "4") — that is model competence, and the model is
  chosen per template
- non-empty `content` — reasoning models legitimately return none
- bitwise determinism across two greedy requests — vLLM is not batch-invariant
- latency or tokens/sec thresholds — host-dependent on a market where ADR 0029
  exists precisely because hosts vary
- similarity *magnitudes*; orderings only

### 2. Identity, by round trip rather than by fixture

The served id must EXACTLY equal `--served-model-name` or `VLLM_MODEL`, as a
failure not a WARN. Then: load the tokenizer from the same resolved snapshot the
server loaded, apply the chat template locally to a fixed probe, and assert the
local token count equals `usage.prompt_tokens` from a `max_tokens=1` request.

Exact, deterministic, needs no golden data, works on any model, and it detects a
substituted model, a broken or silently-replaced chat template, and BOS
double-add — the one defect class in this family that has real precedent.

The QA model is additionally pinned by `--revision`, because `VLLM_MODEL` names a
floating third-party ref today.

### 3. Two cells, both on-demand, from ONE template

A serverless-capable image gets a **standard** cell and a **serverless** cell.
Both are ordinary on-demand rentals; the serverless one differs only by
`SERVERLESS=true` in env. The autoscaler path itself is out of scope — this gates
the instance-side behaviour that setting entails.

**The serverless cell runs on every CUDA variant** — the same matrix as the
standard cells. This SUPERSEDES the original text of this decision, which
specified one serverless cell not crossed with the matrix, on the argument that
the serverless delta is overlay and worker wiring and therefore orthogonal to the
CUDA minor.

That argument is sound and it was not the whole question. An image is promoted
**per variant**, so a variant whose serverless mode was never booted is promoted
on an inference — and "orthogonal" is exactly the class of assumption a gate
exists to stop us making. The extra GPU cell per variant is a deliberate, accepted
cost, chosen over stating the gap as a limitation.

Reversing this decision also removed a quieter defect in its implementation. The
single cell derived its tag from `fromJson(merge-matrix)[0].cuda` — the FIRST
matrix entry, positional rather than chosen. Which variant serverless was tested
on was therefore decided by matrix ordering, and a reordering upstream would have
moved it with nothing announcing the change. Running the whole matrix makes that
question moot rather than merely documented.

**One template, two cells — not two templates.** A second template file is the
obvious way to express a second cell and it is the wrong one: the two drift, and
the only difference that matters stops being visible in the diff. The serverless
cell overrides `SERVERLESS=true` and the worker env at launch. This required one
fix in the client: `test_template.py` detected serverless from the TEMPLATE's env
and onstart only, so a cell whose flag arrives as an override launched with
`is_serverless` false. That happened to be harmless solely because the vLLM QA
template already carries `OPEN_BUTTON_TOKEN=1` — it worked by coincidence, not by
rule. An override *is* the instance's environment and is now read as such.

**The gated path is the SUPERVISOR path.** There are two ways a worker gets onto
a box, and the distinction was invisible until this cell was built:

- **supervisor path** — `onstart: entrypoint.sh`, `SERVERLESS=true` as the
  trigger, and base `pyworker.sh` performs the bootstrap as a supervisord
  program. Every new template uses this.
- **onstart-curl path** — `onstart: entrypoint.sh & ; curl .../start_server.sh | bash`,
  which every *shipped* serverless template uses today. It stays supported for
  backwards compatibility and is **not** what this gate exercises. Recorded as a
  coverage limitation, not implied coverage.

This distinction was not academic. `pyworker.sh` explicitly stands down when it
sees `start_server.sh` referenced in `/root/onstart.sh`, so on the legacy path the
supervisord program is `EXITED` **by design** — and the pre-existing test opened
with `assert_service_running pyworker`, which would have failed on the exact path
every shipped serverless template takes. Because that test had never executed
anywhere, nothing said so. The assertions are therefore written to hold on either
path: the supervisord state is REPORTED, and what is asserted is that something
serves `:3000` and reached a score.

**The serverless cell needs a read-only `HF_TOKEN` secret.** `start_server.sh`
aborts with `HF_TOKEN must be set when BACKEND is set!` before it ever reaches the
worker, and a cell that dies in the bootstrap decides nothing. It is passed to
`qa-gate.yml` as a *secret* rather than through `extra_env`, so it is registered
for log masking and never appears in an input that is plain text in the caller's
YAML.

**The serverless cell lands advisory — but ORDERED.** Ordering and blocking are
separate properties and the first attempt at this conflated them. Leaving the cell
out of `merge-manifests`' `needs` made it non-blocking, and also made it unordered:
the job became eligible the moment the standard cells finished, so the production
approval prompt could appear while the serverless cell was still running, and a
human would be approving before its evidence existed — the one thing the gate is
for. It is therefore IN `needs` (ordering) and absent from the `if` (not blocking).

Naming `needs.build.result` and `needs.qa.result` explicitly in that `if` is
load-bearing: `!cancelled()` is what stops a failed serverless cell from skipping
the promote, and it simultaneously switches off the implicit "skip if any needed
job failed", so the two results that must still block have to be restated. Dropping
either would promote a broken image silently.

This is decision 7's ramp applied to the cell itself, which matters most here
because the mode has never executed anywhere and its first runs are as likely to
find harness gaps as image defects. Promoting it to gating means adding
`needs.qa-serverless.result == 'success'` to that `if` after two consecutive green
runs.

### 4. The worker assertion is the score, not the process

`vllm.d/20-serverless-pyworker.sh` today asserts `pyworker` is RUNNING and
something listens on `:3000`. A worker that binds the port and 500s every request
passes, as does one routing vLLM traffic to the wrong handler.

The real signal is in the SDK. `vastai/serverless/server/lib/backend.py` ends its
benchmark with:

    log.debug(f"Benchmark complete: average perf is {avg}, measured perf is {max}")
    with open(BENCHMARK_INDICATOR_FILE, "w") as f:
        f.write(str(max_throughput))

So the assertion is: `.has_benchmark` was **written by this run**, parses as a
float, and is **> 0** (`max_throughput` starts at 0 and only rises through
`max(...)`, so zero means every run produced nothing). No threshold on the value —
throughput on a rented box of unknown contention is the canonical flaky gate.

**Freshness is required, not incidental.** The same file is READ to skip
re-benchmarking, so a stale `.has_benchmark` on a reused `$WORKSPACE` volume makes
the worker skip the benchmark entirely and the test would certify a run that never
happened. That is the `.syncing` defect from ADR 0029's audit, in another file.

Note also that the benchmark drives `/v1/completions` — a route the current suite
never exercises, since it only tests chat.

### 5. The pyworker bootstrap floats, by design

`pyworker.sh` fetches `vast-ai/pyworker@main` at boot and always will; that is
intended, and this ADR does not propose pinning it.

The consequence is accepted and must be made legible: a serverless cell's verdict
depends on an artifact this repo does not build. **A bootstrap failure is reported
under its own distinct label**, so a human reads "upstream worker bootstrap
failed" rather than "vLLM broken", and the fetched state is recorded in the run
output. This is ADR 0029's "name the machine" applied to the other uncontrolled
variable.

### 6. Capabilities are discovered, and the declaration only constrains

Tiers are derived on the box from live probes, `/v1/models`, `VLLM_ARGS`, and the
model artifacts on disk. An optional `VLLM_EXPECT_CAPS` does not SELECT tiers:

| | discovered | not discovered |
|---|---|---|
| declared | runs, **required** | **FAIL** — claimed and absent |
| not declared | runs, **advisory** | n/a, logged with the reason |

The asymmetry is the point: **discovery can never manufacture a red.** Drift in
the discovery layer can lose advisory coverage, loudly, but can never block a
healthy image nor silently drop something a human declared.

### 7. Advisory before required — one clean run, not two

Every new assertion lands advisory and is promoted to required once it has run
clean on real hardware. This is ADR 0006 condition 2's ramp applied to assertions
instead of scans: on a gate where every red costs a redraw rental, an unproven
assertion is a budget risk.

**The original text of this decision demanded TWO consecutive green promotes. That
is amended to one, on the evidence of the first run.** The contract assertions
returned 10 ok, 0 errors and a single violation — and that violation was a defect
in the CHECKER (`len()` of a BatchEncoding counted dict keys as tokens), not in the
image. The ramp's purpose is to find exactly that before it can block a promote,
and one run against a live engine found it. A second identical run would have
demonstrated nothing further, and the cost of waiting is measured in coverage:
every promote in between is gated by the weaker assertions the strict ones replace.

The ramp is not abandoned, it is spent per assertion. Anything added later starts
advisory again. And the ramp still stands unspent for the serverless CELL, which is
a different kind of claim — a whole rented box and an upstream bootstrap, rather
than a deterministic assertion about a response.

## Binding conditions

1. **No sampling-dependent assertion is ever required.** The refusal list in
   decision 1 is binding; adding to the required set means adding a forced
   assertion, not a better prompt.
2. **Discovery cannot red.** Required status comes only from an explicit
   declaration.
3. **The worker assertion keys on a score written by this run**, never on the
   file merely existing, and never on the process being alive.
4. **A bootstrap failure is attributable.** Distinct label, upstream named.
5. **The ramp is not optional.** Advisory first, two clean promotes, then
   required — recorded per assertion.

## Consequences

**Positive.** The gate stops certifying "the process is alive" and starts
certifying the surface customers integrate against. The per-engine worker split
gets a test at the point where it actually differs. Capability coverage grows
automatically when a template enables a feature.

**Accepted negative.** +1 cell per serverless-capable image per build, and the
serverless cell is *not* thin — it re-runs the mode-agnostic suite. Cell-count
arithmetic under all-or-nothing promotion is real: more cells means more chances a
healthy image is blocked, which is why `retries` must be set on these gates.

**Accepted negative.** This certifies the engine, not the customer's model.
Big-model paths — TP>1, FP8/MoE kernels, long context, quantised loaders — remain
untested, exactly as they are today. The QA template pins a small model for
documented reasons and always did; that coverage was never held, so it is not
being given up. The residual worth having is cheaply covered by kernel-import and
attention-backend assertions at startup, which are a wheel/ABI question rather
than a numerical one.

**Known gap.** `base/28-inadvertent-exposure` skips outright under serverless
(`serverless rule TODO`), so the mode with **no Caddy auth gate** currently
asserts less about exposure than the standard one. The serverless cell is what
makes that testable. Closing it needs an ADR 0006 amendment defining the
serverless exposure rule — the listener set should be `:3000` plus the platform's
own, and nothing else — and that is a separate decision, not folded in here.

**Known gap.** The four `20-serverless-pyworker.sh` files are byte-identical
copies across the engine suites. L067 guards `base/` only; nothing detects
divergence between them, or all four rotting together.

## What would reverse this

- A required assertion reding a healthy image twice on upstream change rather
  than defect. The answer is to cut that assertion to status-code-only, not to
  widen the redraw rule.
- Serverless cells holding batches. The answer is to demote the worker assertion
  to advisory, not to loosen ADR 0029.
- Evidence that discovery has silently lost coverage without the evidence table
  making it visible. That would mean decision 6's asymmetry is not enough and the
  declaration must become mandatory.
