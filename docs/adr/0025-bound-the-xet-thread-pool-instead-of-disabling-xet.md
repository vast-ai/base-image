# ADR 0025 — Bound the Xet thread pool instead of disabling Xet

- **Status:** Accepted
- **Date:** 2026-08-13
- **Amends:** ADR 0014 (container-aware CPU thread caps). The safety valve, its
  trigger and its managed-block mechanism are unchanged; only what it does to
  Hugging Face downloads changes.

## Context

ADR 0014 added a boot-time safety valve for hosts whose container sees far more
CPUs than it is entitled to *and* whose `pids.max` is pathologically low (the
motivating host: 384 visible cores, ~46-core quota, `pids.max=1024` instead of
the usual ~256 per allocated core). Native runtimes size their thread pools to
the visible core count, a few pools exhaust the pid budget, and `pthread_create`
fails with `EAGAIN` — killing model downloads and inference.

For most runtimes the valve caps a thread-count variable. For Hugging Face
downloads it did something blunter: it set `HF_HUB_DISABLE_XET=1`, turning off
Xet entirely. The reason is in the code:

> hf_xet's Rust pool ignores the thread-count vars, so disable xet on these hosts
> (its only cost — slower dedup transfer — applies solely where it would
> otherwise crash).

Two things have changed since that was written.

**Xet is no longer an opt-in.** `hf-xet` is a hard dependency of
`huggingface_hub` — not an extra — for `x86_64`/`aarch64`, and has been since
**0.34.0 (2025-07-25)**:

```
hf-xet<2.0.0,>=1.5.2; platform_machine == "x86_64" or ... "aarch64"
```

`Dockerfile` installs `huggingface-hub[cli]` unpinned, so every base image built
since then ships it and `HF_HUB_DISABLE_XET` defaults to `False`. Disabling it is
therefore not "declining an optimisation", it is removing the default download
path — and doing so specifically on the hosts with the least CPU entitlement,
which are the ones that benefit most from deduplicated transfer.

**The premise that the pool cannot be bounded is only half true.** It was tested
with `RAYON_NUM_THREADS`, which hf_xet genuinely ignores. But hf_xet's pool is
Tokio's, and Tokio's default multi-thread runtime reads `TOKIO_WORKER_THREADS`.

### Measurement

Peak thread count of a single `hf download openai/whisper-tiny`, sampled from
`/proc/<pid>/task`, in a container with the visible core count varied by
`--cpuset-cpus` (`huggingface_hub 1.27.0`, `hf-xet 1.6.0`):

| visible cores | baseline | `TOKIO_WORKER_THREADS=2` | `TOKIO_WORKER_THREADS=8` |
|---|---|---|---|
| 2  | 29 | 28 | 34 |
| 8  | 34 | 28 | 35 |
| 16 | 45 | 28 | 34 |

Baseline grows with visible cores — roughly one thread per core above a ~28
floor, which extrapolates to ~400 threads for one `hf` process on the 384-core
host. **Bounded, the footprint is flat regardless of core count**, which is
exactly the property the valve needs.

For completeness, at 16 visible cores: `HF_HUB_DISABLE_XET=1` → 10 threads;
`RAYON_NUM_THREADS=2` → 42 (no effect); `HF_XET_HIGH_PERFORMANCE=0` → 42;
`HF_XET_FIXED_DOWNLOAD_CONCURRENCY=2` → 42;
`HF_XET_CLIENT_AC_MAX_DOWNLOAD_CONCURRENCY=2` with adaptive concurrency off →
45; `HF_XET_DATA_MAX_CONCURRENT_FILE_DOWNLOADS=1` → 40.

## Options considered

### A. Keep disabling Xet (status quo)

Rejected. It works, but it now costs the default download path on the hosts
least able to spare bandwidth or CPU, and it rests on a premise the measurement
above contradicts. It also silently diverges further as Hugging Face migrates
more repositories to Xet storage.

### B. Bound Xet with hf_xet's own knobs

`HF_XET_FIXED_DOWNLOAD_CONCURRENCY`, `HF_XET_CLIENT_AC_MAX_DOWNLOAD_CONCURRENCY`,
`HF_XET_DATA_MAX_CONCURRENT_FILE_DOWNLOADS`, `HF_XET_CLIENT_ENABLE_ADAPTIVE_CONCURRENCY`.

Rejected on evidence: measured at 38-45 threads against a 42-45 baseline. They
govern *network* concurrency — in-flight range requests and files — not the size
of the runtime's worker pool. They are the knobs that look right and are not.

### C. Restrict the container's visible CPUs

Make the pools shrink by making `nproc` tell the truth (a cpuset). Rejected: the
cpuset is the host's to set, not ours; we are inside the container, and by the
time any boot hook runs the cpuset is fixed.

### D. Bound the Tokio worker pool (chosen)

Set `TOKIO_WORKER_THREADS` to the same cap the valve already computes, and stop
setting `HF_HUB_DISABLE_XET`.

## Decision

On a host that trips the ADR 0014 trigger, the managed block sets
`TOKIO_WORKER_THREADS=<cap>` and no longer sets `HF_HUB_DISABLE_XET`. Xet stays
enabled everywhere; its thread pool is bounded where the pid budget demands it.

`HF_HUB_DISABLE_XET` remains honoured as a **user/template** variable — the valve
simply stops setting it, so anyone who wants Xet off can still say so and the
existing "never overwrite a user value" rule applies unchanged.

## Binding conditions

1. **The migration must unset, not merely stop writing.** `10-prep-env.sh` sources
   `/etc/environment` into the boot shell *before* stage 12 runs, so an
   already-capped instance reaches the valve with `HF_HUB_DISABLE_XET=1` already
   exported. Rewriting the managed block removes it from the file but not from
   the live shell, and supervisord — launched later in that same shell — would
   inherit it. Xet would then stay disabled forever on precisely the instances
   this change exists to help, with no signal. The valve must unset any variable
   that was in the *previous* block and is not in the new one, on the write path
   as well as the no-op path.
2. **`TOKIO_WORKER_THREADS` is generic, and that is accepted deliberately.** It
   bounds every Tokio-based Rust program in the instance, not just hf_xet — `uv`,
   `tokenizers`, parts of some inference stacks. On a host where the valve is
   already capping OMP and friends to the CPU entitlement, bounding Tokio to the
   same number is consistent and strictly less blunt than removing a download
   path. It is a wider reach than the variable it replaces and is recorded as a
   decision rather than a side effect.
3. **The mechanism is a convention, not an API.** If hf_xet ever constructs its
   runtime with an explicit `worker_threads()`, `TOKIO_WORKER_THREADS` stops
   working *silently* and the storm returns. The measurement above is recorded
   here so the claim is falsifiable, and the on-box test asserts the variable is
   written; neither can detect the library changing under us. Re-measure when
   `hf-xet` majors.
4. **The on-box test keeps its mutation.** `tests/base/56-cpu-thread-limits.sh`
   must cover the new variable *and* the stale-variable migration, and must fail
   if the cap write is removed.

## Consequences

Positive:

- Deduplicated transfer survives on the hosts that need it most, instead of
  being switched off there.
- One fewer place where our images deviate from Hugging Face's defaults, so the
  behaviour a user debugs on a Vast instance matches what they read upstream.
- The bound is core-*independent*, so it holds on a host with any core count —
  including whatever comes after 384.

Accepted negatives:

- A bounded Xet download still costs ~28 threads against ~10 disabled.
  Irrelevant against `pids.max=1024`; recorded so the comparison is honest.
- `TOKIO_WORKER_THREADS` reaches beyond Hugging Face (condition 2).
- We now depend on a Tokio convention holding (condition 3).

## What would reverse this

- Evidence that a bounded Xet download still exhausts the pid budget on a real
  low-pids host — the valve would go back to disabling, and the measurement here
  would be the thing shown to be unrepresentative.
- hf_xet pinning its own worker count, making `TOKIO_WORKER_THREADS` inert.
- `TOKIO_WORKER_THREADS` throttling something in the instance that matters more
  than the download it protects, on the narrow set of hosts where the valve
  fires.
