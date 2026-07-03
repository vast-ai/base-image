---
name: qa-fix
description: Diagnose and fix a Vast image that FAILED the live-GPU QA smoke, against the held instance. Use after `imagegen qa <image>` reports BLOCKED (it holds the box and writes a diagnosis bundle). The skill reads the bundle, SSHes into the live workbench, finds the root cause, verifies a candidate fix ON the box, and proposes a source diff for the human to approve before rebuild. Human-gated (ADR 0009).
---

# Fix an image that failed live-GPU QA

Closes the loop the static gates can't: defects that only surface at model-load on a real
GPU (wrong upstream pin, a missing runtime dep, a non-executable script). `imagegen qa`
has already run the smoke, **held the rented box**, and written a diagnosis bundle. Your
job: diagnose against the live box, verify a fix on it, and hand the human a concrete,
verified source change. Read [docs/adr/0009-self-healing-qa-fix-loop.md](../../../docs/adr/0009-self-healing-qa-fix-loop.md) first.

**Contract (non-negotiable, from ADR 0009):**
- **Live-green is a hypothesis; green from a clean rebuild of committed source is the only
  proof.** Never tell the human "fixed" on the strength of a live patch — the rebuild + a
  re-run of `imagegen qa` is what certifies it.
- **Closed fix surface.** Only the image's own files change: its `Dockerfile`, its
  `ROOT/opt/supervisor-scripts/*.sh`, or its `templates/*/template.yml`. Anything else —
  the generator, shared conventions, upstream source — is an **escape hatch**: stop and
  surface it.
- **The human owns the merge.** You propose and verify; you do not apply the source diff or
  rebuild until the human approves. Never auto-promote.
- **Teardown discipline.** The box bills while held. When done — approved, rejected, or
  stuck — tear it down (`imagegen qa-teardown <image>`); the reaper is only the backstop.

## Step 1 — Read the bundle
`imagegen qa` wrote `<image-dir>/.qa/bundle.json`. It has: the `verdict` (state, exit_code,
`got_result_event`, failed `tests`), the `ssh` coords `{host, port}`, the streamed
`log_paths`, the `staging_ref` under test, and the `fix_surface` (the exact
Dockerfile / supervisor-scripts / template paths you may change). Start there — do not
re-derive it.

## Step 2 — Get onto the live workbench and read the failure

**First confirm SSH works** — `imagegen qa` prints `ssh: reachable ✓` or `NOT reachable ✗`.
Vast injects the SSH keys registered on the *account that owns the box* (the QA account,
525202), NOT a personal account — so if it's NOT reachable, **stop and tell the operator to
add their pubkey to the QA account**. Do not diagnose blind; the whole method is
verify-on-the-box.
```
ssh -o StrictHostKeyChecking=no -p <ssh.port> root@<ssh.host>
```
Read the actual traceback: the streamed `log_paths` (e.g. `/var/log/portal/<name>.log`) and
`supervisorctl status`/`tail`. Do not theorise from the exit code alone — exit `1`/`5`
cannot distinguish a script-mode bug from a dep bug from an upstream break; the log can.

## Step 3 — Diagnose the root cause (against ground truth, not a summary)
- Read the exception and follow it (e.g. `'NoneType' object is not callable` → which name is
  `None` → the failed import behind it).
- **Verify any upstream pin against the upstream project's OWN install** — its `Dockerfile`
  / requirements, not a research summary. (A pin to the wrong branch is a classic miss.)
- Classify the failure — it decides the action:
  - **image-dependency / image-script** → a Dockerfile pin, an added dep, a `chmod` → **rebuild**.
  - **template-config** (wrong port/env, the app itself is fine) → edit `template.yml`, **no rebuild**.
  - **upstream-broken** (no in-image change can fix it) → **STOP and report**, with evidence.
  - **infra flake** (verdict `no_offers`/`bad_instance`, or a transient) → not a code bug; re-run `imagegen qa`, don't "fix".

## Step 4 — Verify the fix ON the live box (the load-bearing step)
Apply the candidate in place and prove it resolves the failure *before* proposing anything:
`pip install '<dep>'` / `chmod +x <script>` / edit the config, then restart the affected
supervisor program (`supervisorctl restart <name>`) or re-run the failing import, and
**confirm the app actually serves** — hit its endpoint / re-run the functional check. A fix
you haven't watched go red→green on the box is a guess, not a fix.

## Step 5 — Map it to a durable source change (bake-equivalence)
Translate the live command into the permanent edit within the closed fix surface:
- a live `pip install X<81` → a pinned line in the **Dockerfile** install RUN (not a runtime install);
- a live `chmod +x` → the script committed executable (mode `100755`), not a runtime chmod;
- a live config edit that the app reads at launch → pin it in the **supervisor script** or the config it bakes.
If the live fix has **no clean source-equivalent**, it does not qualify — say so and escalate.

## Step 6 — Present the fix proposal (human-gated — stop here)
Hand the human ONE structured proposal, not a transcript:
- **Symptom** — the failing test/verdict + the exact traceback lines.
- **Root cause** — one sentence.
- **Evidence** — what you checked on the box and against the upstream's own install.
- **Verified on the live box** — the exact command run + the confirmation the app then served.
- **Proposed diff** — the concrete change to the Dockerfile / script / template (the durable
  bake-in of the verified command).
The human approves the **diff**; the on-box verification is the proof behind it.

## Step 7 — On approval: apply, rebuild once, re-verify, tear down
Apply the diff to source, then **rebuild once** and re-run `imagegen qa <image>` — a green
verdict from the freshly-built image is the certification (Step-4 live-green was only the
hypothesis). If it comes back green: report done, and the box from this run is already torn
down by the PASS path. If it comes back red with a **new** failure, that's progress —
diagnose the next one. If it comes back with the **same** failure, the bake didn't
reproduce the live fix (a Dockerfile ordering/caching issue) — surface that explicitly,
don't just retry. Then `imagegen qa-teardown <image>` if any box is still held.

**Escape hatch:** a correct fix that needs to touch anything outside the image's own three
file types — the generator, a shared convention, an invariant, CI, or upstream — is not a
qa-fix change. Stop and surface it to the human (it may be a Bug→Invariant for the linter,
or an upstream issue), rather than reaching outside the closed surface.
