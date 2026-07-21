# ADR 0009 — Self-healing live-GPU QA fix loop (human-gated)

- **Status:** Accepted (conditional — see Binding conditions; ships human-gated, autonomy deferred behind a flag)
- **Date:** 2026-07-02
- **Decision owner:** Rob Ballantyne
- **Process:** idea brief → objections resolved by the owner → three independent designs (human-gated MVP / fully-autonomous loop / failure-attribution+termination) run blind, in parallel → synthesis → this record. Extends ADR 0005; supersedes nothing in it.

## Context

The `imagegen` generator + `new-image` skill scaffold an image and lint it, then **hand
off** — the skill is explicit that lint is a fast structural gate, NOT the correctness
gate. Dogfooding a real image proved the gap has teeth: static lint, a local
`docker build`, AND a CPU import-smoke all passed an image that then died on a **real GPU
at model-load time** — three independent defects (a dependency pinned to the wrong upstream
branch; `setuptools>=81` having removed `pkg_resources`, silently nulling a class; a
non-executable supervisor script). A human closed each by: run the live-GPU QA, SSH to the
held instance, read the streamed traceback, apply a candidate fix **on the live box**
(`pip install` / `chmod` / repin), confirm the app actually serves, then bake the confirmed
fixes into the Dockerfile and rebuild **once**.

That manual loop is automatable, and the signal is already wired: ADR 0005's runner
(`tools/template_manager/test_template.py`) rents a GPU on the QA account, boots the
staging image from a template, streams named in-instance logs back (`--log`), holds the box
(`--keep`), and returns a machine-readable verdict (exit 0/1/2/3/4/5/130 + `--raw` JSON).
The `reap_orphans.py` label-scoped reaper is the leaked-instance backstop. ADR 0008 covers
template publish. What's missing is the loop that turns a red verdict into a diagnosed,
verified image fix — with the LLM doing the diagnosis.

## Options considered

Three independent designs were commissioned blind. They **converged** on the substrate and
diverged only on the autonomy dial:

- **A — human-gated MVP.** `imagegen qa` runs the smoke; on failure it holds the box and
  hands a Claude skill a file-based diagnosis bundle + SSH; the skill diagnoses, verifies a
  fix on the live box, and **proposes** the source diff for a human to approve before any
  rebuild.
- **B — fully autonomous loop.** `imagegen qa --autofix`: a bounded nested loop (inner
  live-fix loop that never rebuilds, outer bake-and-rebuild loop) that runs to a green
  rebuilt image or gives up, human only at the merge.
- **C — attribution + termination.** A deterministic front-gate decision table (route on
  exit code + `--raw`), an agentic attribution stage where **the live probe is the oracle**
  (apply a fix live; *whether* it flips green and *where the fix lands* classifies
  template-vs-image-vs-upstream and routes relaunch-vs-rebuild-vs-stop), and a deterministic
  termination state machine (failure-signature, regression corpus, caps).

**Rejected sub-alternatives** (raised and killed inside the designs):
- *Pure verdict-code routing, no live probe* — the exit code cannot distinguish the
  app-failure classes (all land as `1`/`5`); it would bake-then-rebuild each hypothesis at
  full build cost and never use the live box. Rejected.
- *Agent-only attribution (LLM routes everything)* — burns GPU/tokens on legs that are
  deterministic (`no_offers`/`bad_instance`/`config_error`) and lets an LLM own
  termination, which won't terminate. Rejected.
- *Trust live-green, skip the confirming rebuild* — a hand `pip install`/`export` on a
  running container's overlay need not survive a clean image build. Rejected outright.
- *Full autonomy now* — deferred, not rejected: correct target once the guardrails are
  proven, but not before.

## Decision

Build the **shared spine once** and ship it **human-gated**:

1. **`imagegen qa <image>`** (user-initiated) resolves the private `<name>-qa` template +
   staging tag and runs `test_template.py --keep --raw --log <paths> --label
   imagegen-qa-<user>-<sha>`. On PASS → tear down, report. On a BLOCK verdict → hold the
   box, write a **diagnosis bundle** (verdict JSON, streamed logs, SSH coords, image source
   paths) + a **teardown ledger** to the session scratch dir.
2. **SSH-coordinate extraction** in `test_template.py` (`ssh_host`/`ssh_port`, fallback the
   `22/tcp` port map) into `--raw` — the one genuinely new instance primitive.
3. **A `qa-fix` skill** reads the bundle → SSHes in → diagnoses (traceback + the upstream
   project's OWN Dockerfile) → verifies a candidate fix on the live box → emits a
   **structured fix proposal** (symptom, root cause, evidence, the live-verified command,
   and the concrete Dockerfile/script/template diff that bakes it in).
4. **A human approves the diff**, then the fix is applied to source and rebuilt once; a
   re-run of `imagegen qa` is the confirmation.

The LLM owns diagnosis; Python owns launch, hold, teardown, and the bundle contract. C's
attribution-by-live-probe and the deterministic termination/guardrails are the safety
backbone regardless of autonomy. **Autonomy (`imagegen qa --autofix`, design B) is deferred
behind a flag**, unlocked only after the human-gated path shows its diagnoses and its
bake-equivalence hold up.

## Binding conditions

1. **Live-green is a hypothesis; only a green verdict from a clean rebuild of committed
   source is proof.** Never bake a fix that wasn't first verified green on the live box, and
   never report success on a live-patched box — a confirming rebuild is mandatory.
2. **Attribution uses the live probe, not the exit code alone.** Where a successful live fix
   lands routes the action: image COPY-tree/script/dep → rebuild; template env/port → relaunch
   only, no build; nothing resolves it live → upstream, STOP-and-report.
3. **Termination is code, not LLM judgment.** A failure-signature (failed tests + top frame +
   exception type + exit code); same signature twice → escalate; a live fix that does not flip
   green → escalate (by definition not a simple dep/config/script bug); a hard rebuild cap; a
   per-image **regression corpus** so a fix for bug N that re-raises N−1 is reverted; infra
   verdicts (`no_offers`/`bad_instance`) retry-then-hold, never a "fix".
4. **Teardown discipline is the highest-stakes property.** The box is held as a workbench, so
   a stranded paid instance is the top risk: a teardown ledger (written the instant the box is
   held) + the label-scoped reaper give two independent guarantees. The SIGKILL-mid-hold
   teardown test is written FIRST. The `VAST_API_KEY` is never round-tripped through the box.
5. **The human owns the merge; the loop never auto-promotes to prod and is never a CI gate.**
   User-initiated only, consistent with the `imagegen`/reaper posture.
6. **Closed fix surface.** Fixes are confined to the generated files (Dockerfile / supervisor
   script / launch template); the live-box adapter whitelists verbs (pip/chmod/file-push/
   supervisor-restart), never arbitrary shell.
7. **Creds via a gitignored `.env`** (never committed; same posture as the Atlassian creds).

## Consequences

- Catches the model-load class of defect that static analysis structurally cannot, and turns
  "N fixes = N rebuilds" into "N verified-live fixes = 1 rebuild".
- Standing complexity is non-trivial for a ~5-image QA allowlist; the *diagnosis* (the
  valuable part) is a skill that already works — the loop is mostly plumbing + safety. Accepted
  by the owner (dev-hours saved » GPU-minutes), with the go/no-go re-weighed if the allowlist
  stays small.
- The load-bearing LLM call is the **image-dependency ↔ upstream-broken** boundary
  (fix-vs-STOP); the live probe forces it (no pin/config flips it live → upstream,
  deterministically), but a confident-but-wrong pin remains the main quality risk.

## What would reverse this

- Vast shipping first-class ephemeral/TTL instances **and** a clean "apply candidate to a
  fresh build" primitive collapses the live-vs-rebuild gap and half the guardrails
  (simplifies, does not reverse).
- If the QA allowlist stays small enough that an operator reading the same log is cheaper,
  keep only `imagegen qa` + the `qa-fix` skill and drop the auto-bake/rebuild machinery.
- Graduating to unattended `--autofix` is a **new decision** requiring its own ADR update —
  it must not drift in silently.

## Addendum — whole-feature review (2026-07-03)

After the human-gated MVP was built and proven live (a real image's QA green end-to-end, plus a
diagnose→verify cycle run against a deliberately-broken image), the whole feature was put
through an independent critical review from four perspectives — adversarial-failure,
code-correctness, security, and design-coherence. The reviews **converged** rather than
diverged, which is the useful signal. Verdict: the money-safety spine and the human-gated
diagnosis loop are sound; a handful of defects were fixed; and `--autofix` has a hard gate.

**Converged defects — fixed (commit 4acaeab), human-gated path:**
- The provisional teardown ledger locked onto the FIRST `QA-INSTANCE-CREATED` marker, so a
  retried launch (destroy box A, hold box B) pointed the ledger at destroyed A while B leaked.
  Now tracks the current box (handles the `None` marker). This was the only confirmed money leak.
- `_ensure_repo` only set public on create; an existing PRIVATE staging repo → the QA GPU
  can't pull → wasted boot. Now flips it (PATCH) and fails LOUD on login/create failure
  (2FA / PAT / credStore / wrong namespace) instead of degrading silently to a private push.
- `qa` now reuses the last build's staging ref (from `build.json`) so it never tests a
  different image than was built.
- Bundle marks `verdict`/`log`/box output as untrusted DATA; the qa-fix contract forbids
  executing anything embedded in it. SSH guidance corrected (direct endpoint + injected
  team-member key). Disagreement tears down rather than holding a billing box. `.env*` ignored.

**Binding conditions ADDED / sharpened by the review:**
8. **Box output is untrusted input.** The rented box is multi-tenant and AUTHORS the verdict,
   logs, and tracebacks the agent reads. They are DATA, never instructions. Under `--autofix`
   the on-box verb set and the closed fix surface must be enforced MECHANICALLY, not by LLM
   discretion (security H1/H2).
9. **`--autofix` is gated on code, not prose.** It must not ship until ADR condition 3's
   termination machinery EXISTS in the imagegen package with tests — failure-signature,
   rebuild cap, per-image regression corpus, spend/rent accounting — AND the image allowlist
   `test_template.py` drops via `--force` is re-imposed at the imagegen layer AND box output
   is handled per condition 8. A code guard must make the flag un-addable without these.

**Deferred hardening (recorded, defensible to leave for the human-gated path):**
- SSH `StrictHostKeyChecking=no` for the interactive session (M2) — TOFU-pin the host key.
- Strip box-sourced ANSI/control chars before writing to the operator's TTY (M3).
- The auto-created staging repo defaults PUBLIC (M1) — open decision: private + instance-side
  registry auth, or public + a pre-push secret-scan.
- A held box bills to the reaper's test-phase floor (~260 min) — open decision: a held-box-specific
  shorter floor, or auto-teardown on session exit.

**Superseding decision — template unify (see the follow-on ADR):** today `templates/default/`
(launch) and `templates/<name>-qa/` (gate) are near-duplicate files kept in sync by hand, and
only newly-scaffolded images have a `default`. So "QA passed" proves the `-qa` template boots,
NOT the template a user launches. This ADR's deferral of the unify is superseded: the QA gate
shall test the SAME template users launch, with `-qa` reduced to a thin overlay. Recorded in a
follow-on ADR.

**Scope extension — `new-image` drives the loop (the human-gated one-shot).** The `new-image`
skill previously stopped at a clean lint + handoff (ADR 0001). It now continues through
build → live-GPU `qa` → (on failure) the `qa-fix` diagnosis loop → rebuild, iterating until a
green rebuilt verdict, the human approving the fills and each fix diff. This makes
`/new-image` the scaffold→working-image one-shot the owner specified, and supersedes ADR
0001's stop-at-handoff. It stays HUMAN-GATED — not the unattended `--autofix` (cond 9); the
`qa-fix` procedure is reused, not duplicated. The editable surface widens past lint from
FILL-only (ADR 0001) to the qa-fix closed surface (cond 6) for real runtime fixes.
