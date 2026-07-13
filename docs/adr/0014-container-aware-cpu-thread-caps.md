# ADR 0014 — Container-aware CPU thread caps at boot (low-pids safety valve)

- **Status:** Proposed
- **Date:** 2026-07-13
- **Decision owner:** Rob Ballantyne

## Context

A Vast instance is a Docker container. Its kernel is shared; it sees the physical
host's full CPU count (`nproc`/`os.cpu_count()`) but is *entitled* only to a CPU
quota (a slice), and it has a hard container-wide thread/process cap
(`pids.max`). Native/ML runtimes — OpenMP/`libgomp`, OpenBLAS, MKL, torch, Rust
`rayon`/`hf_xet` — size their thread pools to the **visible** core count, not the
quota. When `pids.max` is also low, a few such pools exhaust it and
`pthread_create` fails with `EAGAIN` ("Resource temporarily unavailable").

This surfaced as the **ACE-Step image failing every generation** on one host:

- Model download crashed in `hf_xet` (Rust): `failed to spawn thread: WouldBlock`.
- Inference crashed in `libgomp`: `Thread creation failed`.

Confirmed mechanism on that box: `OMP_NUM_THREADS=8` dropped the API server from
**363 → 20 threads**, and generation then completed end-to-end. The failure
recurred on a **service restart**, not just first boot — relevant because
`/etc/environment` is re-read by every supervisor service on restart.

**We then sampled four hosts to test whether this is common or an outlier:**

| host | cgroup | `nproc` | quota-cores | `pids.max` | pids ÷ visible-core |
|------|--------|---------|-------------|------------|---------------------|
| ACE-Step (crashed) | v1 | 384 | 46 | **1024** | **2.7** |
| B | v2 | 384 | 46 | 11776 | 30.7 |
| C | v2 | 128 | 61 | 15616 | 122 |
| D | v2 | 128 | 41 | 10240 | 80 |

Two facts fall out:

1. **CPU oversubscription is universal** — every host sees the whole machine but is
   entitled to a slice (you rent ~46 of 384 cores; `nproc` still says 384). So
   oversized thread pools happen everywhere; on their own they only waste cycles.
2. **The crash needs a pathologically low `pids.max`.** Healthy hosts follow
   `pids.max ≈ 256 × allocated-cores` (B/C/D fit exactly: 46→11776, 61→15616,
   41→10240) and sit at **30–122 pids per visible core**. The crashed host got
   **1024 = 256 × 4** — a pids budget sized as if it had 4 cores, not the 46 it
   rented. It is a misconfigured/rogue host (the only cgroup-v1 sample), not the
   fleet default. At its budget (2.7 pids/visible-core) the oversized pools blow
   the cap; at a healthy budget the identical pools have 10–45× headroom and never
   do.

So the reported crash is a **rare bad-host condition**, and — critically — the bad
condition (cgroup version, `pids.max`) is **not visible in the rental offer**; you
only learn it once booted. You cannot select against it. That makes a boot-time,
on-box adaptation the only mechanism that can protect an unpredictable placement.
This is the container-unaware-runtime problem the JVM (`UseContainerSupport`) and
Go (`automaxprocs`) already solved for their runtimes.

This is a **base-image `ROOT/` boot concern**, inherited by every derivative and
external image. Boot hooks in `/etc/vast_boot.d/*.sh` are sourced in order (after
`10-prep-env.sh`, which creates `/etc/environment`); `/etc/environment` is sourced
by every supervisor service and re-read on service restart.

## Options considered

**Scope of intervention**
- **A. A narrow low-pids *safety valve* (chosen).** Cap thread pools **only** on a
  host whose pids budget is dangerously low; a no-op on every healthy host. Targets
  exactly the failure, near-zero fleet blast radius.
- **B. Fleet-wide "always cap pools to the quota" for perf hygiene. Rejected.**
  Oversubscribed pools are wasteful everywhere, but capping every instance is a
  broad, silent behavior change (a tenant on a big-but-throttled box suddenly sees
  fewer BLAS threads) for a diffuse benefit — the weakest, most-contested idea in
  review. Not worth the blast radius; the crash is what we must fix.
- **C. Do nothing in the image; just report/avoid the bad host. Rejected as
  sufficient.** You can't see `pids.max` before renting, so you can't avoid it;
  reporting fixes one host but not the next random placement. (We still report it —
  see Consequences — but it can't be the only defense.)

**The trigger**
- **D. Oversubscription ratio (`visible > quota × 1.5`). Rejected.** True on *all
  four* hosts above — it fires on healthy hosts too, collapsing into option B.
- **E. Pids headroom per visible core (`pids.max < nproc × 16`) (chosen).** Directly
  measures the failure condition. The crashed host is 2.7; every healthy host is
  30–122. The threshold 16 sits deep in that gap, so it fires on the bad host and no
  other.

**The cap value**
- **F. Fixed 8 (the one tested value). Rejected:** cargo-cults one box; arbitrary
  elsewhere.
- **G. `floor(quota)`. Rejected:** quota-sized pools *stack* (libgomp + OpenBLAS +
  MKL + torch intra/inter-op ≈ 180 threads/process; a multi-worker dataloader forks
  that) and can still approach a low `pids.max`. Quota is the entitlement, not a safe
  per-pool number.
- **H. `clamp(round(quota), 2, ceiling=16)`, `VAST_CPU_THREAD_CEILING`-overridable
  (chosen).** The ceiling bounds any single pool's draw on the pid budget regardless
  of a generous quota; the floor of 2 protects inference latency on a sub-2-core
  quota. 16 stays within a factor of the proven-safe 8 while returning some
  throughput.

**Xet (the download crash)**
- **I. Rely on `RAYON_NUM_THREADS` to bound `hf_xet`. Rejected as load-bearing.** One
  live test suggested it does, but adversarial review argues `hf_xet` is a **tokio**
  runtime sized off the cpuset, which `RAYON_NUM_THREADS` does not govern. The
  mechanism is unverified; do not depend on it.
- **J. Set `HF_HUB_DISABLE_XET=1` on the safety-valve path (chosen).** Proven. Its
  only cost — losing xet's dedup transfer speedup — applies solely on hosts where xet
  otherwise **crashes**, so no real regression. On healthy hosts (valve not tripped)
  xet stays enabled.

**Codification**
- **K. A linter `RULES` code (the repo's usual Bug→Invariant step). Rejected as
  theater — called out explicitly.** "Effective cores, not visible cores, size the
  pools" is a **runtime cgroup fact** invisible to a static analyzer over
  Dockerfiles/`ROOT` files; a `RULES` code could only assert "the hook file exists,"
  proving nothing about the arithmetic, trigger, or override logic.
- **L. A pure decision function + on-box test + mutation test (chosen).** The
  decision is factored into side-effect-free bash functions unit-tested with
  synthetic `(pids.max, nproc, quota)` tuples — including the pathological host we
  can no longer rent on demand — plus a live-host consistency check. Mutating the
  hook (drop the clamp / the trigger / the idempotent strip / the override guard)
  makes the test fail. This upholds the protocol's intent ("no mutation test = the
  check doesn't count") at the layer where the invariant actually lives.

## Decision

Add `ROOT/etc/vast_boot.d/12-cpu-thread-limits.sh`, a **low-pids safety valve**.
Every boot it:

1. Reads the container's `pids.max` (cgroup v2 `/sys/fs/cgroup/pids.max`, v1
   `/sys/fs/cgroup/pids/pids.max`), `nproc`, and CPU quota-cores (v2 `cpu.max`, v1
   `cpu.cfs_quota_us`/`cpu.cfs_period_us`, rounded).
2. **Intervenes only when `pids.max < nproc × 16`.** Any unreadable/unlimited pids
   value, or an ample budget, is a **no-op**.
3. Computes `cap = clamp(round(quota), 2, ${VAST_CPU_THREAD_CEILING:-16})` (no
   readable quota ⇒ the ceiling), and for `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`,
   `MKL_NUM_THREADS`, `NUMEXPR_NUM_THREADS`, `NUMEXPR_MAX_THREADS`,
   `VECLIB_MAXIMUM_THREADS`, `RAYON_NUM_THREADS` writes the cap, plus
   `HF_HUB_DISABLE_XET=1` — **only for vars the user/template has not set**, judged
   from `/etc/environment` (outside the block) **and** `${WORKSPACE}/.env`.
4. Writes them into a **delimited managed block** in `/etc/environment`, **stripping
   its own prior block first** and recomputing every boot (migration-safe), and
   `export`s them so supervisord (started later in the same boot) inherits them.
5. **No-op reconciliation** (formerly-capped instance now on a healthy host, e.g. its
   `pids.max` was corrected): removes the stale block, `unset`s **only the vars that
   block actually set** (parsed from the block, not a static list — so a user value
   is never touched and a var dropped from a future managed-set can't leak), then
   re-sources `/etc/environment` + `${WORKSPACE}/.env` to restore any real user value
   — because `10-prep-env.sh` (`export_env=true`) has already exported the stale block
   into the boot shell that later launches supervisord, so stripping the file alone
   would leave the caps live in-process.

Codified by `ROOT/opt/instance-tools/tests/base/56-cpu-thread-limits.sh` (unit +
live + wiring + mutation-tested), not a linter rule.

## Binding conditions

Non-negotiable; if any is refused the decision is void.

1. **Safety valve, not a fleet tuner.** No-op unless `pids.max < nproc × 16`. Healthy
   hosts are untouched — no thread caps, xet stays enabled.
2. **Recompute every boot, and shed on a healthy boot.** `/etc/environment` persists
   across stop/start (overlayfs) and a host's `pids.max` can be corrected; the managed
   block is stripped-and-recomputed every boot, and on a no-op boot the vars the stale
   block set are also cleared from the boot shell (not just the file) so supervisord
   does not inherit them. A cap from a prior boot never lingers, in the file or the
   process.
3. **Respect explicit overrides.** A value the user set via `/etc/environment` (incl.
   launch `-e`, dumped there by `10-prep-env.sh`) **or `${WORKSPACE}/.env`** always
   wins — the hook fills only unset vars, and the no-op path restores such values
   rather than clearing them.
4. **Fail-safe.** Unreadable/ambiguous cgroup state ⇒ no-op; never fall back to
   `nproc` as the cap basis.
5. **Xet disabled only on the tripped path**, and never relied upon being governed by
   `RAYON_NUM_THREADS`.
6. **Codified by the runtime test + mutation test, not a `RULES` code.** The linter
   baseline is unchanged/CLEAN. When the docs-tooling branch lands, add the
   corresponding `invariants.md` §4 blind-spot entry pointing at this test.

## Consequences

- The rare low-pids host degrades gracefully (capped pools) instead of crashing
  downloads/inference — for base services and every derivative/external alike.
- Every healthy host is a **no-op**: no throughput change, xet unaffected. Blast
  radius is confined to the pathological placement.
- **Complementary action (not in this repo):** report the crashed machine to Vast —
  `pids.max=1024` on a 46-core instance violates their own `256 × cores` default,
  likely an old cgroup-v1 host or an operator `--pids-limit`. Fixing it removes that
  host from the pool; the safety valve remains for the next unpredictable one.
- **Not covered** (documented): torch `set_num_interop_threads` (not env-driven), Go
  tooling (`GOMAXPROCS`), libraries reading `sched_getaffinity` directly, and a
  possible `cgroupns=host` layout where the standard cgroup paths could read the host
  root (fail-safe: a nonsensical read ⇒ no-op). If a genuine multi-service pids-sum
  failure ever appears on a *healthy*-budget host, per-service caps are the escalation.
- **Known limitation of the no-op path.** It can restore a user value that lives in
  `/etc/environment` or `${WORKSPACE}/.env`, but not one that exists *only* in the
  process env — e.g. a `-e OMP_NUM_THREADS=…` added via edit-instance *after* first
  boot, which `10-prep-env.sh` (write-once, identity-gated) never records in the file.
  On a healthy reboot that var is unset back to the library default rather than the
  user's number. This is a downstream symptom of `10-prep-env.sh`'s write-once design,
  not fixable in this hook; the safe direction (default, not a stale cap) is the lesser
  harm. Setting the override in `.env` avoids it entirely.
- The real bad host could not be re-rented (it was gone within days, and cgroup
  version/pids budget aren't selectable), so end-to-end validation on a live
  pathological host is opportunistic. The crash-fix itself was already demonstrated on
  that box during triage; the trigger/arithmetic/write logic is validated host-independently
  by the unit + mutation tests.

## What would reverse this

- **The durable cure is platform-side.** If Vast aligns the cpuset to the quota (so
  `nproc` reports the entitlement and runtimes self-size) and/or guarantees
  `pids.max` scales with allocated cores, the valve stops tripping and can be retired.
- `hf_xet` respecting a quota/explicit cap upstream — then the conditional
  `HF_HUB_DISABLE_XET` drops.
- Evidence the ceiling (16) or trigger (×16) is wrong on real workloads — retune the
  env override, or move to per-service caps.
