# ADR 0016 — Unsloth Studio: force the GPU llama.cpp build, and a known first-login password

- **Status:** Accepted
- **Date:** 2026-07-23
- **Decision owner:** Rob Ballantyne

## Context

Two defects were reported against the shipped `unsloth-studio` image and confirmed
on a live instance.

**1. llama.cpp ran on CPU.** The image bundles Unsloth Studio's `llama.cpp` for GGUF
inference/export. On the live box, `llama-server` was launched with `-ngl -1` (offload
all layers) but GPU utilisation was 0% and the process was burning CPU. Root cause: the
studio's `setup.sh` gates `-DGGML_CUDA=ON` on a **runtime GPU probe** (`nvidia-smi -L`,
falling back to `/proc/driver/nvidia/gpus`). Inside `docker build` there is no GPU, so
the probe fails and the build silently falls through to a CPU-only binary — the build
output contained only `libggml-cpu-*.so`, no `libggml-cuda.so`. Nothing failed, so the
image shipped and every inference offloaded to CPU. The Dockerfile even set up CUDA
driver stubs for link-time expecting a GPU build; the runtime gate defeated it silently.

**2. First login required sourcing a password off disk.** On first start the studio seeds
its admin account with a random diceware passphrase (`must_change_password=True`) written
to a bootstrap-password file beside its auth DB. It auto-fills that password into the login
page only for a **direct-loopback, same-origin** request. Behind the image's Caddy reverse
proxy the request is proxied, injection is suppressed, and the user must SSH in and read the
passphrase off disk before they can log in. There is no upstream switch to disable auth.

Both touch the image build/boot contract, so the change is recorded here and codified in the
linter (see Consequences). Relevant invariants: services bind loopback behind Caddy
([docs/invariants.md](../invariants.md) §services); no baked weights (§6, unaffected here).

## Options considered

### Login

- **Set `UNSLOTH_STUDIO_PASSWORD` to a fixed value (e.g. `password`).** Supported, non-interactive.
  Rejected as the sole fix: it routes through `update_password`, which **clears**
  `must_change_password` — so the weak default would become a permanent credential with no
  forced rotation.
- **Set `UNSLOTH_STUDIO_PASSWORD` to the per-instance portal token (`OPEN_BUTTON_TOKEN`).**
  Per-instance, not a repo constant. Rejected: users find the token hard to locate, and it
  still clears the forced-rotation flag.
- **Patch the vendored studio to bypass auth entirely.** Achieves true no-login. Rejected:
  a fragile sed-patch against AGPL upstream internals, re-applied every build and liable to
  break silently on a studio update, for a UI that already sits behind Caddy token auth.
- **Pre-seed the studio's own bootstrap-password file with `password` on a fresh instance
  (chosen).** The studio's `ensure_default_admin()` reads an existing bootstrap file and seeds
  the admin with that password **and `must_change_password=True`**. So the user logs in with a
  documented, memorable credential (no disk-sourcing) and is immediately forced to change it —
  using only the studio's own persistence hook, no code patch.

### GPU build

- **Rely on the studio's runtime GPU probe.** The status quo. Rejected: it is the bug — no GPU
  in `docker build` means a silent CPU-only binary.
- **Build llama.cpp ourselves with `-DGGML_CUDA=ON`, bypassing the studio.** Rejected:
  duplicates and forks the studio's tested build recipe (arch flags, CPU-dispatch variants,
  version pin); high maintenance for no benefit over steering the studio's own build.
- **Satisfy the studio's build gate and pin the arch list (chosen).** Provide a build-only stub
  `nvidia-smi` so the probe passes, put `nvcc` on PATH, and set `UNSLOTH_LLAMA_CUDA_ARCHS`
  (which the studio honours verbatim, so arch selection does not depend on a real GPU). Remove
  the stub before the layer ends so it never shadows the real runtime `nvidia-smi`. Then a
  post-build `test -f …/libggml-cuda.so` fails the build if the CUDA backend is missing.

## Decision

- **Login:** a fresh-instance boot hook (`ROOT/etc/vast_boot.d/39-unsloth-bootstrap-password.sh`)
  writes `password` to the studio's bootstrap-password file **only when no auth DB exists yet**,
  so the studio seeds a known first-login credential with forced rotation. It never clobbers an
  instance whose user has already set a password.
- **GPU:** the Dockerfile forces the studio's CUDA llama.cpp build (stub `nvidia-smi` + `nvcc`
  on PATH + `UNSLOTH_LLAMA_CUDA_ARCHS="80;86;89;90;120"` + the required CUDA dev headers) and
  **asserts `libggml-cuda.so` exists** after the build, failing the build otherwise.
- **Codify:** linter rule **L056** requires any image that runs `unsloth studio setup` to carry
  that CUDA-backend assertion, with a mutation test proving it bites.

## Binding conditions

- The baseline `imagegen lint --all` stays clean on committed images. L056 surfaced the same
  latent CPU-only build in **`aio-studio`** (which also ran `unsloth studio setup` with no CUDA
  force/assert and copied the result to the ill-named `/opt/llama-cpp-gpu`); it received the same
  fix in this change — build steering + the `libggml-cuda.so` assertion — plus the CPU-dispatch
  patch (`GGML_CPU_ALL_VARIANTS`) it was also missing. Both images must pass a real build.
- The GPU fix is verified by a real `docker build` + live-GPU check that `llama-server` offloads
  to the GPU (the linter is a shape gate, not a correctness gate — ADR 0001).

## Consequences

- GGUF inference/export in the studio runs on the GPU across Ampere→Blackwell (sm 80/86/89/90/120).
  A future GPU generation outside that list would offload to CPU until the arch list is extended —
  the build assertion still passes (the backend exists), so this is a coverage limit, not a silent
  CPU regression of the previous kind.
- The CUDA build is larger and slower than a CPU-only build; the multi-arch list widens it further.
- First login is `unsloth` / `password`, forced to change immediately; safe only because the UI is
  behind Caddy token auth. The weak default exists only pre-rotation, on the loopback side of the
  proxy. Documented in the image README and agent guide.
- A regression to a CPU-only llama.cpp is now un-shippable for any `unsloth studio setup` image
  (build assertion + L056).

## What would reverse this

- Upstream Unsloth Studio adds a supported "assume GPU at build time" flag or an auth-disable
  switch, making the stub / bootstrap-file mechanisms unnecessary.
- Upstream changes the bootstrap-password file mechanism or the llama.cpp build output layout,
  which would require re-verifying the boot hook and the `libggml-cuda.so` assertion path.
