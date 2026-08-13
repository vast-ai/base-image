# ADR 0024 — Resolve the driver's CUDA version through one verified helper

**Status:** Accepted
**Date:** 2026-08-12

## Context

`/etc/vast_boot.d/05-configure-cuda.sh` picks which CUDA toolkit an instance uses.
It needs one number: the maximum CUDA version this host's driver supports. Until
now, five places in the repo obtained it by scraping `nvidia-smi`'s
human-readable table for

```
CUDA Version: 13.3
```

NVIDIA driver branch 610 renamed that field:

```
| NVIDIA-SMI 610.57.04   KMD Version: 610.57.04   CUDA UMD Version: 13.3 |
```

Every scrape returned empty on every 610 host at once — deterministic and
fleet-wide, not a flaky machine. In the boot script the empty value aborted CUDA
configuration *after* it had already deleted every CUDA entry from
`/etc/ld.so.conf.d`, so instances booted with the toolkit on disk and none of it
on the loader path.

It stayed invisible because torch ships its own CUDA libraries inside the venv:
torch, the GPU tests and CUDA compute all kept working. The first casualty was
`torchcodec` failing to find `libnppicc`, three layers from the cause, on a host
that looked like a flaky rental. Confirmed on live rented hosts running
610.57.04 and 610.43.02.

Two properties of the environment shape the fix:

* **`cuDriverGetVersion` reports whichever `libcuda.so.1` the loader resolved.**
  A previous boot that enabled forward compat wrote
  `/etc/ld.so.conf.d/0-compat-cuda.conf` (named `0-` so it wins), and `/etc`
  persists across a stop/start on overlayfs. An unguarded probe on the second
  boot therefore returns the *compat* version — which makes "is compat needed?"
  answer *no*, disables it, and brings a cross-major instance back with a newer
  toolkit on an older driver. Worse than the bug being fixed, and invisible to
  CI, which only ever boots an instance once.
* **`LD_LIBRARY_PATH` is a search hint, not a pin.** Naming a directory that
  yields no loadable `libcuda.so.1` sends the loader on to the ld.so cache — to
  the compat library, silently. Any bypass built on it fails *open*, to
  precisely the wrong answer.

## Options considered

### A. Chase the new field name

Widen the greps to match `CUDA UMD Version:` as well.

Rejected: it restores service and leaves the class of defect untouched. The
contract being relied on is rendered prose in a tool whose output NVIDIA changes
without release notes; the next rename breaks the fleet the same way. It also
does nothing about the destructive ordering, which is what turned a parse miss
into an unusable loader configuration.

### B. Per-caller `LD_LIBRARY_PATH` bypass, in bash

Each caller finds a native `libcuda.so.1` itself and pins the probe:

```bash
_p=$(find /usr/lib -name 'libcuda.so.1' -not -path '*/compat/*' | head -1)
MAX=$(LD_LIBRARY_PATH="$(dirname "$_p")" cuda-driver-version)
```

Rejected, after it shipped and was caught in review: it fails open twice. An
empty `_p` produces `LD_LIBRARY_PATH=` and an unpinned probe; a `find | head -1`
on a host with 32-bit driver libraries mounted (`NVIDIA_DRIVER_CAPABILITIES=all`
includes compat32) is readdir order, and a 64-bit process skips the wrong-ABI
match and falls through to the cache. Both land on the compat library. And
because the same six lines lived in the boot script *and* in `base/60-gpu-cuda`,
the test agreed with a wrong boot instead of catching it — a mirrored heuristic
is not an independent check. Hardening one copy left the other, in the file that
gates promotion.

### C. Snapshot → cleanup → probe → restore on failure

Keep reading the version *after* the loader cleanup, where nativeness holds by
construction (the compat conf has just been deleted), and make the abort safe by
snapshotting `ld.so.conf.d` first and restoring it verbatim if the probe fails.

Rejected, though it is the only option that gets nativeness structurally rather
than by resolution. It keeps a window in which the loader configuration is
destroyed, and moves correctness onto a restore path that runs precisely when
something has already gone wrong — untestable in the case that matters, and a
partial restore leaves the incident state with no marker. Validate-before-mutate
is the stronger invariant, and it is worth paying an explicit resolution step
for.

### D. Fail closed when no conventional native directory exists

If neither `/usr/lib/<arch>-linux-gnu` nor `/usr/lib64` holds a `libcuda.so.1`,
refuse to probe at all.

Rejected as stated: hosts that mount driver libraries elsewhere (the legacy
`/usr/local/nvidia/lib64` layout) worked before and would be turned into held
`-auto` promotions on healthy hardware. The danger is not "unconventional
layout", it is "the answer came from a compat library" — so the refusal is keyed
on that, verified after the fact, rather than on where the file was found.

### E. Cross-check the library against `nvidia-smi --query-gpu=driver_version`

Path shape (`/compat/`, `/stubs/`) is a packaging convention, not proof. Since a
genuine driver library is named `libcuda.so.<driver version>`, reject any
candidate whose filename names a *different* version than the CSV query API
reports — a different interface from the table the 610 rename touched.

Built, reviewed, and removed the same day. Three executed results killed it:

* it refused a healthy `libcuda.so.1 -> libcuda.so.1.1` chain, which ships on
  real hosts — `1.1` parses as a driver version, and the guard's own comment
  reasoned about `libcuda.so.1` and stopped one dot short;
* it did **not** catch the threat it was written for — a compat library copied
  into the arch directory under the plain SONAME passes untouched;
* NVIDIA's compat packages *are* named with a driver version, so the premise
  ("a compat one never is") was simply false.

The direction of failure decided it. A refusal is a hard boot abort *and* a QA
failure, so a wrong rejection is fleet-wide and holds every `-auto` tag —
including the one carrying this fix. That is the shape of the incident being
fixed, re-created by its fix. Demoting instead of vetoing was considered and
rejected too: it keeps the complexity and the false premise while closing
nothing. Verification stops at what the loader actually mapped.

## Decision

**One resolver, in `/opt/instance-tools/bin/cuda-driver-version`, with two
modes.**

* Default mode returns the *effective* version — whatever the loader resolves,
  compat included — and keeps a text fallback that tolerates both nvidia-smi
  spellings. This is the right answer for reporting (`portal-aio` capabilities,
  logs).
* `--native` returns the *driver's own* version, for any decision about forward
  compat. It `dlopen`s an absolute path (a name containing `/` performs no
  search at all), preferring the current-ABI directory, then **verifies from
  `/proc/self/maps` which file was actually mapped** and refuses if it is under
  a `compat/` or `stubs/` directory. Verification stops there, deliberately —
  option E above explains why a filename-versus-`nvidia-smi` cross-check was
  built and then removed.

  A final unpinned load is always tried, not only when no absolute candidate
  exists — a candidate can be *rejected* as well as absent, and the loader's own
  answer is still worth checking. It is safe to try because the verdict never
  depends on how the file was found: every candidate faces the same post-hoc
  check on what was actually mapped. So option D's regression does not occur
  while the compat hole stays shut.

  `--native` has no text fallback: nvidia-smi renders the effective version, and
  a caller that asked for the native reading gets the native reading or nothing.
  A candidate that loads but cannot answer (a placeholder `.so` without the
  symbol) is skipped, not fatal — one bad file must not end the search.

Both callers — `05-configure-cuda.sh` and `base/60-gpu-cuda` — use `--native`.
Neither re-derives it.

**The boot script validates before it mutates.** The version is read and
shape-checked before a single loader entry is touched; a failure on *that* path
returns 1 with the existing configuration intact. This is scoped deliberately:
the loader cleanup still runs before `try_forward_compat`, so the compat
decision is not covered by it — see the next paragraph, which addresses that
path by detection rather than by ordering.

**A lost forward-compat is retried, then recorded.** `try_forward_compat`
returns 1 for several unrelated reasons — not needed, disabled, no compat
libraries shipped, or its `cuInit` probe failed — and the selection that follows
cannot tell them apart. On a *restart* the last one is not benign: an instance
running CUDA 13.0 through compat comes back on 12.4 because a probe failed at
boot stage 05, where nothing has waited for the driver, and everything the
customer compiled against `libcudart.so.13` breaks. So the probe is attempted
three times with a pause — a device that is merely not ready yet must not be
mistaken for a consumer GPU that can never use compat — and only then does the
fallback stand.

If it does, `configure_cuda` records the condition and the boot continues (a
fallback has already been chosen; aborting would leave nothing configured). Two
properties of that record were learned the hard way:

* it is keyed on a **durable** marker (`/etc/vast-cuda-compat-established`),
  not on `0-compat-cuda.conf`, because the cleanup deletes the conf — so the
  condition was reported on exactly the boot it occurred and every later boot
  called the still-degraded instance healthy;
* it fires only when the selection **actually changed**. Every shipped config
  installs a single toolkit, where nothing can move; claiming it "fell back to
  an older toolkit" there is false, and that state is already caught correctly
  by `base/60-gpu-cuda`'s compat assertions.

First boot on a consumer GPU takes the same code path and is explicitly not
this — compat was never carrying that instance, so nothing was lost.

**The abort is detected on purpose.** `configure_cuda` clears
`/run/vast-cuda-config-failed` at the start of every run and writes the reason
there on abort or downgrade; `base/60-gpu-cuda` asserts on it. Previously the
state was caught only when the image happened to ship an *indirect*
`/usr/local/cuda`. A failure to write the marker is announced rather than
swallowed — an unwritable `/run` would otherwise restore the invisibility this
exists to end.

**Version comparison is component-wise, not `awk`'s.**
`awk "BEGIN {exit !(13.10 > 13.9)}"` is false — awk reads both as decimals — and
every comparison in the boot script feeds toolkit selection. The config table
already reaches 13.3, so the first double-digit minor would have selected the
wrong toolkit silently, which is the failure class this change exists to remove.

**There is an escape hatch.** `--native` refuses rather than guessing, and a
refusal aborts CUDA configuration for the session. `VAST_CUDA_MAX_OVERRIDE=X.Y`
lets an operator supply the value directly, shape-checked exactly like a probed
one and announced loudly, so a host class we got wrong is fixable from a template
env rather than by a rebuild.

**Three runtime assertions in `base/60-gpu-cuda`**, each verified in both
directions: the toolkit's libraries are reachable through `ldconfig`; the driver
version is a version; `/usr/local/cuda` resolves to a `cuda-X.Y` directory.

**Codified, not merely described:**

* **L063** — no shipped script parses nvidia-smi's table for the CUDA version.
* **L064** — no shipped script open-codes the native-libcuda bypass.
* **L062** (tightened) — a deferred failure must be reported before every exit
  that does not fail. The rule now understands `http_check`, `test_skip`, branch
  structure and helper functions, because its first form certified the very bug
  it was written for.

Scope of L063/L064 is every script that ships inside an image, extension or not
— 10 of the 12 tools in `ROOT/opt/instance-tools/bin` have no extension,
including the helper itself.

## Binding conditions

1. **The helper's behaviour is pinned by an executed test, not by review.**
   `tools/imagegen/tests/test_cuda_driver_version.py` runs both harnesses in a
   container against fabricated `libcuda.so.1` files at real absolute paths —
   the only way to test a resolver whose subject is the dynamic loader. The
   boot-and-test harness runs the pair, because the failure mode is the two
   agreeing on a wrong value.
2. **The breadcrumb is cleared at the start of every boot.** `/run` is part of
   the container's overlay here (docker does not tmpfs-mount it), so a stale
   marker would fail the QA gate forever.
3. **L064 must not fire on compat-presence probes.** Asking "does this toolkit
   ship compat libraries" with `compgen -G .../libcuda.so.*` is a different,
   legitimate question that both callers ask; keying the rule on the bare name
   made it fire on correct shipped code.
4. **`imagegen lint --all` stays clean** (27 images, 0 errors) and every new rule
   has a mutation test against a real file.
5. **Nothing in the boot path calls `nvidia-smi` without a timeout.** This
   helper runs from the first boot script, which is *sourced*, so a wedged driver
   would stop the instance ever becoming ready. An empty reading is already a
   handled outcome; a hang is not.
6. **The live QA gate cannot reach either of the two conditions above.** It boots
   each instance exactly once, so the restart-only compat loss is structurally
   out of reach, and it does not filter for a 610-branch driver. Before
   promotion, run one gate pass filtered to `driver_version.gte=610` and one on a
   datacenter host where forward compat is genuinely active (a `cuda-13.x` config
   on a 12.x driver). The container harnesses cover the mechanism; only a live
   run covers the host.

## Consequences

Positive:

* The number the fleet's CUDA selection depends on now comes from a stable C ABI
  and cannot be reworded by a driver bump.
* A probe that cannot prove its answer is native returns nothing, and nothing is
  a value every caller already handles safely.
* The driver-version read changed from "destroy then abort" to "abort having
  changed nothing", and both that abort and a lost forward-compat are now
  asserted rather than inferred.
* The test that gates promotion no longer shares an implementation with the
  thing it tests.

Accepted negatives:

* `--native` can refuse on a host where the old code would have returned a
  (possibly correct) answer — no libcuda loadable at all. That holds the `-auto`
  tag for that config. Deliberate: a wrong toolkit selection is silent and
  reaches customers, a held tag is loud and reaches us.
* Two container-based tests in an otherwise-fast suite (~35s). Judged worth it:
  no pure-python test can observe loader resolution, and this defect was found by
  a customer, not by CI.
* L062's walk is a control-flow approximation, not a bash parser. It is
  deliberately asymmetric — conservative about what clears a pending failure,
  generous about what defers one. Two blind spots remain and are known: a
  `fail_later` inside a subshell or pipeline, where the record is lost at runtime
  and nothing static can see it; and per-arm merging of a `case`, which is
  linear rather than branch-aware (arms are detected, but not treated as
  alternatives — the conservative direction).
* `--native` refuses without retrying, on the first boot script, at the moment
  the driver is least reliable. That is deliberate — `NVIDIA_DRIVER_CAPABILITIES`
  is baked and asserted by `base/55-environment.sh`, so libcuda is always
  injected — but it means a systemic failure (a container-runtime change
  relocating the driver libraries) would fail every host at once. See below.

## What would reverse this

* NVIDIA making `cuDriverGetVersion` unavailable or unreliable in a container
  (it needs no `cuInit` and no usable device today).
* Evidence that `--native`'s refusal path fires on healthy production hosts —
  the QA gate makes that visible as held tags rather than silent breakage, and
  would argue for widening the candidate search rather than reopening the
  unverified probe. Note the correlated-failure shape this implies: because
  refusal is both a hard boot abort *and* a QA failure, a change in how the
  container runtime injects driver libraries would fail every host simultaneously
  and hold every `-auto` tag — including the tag carrying the fix. Retrying the
  probe, or degrading to a warning on the promotion path only, is the response if
  that ever happens.
* A shipped, stable machine-readable driver-capability interface from NVIDIA
  that supersedes both the C ABI call and the text fallback.
