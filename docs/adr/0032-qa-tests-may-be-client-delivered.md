# ADR 0032 — QA tests may be client-delivered, and the image keeps what it must prove itself

- **Status:** Proposed
- **Date:** 2026-08-21
- **Decision owner:** Rob Ballantyne

## Context

A shipped instance test has exactly one delivery mechanism today: it is baked into
an image's `ROOT/opt/instance-tools/tests/`. That single mechanism carries two
different jobs at once, and they have different requirements.

**Job one — the image proves things about itself.** The suite runs at boot on
every customer instance, forever. `base/60-gpu-cuda` asserting the CUDA userland
loaded is a property of the image, and a customer should get that answer whether
or not anyone is watching.

**Job two — the gate proves things about a candidate image.** A promotion cell
asserts far more than a customer needs, on a rented GPU, once. `vllm.d/12-vllm-contract`
exists to decide whether a build is fit to promote.

Baking both means every change to either pays the same price, and that price was
measured rather than estimated. Derivative base pins sat at `2026-06-15` until
`2026-08-21` — **67 days** — during which base's `ROOT/` changed in 10 commits,
three of them to `lib.sh`, the library every test sources. None of it reached
`llama-cpp` or `oobabooga`. The full chain for a test change to reach a derivative
is: merge, base build, promote with QA and manual approval, pin bump in two files,
derivative rebuild. Three of those five steps are manual.

The delivery asymmetry makes it worse in a way that is easy to miss. External
images build with `base_image_source=.` — the repo root as a build context — so
`COPY --from=base_image_source /ROOT /` gives them the working tree's tests at
build time. Derivatives take base's `ROOT/` from the pinned image layer. So the
same commit reaches five images immediately and two on a multi-week delay, and
nothing announces the difference: the gate reports coverage that is true for some
images and false for others.

There is a third pressure. Templates that ship a real model want assertions about
*that deployment* — a vision model's tier, a reranker's shape — which do not
generalise to the image and should not be baked into it.

**The mechanism to do better already exists and is unused.** `runner.sh` calls
`wait_for_client()`, which blocks for up to 7200s until a client connects, with
the stated purpose of preventing "tests from running and completing before anyone
is watching". `discover_tests()` globs `find "${TESTS_DIR}" -maxdepth 1 -name '*.d'`,
so any `.d` directory present when the runner starts is picked up. Between those
two facts there is already a window in which a connected client can place tests
that the existing machinery will run, stream, phase-gate and require exactly like
baked ones.

A framing that was **considered and rejected**: treating that window as an
injection hazard. It is not a security boundary. The instance runs as root and the
template owns `onstart`, so a template can already disable the suite outright.
Defending the test directory against the party that controls the container is
effort spent on a boundary that does not exist. What the window is worth is a
delivery channel.

## Options considered

### A. Bake everything (status quo) — rejected

Every test ships in an image. Simple, one mechanism, and the guarantee is uniform.

Rejected on measured cost. A test change cannot reach an already-promoted image at
all, which means an assertion cannot be added to an image in production without
rebuilding and re-promoting it. The 67-day derivative lag above is the same defect
seen from the other side. It also forces template-specific assertions into images
that do not need them.

### B. Shared test library in base, thin per-image wrappers — rejected here

One implementation of the shared assertions in base's `ROOT/`, invoked by a small
stub in each engine suite.

Rejected as an answer to *this* problem because it inherits exactly the lag it is
meant to solve: base is where derivatives get their tests late. It remains a live
option for reducing duplication between engine suites, which is a separate
question decided separately.

### C. Templates supply their own test code — rejected

The Vast-side template record carries tests, or a URL to them, and the instance
runs whatever it is given.

Rejected, and **not** on security grounds — the template already controls the
instance, so this grants no capability it lacks. It is rejected because a QA
verdict must be reproducible from this repo. Template records are edited outside
version control and outside review, so a cell's verdict would depend on an
artifact with no commit, no diff and no history. ADR 0031 decision 5 already
accepts one such dependency, the floating pyworker bootstrap, and only by
requiring it be separately attributable in the run output. Making every assertion
that shape inverts the trust direction: the artifact under test would supply the
standard it is judged by.

### D. Client-delivered, repo-resident tests (chosen)

The QA client places tests into the running instance before signalling ready. The
tests live in this repo beside the template they belong to, so they are reviewed,
versioned and diffable, and they reach any image — including one already in
production — without a build.

## Decision

**A QA test may be delivered by the client instead of baked into the image, and the
two classes mean different things.**

### 1. The line between the classes is what makes this safe

- **Baked** — the image asserts this about itself, on every customer instance,
  at every boot, with nobody watching.
- **Injected** — the gate asserts this about a candidate image, at promotion time
  only.

The second is not a substitute for the first. Anything a customer instance should
verify about itself stays baked.

### 2. Delivery reuses the existing machinery, and adds no result plumbing

The client places executable tests in `/opt/instance-tools/tests/client.d/` and
*then* connects to the results server. `discover_tests()` finds them, and
streaming, ADR 0030's phase gate, `INSTANCE_TEST_REQUIRE_PASS` and `qa_verdict`
all apply unchanged. Reusing the path rather than adding a parallel one is
deliberate: a second result channel would be a second place for a test to go
missing quietly.

### 3. Tests live beside the template they serve

`templates/<name>-qa/tests/`. A template that pins a particular model can carry
assertions about that deployment without pushing them into an image where they do
not generalise.

### 4. Injected results are visually distinct from baked ones

The `client.d/` prefix stays in the reported test name. "42 passed" that silently
mixes the two tells a human nothing about which assertions the image actually
carries, which is the question being asked at a promotion gate.

### 5. A verdict records what was injected

The bundle's content hash and the repo ref it came from are recorded in the run
output. This is ADR 0031 decision 5's attribution rule applied to the one new
uncontrolled variable this introduces.

### 6. Delivery failure is a failure, never a silent skip

If the push does not land, the run fails. A suite that quietly proceeds without
the tests it was supposed to gain is the skip-as-pass shape this project has spent
its QA work closing — and it is worse here than usual, because the missing
assertions leave no trace in the results at all.

### 7. The linter follows the required set wherever it lives

`L059` (a required test must be able to fail) and `L072` (a gating template must
require its own suite) reason about baked paths today. If
`INSTANCE_TEST_REQUIRE_PASS` can name a client-supplied test, both must resolve
`templates/<name>-qa/tests/` too. L059 was silent on `external/` for months for
exactly this reason — it reported clean by failing to find the file.

## Binding conditions

1. **Injected tests may only ADD.** No assertion moves from baked to injected
   because it is awkward, slow or flaky. The pressure to do so is the predictable
   failure mode of this decision — it makes a red go away while quietly ending the
   image's ability to prove something about itself on a customer's box.
2. **No verdict without provenance.** A run that cannot say what it injected
   cannot be reproduced, and an irreproducible verdict is not evidence.
3. **Injected results stay distinguishable** in the reported names.
4. **Delivery failure fails the run.**
5. **The required-pass gate keeps its reach.** A required test the linter cannot
   resolve is a rule that has gone blind, not a rule that is satisfied.

## Consequences

**Positive.** A test change reaches any image, including images already promoted,
without a build. The 67-day derivative lag stops applying to gate assertions, and
the external/derivative delivery asymmetry stops silently splitting coverage.
Template-specific assertions get a home that is neither an image nor an unversioned
template record. The interactive workflow the QA-fix loop (ADR 0009) wanted becomes
straightforward: launch a box, iterate a test against it live, commit it when it is
right, rather than guessing and paying a rebuild per attempt.

**Accepted negative.** Two classes of test is more to hold in your head than one,
and the class of an assertion is now a judgement call at authoring time. Condition
1 is what keeps that judgement honest, and it is the condition most likely to be
eroded quietly.

**Accepted negative.** The gate's assertions and the image's assertions can now
diverge in version. A promoted image proves what it was baked with; the gate proves
what the repo held at promotion time. That is the intended flexibility and it is
also a way for the two to drift apart without anyone noticing.

**Known gap.** This says nothing about reducing duplication *between* engine
suites, which is decided separately — the current position is per-image copies with
the drift risk stated rather than a shared base library.

## What would reverse this

- Evidence that assertions are migrating from baked to injected, which would mean
  condition 1 is not holding and images are quietly ceding self-verification to a
  gate customers never run.
- An injected suite whose failure is more often the delivery than the image, which
  would make the channel a source of verdicts rather than a carrier of them.
- The pin lag being fixed at its root — derivatives receiving base's `ROOT/` fresh
  the way externals do — which would remove the strongest argument for this without
  removing the template-specific one.
