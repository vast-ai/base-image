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
  a `compat/` or `stubs/` directory. Only when no absolute candidate exists does
  it fall back to an unpinned load — and applies the same post-hoc check, so
  option D's regression does not occur while the compat hole stays shut. It has
  no text fallback: nvidia-smi renders the effective version, and a caller that
  asked for the native reading gets the native reading or nothing.

Both callers — `05-configure-cuda.sh` and `base/60-gpu-cuda` — use `--native`.
Neither re-derives it.

**The boot script validates before it mutates.** The version is read and
shape-checked before a single loader entry is touched; a failure returns 1 with
the existing configuration intact.

**The abort is detected on purpose.** `configure_cuda` clears
`/run/vast-cuda-config-failed` at the start of every run and writes the reason
there on abort; `base/60-gpu-cuda` asserts on it. Previously the state was
caught only when the image happened to ship an *indirect* `/usr/local/cuda`.

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

## Consequences

Positive:

* The number the fleet's CUDA selection depends on now comes from a stable C ABI
  and cannot be reworded by a driver bump.
* A probe that cannot prove its answer is native returns nothing, and nothing is
  a value every caller already handles safely.
* The boot script's failure mode changed from "destroy then abort" to "abort
  having changed nothing", and that abort is now asserted rather than inferred.
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
  generous about what defers one — and it still cannot see a `fail_later` inside
  a subshell or pipeline, where the record is lost at runtime. That remains
  uncovered.

## What would reverse this

* NVIDIA making `cuDriverGetVersion` unavailable or unreliable in a container
  (it needs no `cuInit` and no usable device today).
* Evidence that `--native`'s refusal path fires on healthy production hosts —
  the QA gate makes that visible as held tags rather than silent breakage, and
  would argue for widening the candidate search rather than reopening the
  unverified probe.
* A shipped, stable machine-readable driver-capability interface from NVIDIA
  that supersedes both the C ABI call and the text fallback.
