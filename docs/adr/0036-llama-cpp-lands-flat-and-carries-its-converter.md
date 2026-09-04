# ADR 0036 — the llama.cpp bundle lands FLAT and carries its own converter

- **Status:** Accepted
- **Date:** 2026-09-04
- **Decision owner:** Rob Ballantyne
- **Amends:** ADR 0018 (prebuilt llama.cpp) — its extraction layout, not its choice of bundle

## Context

ADR 0018 replaced a source compile with the Unsloth fork's prebuilt bundle. That decision
stands: the hash-verified bundle removed a 20-40 minute build, a fabricated `nvidia-smi`
stub, and a hand-pinned arch list. What it got wrong was where the bundle lands.

The bundle is FLAT. ADR 0018 extracted it into `build/bin/` because the studio installer
expects a llama.cpp source-tree shape, and lifted two marker files to the root. That
satisfied one consumer and broke another:

| consumer | looks in |
|---|---|
| `studio/setup.sh` | `build/bin/` (source-tree shape) |
| `unsloth_zoo.check_llama_cpp` | the folder ROOT **only**, on Linux |

`check_llama_cpp` builds `search_dirs = [llama_cpp_folder]`; `build/bin` is consulted on
Windows and nowhere else. With the root holding nothing but two marker files, a GGUF
export concluded llama.cpp was missing and installed its own prebuilt.

**Measured 2026-09-04, on a live instance.** After a training run, an export reinstalled
llama.cpp. Its resolver picked the **CPU** bundle and laid it over the root:

```
/opt/llama-cpp/UNSLOTH_PREBUILT_INFO.json
  "asset": "app-b10715-mix-86bd2d3-linux-x64-cpu.tar.gz"
  "installed_at_utc": "2026-09-04T10:15:07Z"

root       libggml-cpu-* only, NO libggml-cuda.so, llama-quantize a 24-byte stub
build/bin  libggml-cuda.so intact -> CUDA0: NVIDIA RTX PRO 6000 Blackwell
```

Inference kept using `build/bin` and stayed on the GPU, which is why the downgrade was
invisible until an export was attempted — long after every build assertion and QA cell had
passed. This is ADR 0016's CPU-only defect resurfacing on the far side of the build, where
nothing we gate on can see it.

**A second fact, found while fixing the first.** The `cuda12-portable` bundle is binaries
ONLY: zero `.py` files, no `gguf-py`, verified against the real artifact. `check_llama_cpp`
requires a quantizer AND a converter, so with that bundle alone it can never pass —
whatever the layout. Fixing the layout without the converter would have left the reinstall
intact and the diagnosis looking solved.

## Options considered

**A. Symlink `build/bin/*` up to the root.** Smallest diff. Rejected: it inverts which
path is real, and the studio installer writes into the tree — a later install would land
beside the links rather than replacing them, leaving the same two-install ambiguity.

**B. Point the studio at our path with `UNSLOTH_LLAMA_CPP_PATH`.** Rejected as the whole
fix: it moves where the check looks without changing what it finds, so the missing
converter still fails it. Worth setting as defence in depth, but it does not stand alone.

**C. Install the `-cpu` bundle as well, for its converter.** Rejected: two full bundles,
and the CPU binaries would shadow the CUDA ones at exactly the root position that caused
this.

**D. Extract flat at the root, mirror into `build/bin`, and fetch the converter from the
matching source tag (chosen).**

## Decision

The bundle extracts FLAT at `/opt/llama-cpp`, which is its own shape and the shape
`check_llama_cpp` expects. `build/bin/` is then populated by **hardlink** — same inodes, so
no second copy of a 350 MB backend — preserving the source-tree shape `setup.sh` wants.

`convert_hf_to_gguf.py` and `gguf-py/` are fetched from `unslothai/llama.cpp` at
**`${LLAMA_CPP_VERSION}`** — the same tag the binaries were built from, so the two halves
cannot drift. Pure Python, no build.

Both layouts are asserted separately, so a regression names which consumer it broke. The
converter is proved USABLE rather than merely present, by importing `gguf` from the
assembled tree using `/venv/main`'s interpreter — the one that has numpy, and the one the
exporter actually runs under.

## Binding conditions

- The root is the real install. Anything that makes `build/bin` authoritative again
  reintroduces the reinstall.
- The converter is pinned to the binaries' tag. Fetching it from `master` would let the
  converter and the binaries diverge silently.
- The import proof uses a venv interpreter. Asserting with the system `python3` would fail
  on a missing numpy and prove nothing about the exporter.

## Consequences

- The exporter finds a complete llama.cpp and never reinstalls, so the hash-verified CUDA
  bundle is what stays on disk — which is what made ADR 0018's verification meaningful in
  the first place.
- Two extra network fetches at build time (a raw file and a source tarball), against one
  avoided 336 MB bundle download at runtime.
- Disk cost of the mirror is zero: hardlinks, not copies.

### Hardlinks and the $WORKSPACE volume

Hardlinks cannot cross a filesystem, and `$WORKSPACE` is a volume on many instances, so
this was checked rather than assumed:

1. **Build time.** Both ends of the link are inside `/opt/llama-cpp`, created in one RUN
   layer. There is no mount boundary between a directory and its own child, so `EXDEV` is
   not reachable.

2. **Runtime.** `$WORKSPACE` never receives these files. `36-sync-workspace.sh` copies
   `/opt/workspace-internal` with `cp -ru`, and the `llama.cpp` entry there is a SYMLINK
   to `/opt/llama-cpp`. `cp -ru` preserves symlinks rather than dereferencing them, so the
   volume gets a 14-byte link and the binaries stay in the image. Confirmed on a live
   instance:

   ```
   /workspace/unsloth/llama.cpp -> /opt/llama-cpp
   ```

   No hardlink is copied across the image/volume boundary, so mounting a volume at
   `$WORKSPACE` cannot split the linked pair or duplicate the 350 MB. Had the sync
   dereferenced instead, the mirror would have doubled what lands on the volume — which is
   the failure this note exists to rule out.

3. **The mirror must carry symlinks, not just files — and must be proved to RUN.**
   The first attempt used `find -maxdepth 1 -type f -exec ln -f`, and the build failed on
   `llama-server does not execute`. The binaries carry `RUNPATH=$ORIGIN`, so each copy
   resolves its libraries beside itself, and `-type f` silently skipped the five versioned
   `.so` symlinks (`libggml.so.0 -> libggml.so.0.22.0` and friends). The mirror had 56
   regular files, none of the links, and `ldd` reported five `not found`.

   `cp -al` is the correct instrument: it hardlinks regular files and recreates symlinks
   as symlinks. The bundle ships its own `build/`, so that entry is excluded rather than
   recursed into.

   The deeper lesson is about the assertion, not the command. Every check at that point
   tested PRESENCE — `test -x`, `test -f` — and presence is not enough when resolution is
   position-dependent: a mirror can be complete in filenames and still not start. Both
   binaries are now executed (`--version`, the cheapest call that forces the full link
   closure). Had the original assertions included that, this would have been caught in the
   first build rather than the second.

4. **A failing link is silent on its own.** `find -exec` does not propagate the exit status
   of the command it runs: a failing `ln` leaves `find` returning 0 and the build
   continuing. Measured. The separate `build/bin` assertions are therefore load-bearing —
   they are what turns a silently-missing mirror into a failed build.
- **Not gated by QA, and cannot be.** The reinstall happens at export time, after every
  cell has closed. `unsloth.d/11-llama-offload` correctly enumerated a GPU on the run that
  later degraded. The build-time assertions are the whole control here; there is no live
  cell that would catch a regression.

## What would reverse this

Upstream shipping the converter inside the CUDA bundle, or `check_llama_cpp` learning to
search `build/bin` on Linux. Either would make the fetch redundant; the first would also
make the flat layout the only thing still needed.
