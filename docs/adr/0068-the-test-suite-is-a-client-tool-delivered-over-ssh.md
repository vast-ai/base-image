# ADR 0068 — The test suite is a client tool: test types are client units, and on-box checks are delivered over SSH

- **Status:** Proposed
- **Date:** 2026-11-25
- **Decision owner:** Rob Ballantyne
- **Design review:** one round on 2026-11-25: a critical review of the idea, three
  competing designs, three independent assessments (feasibility, value, risk), and a
  final critical review of the plan. The final review asked for changes; they are
  addressed in this text and were not re-reviewed.
- **Amended 2026-11-26 (owner decision, not reviewed):** a template declares its
  model's features in `<ENGINE>_EXPECT_CAPS`; the caller's argument adds to that
  (decisions 2 and 4). The reviewed text had the caller as the only source.
- **Amends:** [ADR 0059](0059-template-testing-lives-in-base-image-in-two-layers.md)
  D1 (where on-box checks are stored), D2 (the vocabulary a caller may declare) and D3
  (what a consumer pins).
- **Supersedes:** [ADR 0032](0032-qa-tests-may-be-client-delivered.md) (Proposed).

## Context

- Every image carries a test suite under `ROOT/opt/instance-tools/tests/`: a runner
  with an embedded HTTP results server, a helper library, 29 base checks and a suite
  per engine or application. A boot stage starts the runner only when
  `INSTANCE_TEST=true`, so no customer instance runs it. It is a test harness that
  ships in customer images.
- A change to a baked check reaches only images built afterwards. Derivatives build
  from a dated base tag and carry that base's copy. A faulty baked check once blocked a
  whole class of QA runs until it was withdrawn, and the interim workaround was for
  derivatives to delete their base's copy.
- The launcher maps an extra public port on every test instance to read results.
- ADR 0059 moves request/response checks to the client and selects them from the
  routes a template declares in `OPENAI_ROUTES`. A property of the model behind the
  chat route (it accepts images, several images, video; it returns reasoning; it calls
  tools) is not a route and cannot be declared. Check selection is a fixed chain in one
  large module, and a new kind of check touches five tables.
- ADR 0032 proposed that some tests be delivered by the client, on the premise that
  baked tests are what "the image asserts about itself, on every customer instance, at
  every boot". That premise does not describe what the boot stage does.
- The launcher already opens an SSH session to the instance with a key that exists for
  one run, for one of the client-side contract checks.

## Options considered

### 1. Keep the suite baked; add only the missing type declaration

- Pro: the smallest change. It delivers everything about test types.
- **Not chosen as the end state.** Test code keeps shipping in customer images, every
  check exists in as many copies as there are image generations, and a faulty check
  still needs a rebuild to leave an image. Its work is kept as the first steps of this
  decision.

### 2. Keep the suite baked; let the client delete or overlay a baked file before the run

The client reads results over SSH, and may remove or replace a named baked file before
it releases the baked runner.

- Pro: fixes and withdrawals reach an image without a rebuild; checks keep the
  environment they inherit today; no compatibility rule is needed.
- **Rejected by owner decision.** Test code stays in customer images and two copies of
  every check keep existing, one baked and one in the repository, with the client
  patching between them.

### 3. A small runner stays baked; all test content is delivered

- Pro: a versioned on-box interface; the boot environment can be captured exactly.
- **Rejected because:**
  - The runner itself keeps the rebuild lag: a runner fault still needs a rebuild.
  - A derivative can switch only after its base pin carries the runner.
  - The exact environment it captures is already available on every existing image
    (decision 6), so the baked part buys nothing.

### 4. Nothing test-specific baked; a driver and the checks are delivered over SSH (chosen)

### Rejected within the chosen option

- **Running the checks in the foreground of the SSH session.** The run would die with
  the connection, leaving a box that a test had reconfigured part-way.
- **Running the checks from the commit that built the image.** A fix would again need
  a rebuild.
- **A hand-kept version number per layer, with per-check version headers and retained
  older variants.** It depends on a manual bump on most check changes, and a forgotten
  bump can be neither detected nor attributed.
- **A new template variable for model features.** A template already has a variable
  that says what its model must be able to do, `<ENGINE>_EXPECT_CAPS`; a second one
  beside it would be a third stored declaration.
- **Selecting types by probing the model.** A broken image path would read as "this is
  a text-only model" and pass silently, and probing undeclared inputs has taken a
  server down.

## Decision

**The test suite is a client tool. A test type is a self-contained unit in the client,
chosen by declaration. On-box checks are no longer baked: the launcher delivers them
over the instance's existing SSH port, runs them detached on the box, and reads the
results back over the same connection.**

ADR 0059's split by where a check looks from stays: the box checks what only the box
can see, and the client checks every request and response through the public endpoint.
What changes is where the on-box code is stored and how it reaches the box.

### Test types

1. **A type is one unit.** One module per type under
   `tools/template_manager/checks/`, declaring its name, the routes it needs, the image
   families that can produce it, its result names (each structural or answer), its
   fixtures and its code. An explicit registry lists them, and a test fails when a
   module is not registered. The tables that list capabilities and result names are
   derived from the registry.
2. **Two things are declared, in two places.**
   - What a template **serves** comes from `OPENAI_ROUTES`, exactly as ADR 0059 D2
     decides. That is unchanged.
   - What the chat model **accepts or returns** (`vision`, `multi-image`,
     `video-input`, `reasoning`, `tools`) comes from the template's
     `<ENGINE>_EXPECT_CAPS`, the variable the contract already reads for the
     capabilities it probes. A template for a vision model declares `vision` there and
     is checked for it on every run, with nothing for a caller to remember. A caller
     may add to it for one run: a `--features` argument, and a `features` input per
     cell in the QA gate. This amends ADR 0059 D2's statement that names are the
     routes'. No new variable is introduced.
   - A capability declared there and absent is a violation, as it already is for the
     contract's own capabilities.
3. **Declaration problems are reported, never absorbed.** An unknown type is a
   configuration error. A type whose required route is not declared is an error. A
   declared type with no unit yet is skipped, never passed. Every run's first result
   says which routes and types were selected and where each came from.
4. **`tools` is the contract's, and every contract checker knows the feature names.**
   - The contract already probes `tools` and already reads `<ENGINE>_EXPECT_CAPS`, so
     it is not checked a second time. Named by a caller, it is handed to the contract
     as declared, which makes the contract's result required.
   - `<ENGINE>_EXPECT_CAPS` is read by the client's contract and by each engine's
     on-box contract check, and each reports an unknown name as an error. All of them
     accept the feature names, and a test holds the lists equal.
   - A copy of the on-box check baked into an image built before this does not accept
     them. A template starts declaring a feature only once its gate runs the delivered
     checks (decision 11).
5. **Vision is the first new type.** Its structural check is deterministic: the request
   is accepted, and the prompt costs more tokens with the image than without. Its
   answer check reads a known token from a committed fixture. Further types are built
   when a template needs them. Answer checks never gate image promotion (ADR 0031).

### On-box checks

6. **The environment is the supervisor's.** The driver reads the environment of the
   running supervisord once, from `/proc`, and gives exactly that to every check. Boot
   stages export only before supervisord starts and the baked runner is started after
   it from the same shell, so this is the environment the baked runner inherits today.
   The driver waits for supervisord (ADR 0030); if it never starts, the run is an
   error. The SSH login environment is not used: it is built by a different parser and
   misses values the boot exports. The driver sets the boot shell's umask explicitly.
7. **The run is detached and durable.** The driver starts in its own session, takes a
   lock, and writes its output, a status file and a completion marker to a run
   directory on the box. The client follows the output by byte offset and resumes at
   that offset after a dropped connection. A second concurrent run is refused.
8. **Delivery is verified, and a delivery failure is a failure.** The bundle (driver,
   helper library, the image's suites, a manifest of hashes) is sent over the session,
   extracted, checked against the manifest, and its digest echoed back. A missing tool,
   a mismatch or a driver that does not start is an error, never a skip.
9. **Suites are chosen by image**, through the same inheritance resolution the linter
   uses (base, then a nested base, then the image's own suite), shared as one function.
10. **Each layer carries a computed stamp, and a mismatch is attributed, not hidden.**
    Every Dockerfile that copies a suite-bearing `ROOT/` writes a content hash of that
    tree to `/etc/vast-layer.d/<layer>`. The client computes the same hash from its
    checkout.
    - Equal: a failing check is a fault in the image.
    - Different or absent: the run proceeds and records the difference. A check from
      that layer's suite that fails is reported as a layer mismatch. It blocks, it is
      not redrawn as a host fault, and it says the remedy: rebuild the image or move
      its pin.
    - Nothing is hand-kept, so nothing can be forgotten.
11. **Checks always come from the client's commit.** A fix or a withdrawal reaches
    every image on the next run. A check of new behaviour fails on a layer that lacks
    the behaviour, and decision 10 says why. Rebuilding or re-pinning an older image to
    bring it up to date is the accepted remedy.
12. **A template without SSH is refused before anything is rented**, as a configuration
    error.
13. **An SSH failure is classified by whether it reproduces.** SSH refused on one
    machine is a host fault and the next offer is tried (ADR 0029). SSH refused on
    every draw of a run blocks: the platform installs sshd at launch inside the image's
    own distribution, so an image can break it everywhere, and that must not end as an
    inconclusive result.
14. **The host key is pinned for the run**, so a reconnect cannot be redirected.
15. **A test instance boots exactly as a customer's does.** The launcher sets no test
    variable and maps no extra port, so the image never starts its baked runner. The
    client connects once the instance is up, removes any baked copy of the suite that
    an older image still carries, and starts the delivered run. Checks do not depend
    on a test variable being set.
16. **Test names do not change.** `require_tests`, `require_external`,
    `INSTANCE_TEST_REQUIRE_PASS` and the verdict keep their meaning throughout.

### Order of work

One thing at a time (ADR 0059 D7). Each step lands and is used before the next starts.

1. Measure, changing nothing: when SSH first accepts during boot; the supervisor's
   environment against the baked runner's, byte for byte; umask and limits; the tools
   the driver needs on every upstream base; SSH in the serverless cell.
2. The type registry, with no change in behaviour.
3. `--features` and the vision type, advisory.
4. Add the layer stamps and the delivered run (decisions 6 to 15), built from the
   suites where they are today. Each image is run both ways on the same tag, as
   separate runs, until its results match. The baked gate remains the gate meanwhile.
5. Switch each gate to the delivered run, one caller per change.
6. Remove the baked suite, the runner and the boot stage, and move the suites out of
   `ROOT/`.

## Binding conditions

1. **No image is ungated at any step.** Each gate switches in a change of its own, and
   the names it requires are the same before and after.
2. **No skip counts as a pass, and no delivery failure counts as a skip.**
3. **The removal step re-points every lint rule that reads the suite location**, moves
   each rule's mutation test to the new location in the same change, and makes the
   shared helpers fail when the suite root is missing or empty. A rule must never pass
   by scanning nothing. A new rule asserts both that nothing remains under the old
   location and that the new one is populated.
4. **New rules, each with a mutation test:** a gate's `features` names only registered
   types whose route the cell serves; every gating QA template offers SSH; every
   Dockerfile that copies a suite-bearing `ROOT/` writes its stamp; no boot stage at or
   after the supervisor's exports a variable.
5. **Proofs carry no host data** (ADR 0059 D6, D10). They record the bundle digest, the
   client commit, each layer's match or mismatch, the name of the environment source
   and the selected types. They never record SSH coordinates or environment values.
6. **The removal step waits** until every gate uses the delivered run, every consumer
   of the launcher has stopped depending on the results port, and the fix loop reruns
   checks through the launcher.
7. **A bundle built from uncommitted changes is recorded as such and cannot gate.**

## Consequences

- Customer images stop carrying test code, the results server and the test hook, and
  test instances stop exposing an extra port.
- One copy of each check exists. A fix or a withdrawal takes effect on the next run.
- The fix loop can prove a corrected check on a held instance without a rebuild.
- SSH becomes the only transport. A template that does not offer SSH cannot be tested.
- Most changes to a check arrive with a change to the image's behaviour. Those still
  need the image rebuilt, or a derivative's pin moved, before the check can pass
  there. This decision does not remove that; it makes the cause visible.
- A derivative whose pinned base is behind fails new base checks until its pin moves.
- The delivered helper library must keep working on the oldest base layout that is
  still gated. The shadow step is the proof of that, not a lint rule.
- An exposure allowlist is delivered from the client's commit, so loosening one
  applies to every tag tested afterwards.
- A model-library approval tests the features the template declares and any the
  caller adds. A template that does not declare that its model accepts images is not
  tested with images, and the run's first result says what was selected.
- ADR 0059 D3 changes form: a consumer pins a checkout of this repository, because the
  bundle is repository content.

## What would reverse this

- SSH proves unreliable as a gate dependency while images are run both ways (step 4).
- The platform makes SSH optional for the templates in scope.
- Customers start to rely on running the suite themselves on their own instances.
