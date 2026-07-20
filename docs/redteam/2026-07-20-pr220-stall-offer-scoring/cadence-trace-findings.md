# status_msg cadence trace — validation for the stall heuristic

The red-team's "would change my mind" asked for a captured `status_msg` cadence
trace from a slow multi-GB pull. Captured it. It **fails** the heuristic.

## Setup
- Instance 45390642, image `vastai/vllm:v0.25.1-cuda-12.9` (~tens of GB), disk 20 GB.
- Host: RTX 4090, **111 Mbps** (offer 36538118) — a deliberately slow, but *healthy*, verified host.
- Polled `get_instance_status` every ~5 s for 25 min, logging `(t, actual_status, status_msg)`.

## Results (276 samples over 1497 s)
- **Never reached `running`** in 25 min — still `loading` when destroyed.
- **`status_msg` carries NO byte-level progress** — 0 of 276 samples contained a byte counter
  (`\d+ [KMG]B`). It is docker *layer-state* only: `Pulling fs layer` / `Verifying Checksum` /
  `Download complete` / `Pull complete`.
- **Longest frozen segments (healthy, still progressing):**
  - `f1555660aa88: Download complete` — **609.6 s (10.2 min)** and *still frozen* at destroy (true value unbounded, ≥610 s).
  - `aaf5445eaf29: Pull complete` — **474.1 s (7.9 min)**.
  - several more in the 20–50 s range.

## Conclusion
Vast's `status_msg` is **not a reliable liveness signal** during the loading phase:
- no sub-layer/byte granularity, and
- a *healthy* slow host freezes it for **10+ minutes** (observed; unbounded) during
  post-download extraction / container-create.

So a `status_msg`-frozen-for-N heuristic **cannot distinguish stuck from slow-healthy**.
Any `stall_timeout` low enough to beat `POLL_TIMEOUT` (40 min) meaningfully (≈15–25 min)
sits *inside* the observed healthy-freeze range → false-abandons healthy slow hosts.
The red-team's strongest objection is empirically confirmed and is close to fatal for the
heuristic **as designed** (frozen-status-based).

## Recommendation
Drop the `status_msg`-based stall detection. Rely on `POLL_TIMEOUT` (40 min in base-image;
reduce vast_landing's 2 h to match) as the clean, false-positive-free bound on the loading
phase. Keep the offer-selection changes (run-cost tie-break + determinism key), which the
red-team and audit found sound.
