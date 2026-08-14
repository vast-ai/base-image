# Invariants

Rules the image family actually relies on, **verified against the real files** (not
transcribed from docs). This is the spec a static linter should encode (see
[ADR 0001](adr/0001-image-scaffolding-tooling.md)). Where reality diverges from
`CONTRIBUTING.md` / `.github/AGENTS.md`, it is flagged — **reality wins here.**

Classes: **derivative** (`FROM vastai/base-image`), **pytorch-nested**
(`FROM vastai/pytorch`, under `derivatives/pytorch/derivatives/`), **external**
(`external/*`, multi-stage wrapping an upstream image).

> ⚠️ **Key finding for the linter:** the pattern is *less* uniform than the docs
> claim. Some headline "rules" cannot reach a clean baseline and **must not be
> gated** (see §3). Most importantly, **`external port == internal + 10000` is
> NOT a real invariant** — this directly affects ADR 0001's plan.

---

## 1. Hard invariants — safe to GATE (clean baseline reachable)

All verified clean across existing images.

- **3 LABELs.** Exactly three top-level `LABEL` lines, keys:
  `org.opencontainers.image.source`, `org.opencontainers.image.description`
  (value ends `suitable for Vast.ai.`), `maintainer="Vast.ai Inc <contact@vast.ai>"`.
- **`env-hash` trailer.** Final build instruction is `env-hash > /.env_hash`.
  *(External: it's the last `RUN`, before only `ENTRYPOINT`/`CMD` — not literally
  the last line.)*
- **`COPY ./ROOT /`** present exactly once. *(External also has
  `COPY --from=base_image_source /ROOT /`.)*
- **External graft block** (`external` only): the fixed env block → 4 `COPY`s
  (incl. `convert-non-vast-image.sh`) → the convert `RUN` → `COPY ./ROOT /` →
  `ENTRYPOINT ["/opt/instance-tools/bin/entrypoint.sh"]` + `CMD []`.
- **No surviving `--torch-backend auto`.** The only `auto` occurrences are `sed`
  rewrites that *replace* it with a concrete backend. Flag `auto` only when it's
  the *argument of an install command*.
- **`uv pip` only**, never bare `pip install` (pytorch-nested; spot-check externals).
- **Supervisor util source ORDER.** When utils are sourced, they appear as a
  *subsequence* of: `logging.sh → cleanup_generic.sh → environment.sh →
  exit_serverless.sh → exit_portal.sh`. Zero inversions exist. Match on path
  (`logging.sh` is sometimes called with an argument).
- **conf.d ↔ script ↔ program-name triple** (STRONGEST invariant, 60/60 clean):
  every `etc/supervisor/conf.d/*.conf` has `environment=PROC_NAME="%(program_name)s"`,
  `command=/opt/supervisor-scripts/<x>.sh`, and `[program:NAME]` where `NAME` ==
  conf filename stem. Bonus checkable: the `command=` target script exists on disk.
- **External `05-<name>-env.sh`** present setting `PORTAL_CONFIG` (externals + a
  few derivatives).
- **PORTAL_CONFIG anchors.** First entry always
  `localhost:1111:11111:/:Instance Portal`; a Jupyter entry present.
- **One `build-<name>.yml` per image.**
- **External build passes `--build-context base_image_source=.`** (all 5). This is
  what makes the otherwise-undeclared `base_image_source` stage resolve.

> Note: "one `build-<name>.yml` per image" is **not** universal — 6 images
> (fooocus, kohya_ss, oobabooga, swarmui, tensorflow, UnrealPixelStreaming) build
> via shared/other workflows. The linter treats workflow presence as a WARN (L030),
> not a gate.

## 2. Conditional invariants — GATE per-class, with exceptions encoded

- **FROM matches class** — EXCEPT: `aio-studio` builds on a custom
  `robatvastai/aio-studio:base-*` (not `vastai/pytorch`); `external/openwebui`'s
  upstream stage has **no `AS` alias** (others use `*_build`). The
  `vast_base_image`-first / upstream-second ORDER *is* hard across all externals.
- **torch-drift guard** (pytorch-nested) — present 16/17. EXEMPT: `aio-studio`
  (per-app venvs). ⚠️ The doc describes the **stale torch-only** form; reality
  checks the 4-package ecosystem (`torch|torchvision|torchaudio|torchcodec`).
- **strip upstream torch pins** (`sed` before install) — strong convention, not
  universal; gate only "if requirements installed, a torch-strip precedes it."
- **CI job set** — app images: `{preflight, build, merge-manifests, collect-tags,
  notify}` (allow `resolve-refs` variant; allow drop of `merge-manifests` for
  single-arch, e.g. voicebox). EXEMPT: `base-image`/`pytorch` (bespoke pipelines),
  `aio-studio-base` (2-job). ⚠️ Docs say 4-job and **omit `merge-manifests`** —
  wrong; 5-job dominates (16 workflows).
- **`MATRIX_ID` / built-tag** — `md5sum | cut -c1-8`; artifact `built-tag-*`.
  Strong convention in app workflows.

## 3. NOT invariants — DO NOT GATE (doc claims false against reality)

- ❌ **`external port == internal + 10000`.** Widely violated: Jupyter terminal
  (delta 0), `ollama` (reversed: `21434:11434`) vs `openwebui` (`11434:21434`) —
  the two even disagree on the *same* service; `aio-studio` pervasively (columns
  appear transposed). **This kills a binding assumption in ADR 0001.** At most a
  *soft warning* excluding known-exempt labels; possibly flag "likely transposed
  columns."
- ❌ **Fixed util SET** ("must source all 4"). The set varies legitimately;
  only the order is invariant. *(And there are 6 utils, not 4 —
  `exit_serverless.sh`, `pty.sh` are undocumented.)*
- ❌ **Uniform cron** `'0 0,12 * * *'`. Deliberately staggered across images.
- ❌ **Required `DEFAULT_MULTI_ARCH` / `RELEASE_AGE_THRESHOLD`.** First exists in
  one workflow; the latter is class-dependent (`COMMIT_AGE_THRESHOLD` for git-ref
  images) and absent in several. Only `DEFAULT_DOCKERHUB_REPO` is near-hard.
- ❌ **`set -euo pipefail` in every RUN.** Only the primary install RUN reliably
  has it.
- ❌ **The docs' specific `vast_boot.d` script list.** Numbering scheme is a rule
  (`^[0-9]{2}-.*\.sh$`); the exact list is mid-migration and already wrong in docs.

## 4. Real but NOT statically checkable (linter blind spots → need the build)

These are why ADR 0001 condition #1 holds: **static lint is the fast gate, the
real `docker build` + smoke test is the correctness gate.**

- torch ecosystem *actually* unchanged after install (guard presence ≠ success).
- CPU smoke-tests pass (presence checkable; success runtime-only).
- PORTAL_CONFIG ports match the port the app actually binds.
- Tag commit-hash-vs-version date suffix (depends on the runtime-resolved ref).
- `base_image_source` build-context *content*.
- single shared `/venv/main` assumption (false for aio-studio by design).
- **Container-aware CPU thread caps** (ADR 0014, amended by ADR 0025). On a host
  that oversubscribes (CPU `cpu.max`/`cfs_quota` ≪ visible `nproc`, e.g. ~46-core
  quota but 384 cpuset) the boot hook `12-cpu-thread-limits.sh` must cap
  `OMP_NUM_THREADS` &co to the entitlement so per-process thread pools don't exhaust
  `pids.max` (`pthread_create` EAGAIN). Whether the cgroup is read correctly, the
  arithmetic, the oversubscription trigger, and the "leave user overrides alone" rule
  are all **runtime facts** — a static check could only assert the file exists. Gate:
  the on-box test `tests/base/NN-cpu-thread-limits.sh` + a harness mutation test
  (delete the cap write → test fires), per ADR 0014. Deliberately **not** a linter
  `RULES` code.
  - The Hugging Face half of the cap is `TOKIO_WORKER_THREADS`, **not**
    `HF_HUB_DISABLE_XET` (ADR 0025). `hf-xet` is a hard dependency of
    `huggingface_hub` since 0.34.0, so Xet is the default download path; disabling
    it removed that path from the hosts least able to spare bandwidth. Measured:
    bounding the Tokio pool holds one `hf download` at ~28 threads across 2, 8 and
    16 visible cores, where the unbounded pool scales with the core count. Two
    things this depends on and cannot detect: hf_xet continuing to use Tokio's
    default runtime, and the variable reaching every Tokio program in the instance
    (accepted). **A variable removed from the managed set must be `unset`, not
    merely stopped being written** — `10-prep-env.sh` sources `/etc/environment`
    into the boot shell before the hook runs, so a stale value outlives its own
    removal and reaches supervisord. Covered by the `migrate-unset-xet` assertion.

**Feasible future cross-file static check:** every `exit_portal.sh "<Label>"` in
an image should have a matching `:<Label>` entry in that image's `PORTAL_CONFIG`
(catches typos). Not currently enforced.

## 5. Docs needing correction before they can be trusted

`CONTRIBUTING.md` / `.github/AGENTS.md` are stale on: the torch guard form (§2),
the boot-sequence list (§3), the 4-job pipeline + missing `merge-manifests` (§2),
the undeclared `base_image_source` stage (§1), the missing utils
`exit_serverless.sh`/`pty.sh` (§1), the port +10000 claim (§3), and uniform cron (§3).

## 6. Invariants codified from review

The no-baked-weights policy is now **gated (L053)**, the public-ADR-secret policy is
**gated (L060)**, and the no-internal-ticket-id policy is **gated (L061)**. The copyleft
policy remains review-only for now (statically checkable; should become a lint rule with
the ADR-binding + `RULES`-catalog pattern of §1).

### No baked model weights — **GATED (L053)**

Model weights must NOT be downloaded or baked into the image; they arrive at
runtime via provisioning or the app's own on-start download (see the runtime
conventions in §7). Rationale: keeps images small and rebuildable, and — because
the *tenant* triggers the download — the weight licence (non-commercial / gated /
territory-restricted) is the tenant's to honour, not something the image distributes.

**L053** enforces it, instruction-aware (operates on the parsed RUN code, so a
*commented* example download does not fire). Detected inside a Dockerfile `RUN`:
`hf download` / `huggingface-cli download` / `hf_hub_download(` / `snapshot_download(`,
and a `wget`/`curl` of `*.safetensors|*.gguf|*.ckpt|*.pth|*.onnx`. `*.bin` is
deliberately **excluded** (too many non-model `.bin` files → false positives). Scope:
model weights only — not small non-model assets (tokenizer/config files, a UI's
bundled icons).

**Exemption (dated, tracked):** `comfyui` bakes one small default SD-1.5 checkpoint
for the out-of-box / QA first-run (a §6-style deviation, tracked for migration to
runtime provisioning — see the `EXCEPTIONS` entry). It is the only current exemption;
new images must provision, not bake.

### App base FROM is a concrete pin — **GATED (L005)**

A `pytorch-nested` / `derivative` image must pin a **concrete** base `FROM` — a dated tag or a
digest — never `latest` and never untagged. A floating base lets a rebuild silently land on a
base image this app was never tested against (ADR 0013). `base-image` / `pytorch` themselves may
float; app images may not. **L005** resolves the base ref via ARG defaults (like L004) and fires
on `latest`/untagged; a digest (`@sha256:…`) passes, and a base injected via a defaultless
build-arg (the CI multi-cuda pattern) is skipped — its concrete tag lives in the CI matrix. The
scaffold's `CHANGEME` is L040's surface, not L005's. Pins are produced/refreshed by the DockerHub
resolver (`imagegen resolve-base` / `new --resolve-base` / `bump`, ADR 0013).

### No credential-shaped secret in a public ADR — **GATED (L060)**

`base-image` is public, and `docs/adr/**` is world-readable. An ADR records the
decision + rationale + rejected alternatives; a credential, an exploit-map, or
account/business specifics do not belong in it — they live in the linked Jira issue
(ADR 0012). **L060** (`check_adr_secrets`, a repo-level check — not per-image) scans
`docs/adr/*.md` for credential *shapes*: private-key blocks, AWS access-key ids,
GitHub/Slack tokens, JWTs, and a secret-named field assigned a literal high-entropy
value. Prose mentions of "token"/"key"/"secret" and env-var references (e.g.
`VAST_API_KEY`) do **not** fire — only a credential-shaped value does. The
*exploit-map* half of the ADR-0012 guardrail (which token, which transport, which
soft endpoint) is not machine-detectable and stays review-enforced. Precedent for
the excision: ADR 0005 condition 8, moved to the internal issue.

### No internal tracker ticket id in a public file — **GATED (L061)**

`base-image` is public. An internal Jira ticket id (a Vast-internal project key such
as `CON-`/`HOST-`/`CLN-`-####) must not appear in any repo file — it leaks the
tracker's structure and is a dangling reference to a private system for external
readers. The linkage runs one way: the internal issue references the public
ADR/commit, never the reverse. **L061** (`check_internal_ticket_ids`, repo-level)
scans the working tree (text files + Dockerfiles, incl. the first-party `external/`
wrappers) for an explicit internal-prefix set — extend `_INTERNAL_TRACKERS` as new
internal projects appear. Precedent: the CON-/HOST-/CLN- references scrubbed from
docs, workflows, templates, and tooling when this rule landed (ADR 0012).

### A shipped instance test is executable — **GATED (L065)**

`runner.sh` discovers tests with `find … -name '*.sh' -executable`, and the
Dockerfile ships the overlay with a bare `COPY ./ROOT/ /`, which preserves mode.
So a test committed `0644` is **not collected** — and unlike a skip or a missing
required test, it produces no output whatsoever: not a SKIP line, not a "missing
from this image", nothing. The only way to notice is to compare a directory
listing against the collected list, which nobody does.

`base/11-instance-metadata.sh` and `base/12-provisioning.sh` shipped `0644` from
their introducing commit and had therefore **never run once**. Confirmed against
production QA evidence, which records 23 tests where the directory holds 25. Two
consequences ran unnoticed for that whole period: `lib.sh`'s `instance_field()`
reads a metadata file only test 11 writes, so it could only ever have returned
empty *while signalling success* (nothing broke solely because it has no callers
yet — `README.md` advertises it to derivative tests); and `runner.sh`'s
"no blind provisioning wait —
12-provisioning.sh handles monitoring" was false, so tests that document
themselves as running after provisioning were racing it. **L065**
(`check_instance_tests_executable`) is L051's shape applied to
`ROOT/opt/instance-tools/tests/**` and the derivative/external overlays;
`lib.sh` is exempt because it is sourced, not executed.

### A required test must be able to fail — **GATED (L059)**

L057 makes a gating QA template *name* the tests that must have PASSED. This
closes the next hole down: a named test containing no failure path at all
reports `passed` on every box, so requiring it asserts nothing beyond the script
reaching its `test_pass`, and the gate reads as coverage while certifying
nothing. Not hypothetical — `base/62-gpu-libraries.sh` was in base-qa's
require-pass set with every branch an `echo`/`WARN` and zero `fail_later` calls,
so the third of three gating tests asserted exactly `has_gpu`, which the other
two already assert. **L059** (`check_required_tests_can_fail`) resolves each name
in `INSTANCE_TEST_REQUIRE_PASS` against the base overlay and each derivative
tests dir, and requires at least one real `test_fail`/`fail_later` **call** — a
mention in a comment does not count, which is exactly how the defect hid.
Deliberately weak: it asserts a failure path *exists*, not that it is a good one.
Whether a test can fail at all is decidable by reading the file; whether it fails
for the right reasons is a review question.

The rule that makes such a test assertable without false-reddening images that
legitimately ship less: **absent is fine, installed-but-broken is a failure.**
Whether a library is shipped is a property of the image (ours); whether the
hardware exists is a property of the host (not ours). `ldconfig -p` distinguishes
them — `ctypes.CDLL` alone cannot, it fails identically for both.

### A deferred failure is reported before every non-failing exit — **GATED (L062)**

`fail_later` (and `http_check`, which calls it internally) only **records** a
failure; `report_failures` is what turns the record into a failing test. Reach
`test_pass` or `test_skip` with one pending and the runner exits 0 (or 77) with a
`FAIL: …` line in the log and a green verdict — skip-as-pass wearing a different
hat, on the gate that exists to close exactly that. Presence anywhere in the file
is not enough, and neither is textual order: a `report_failures` that runs only
inside a conditional does not clear a failure recorded outside it. **L062**
(`check_fail_later_is_reported`) walks each shipped test under
`ROOT/opt/instance-tools/tests/**` and `derivatives/*/ROOT/…/tests/**`, tracking
branch structure — it merges `if`/`else` arms by OR (an `elif` chain with no
`else` still merges the fall-through arm), restarts each alternative from the
state at the branch, applies a helper function's effect at its call site in body
order, and treats only an unguarded `report_failures` as a clear. Found the
honest way, twice, while adding the CUDA-libpath check to `base/60-gpu-cuda`:
once for a missing report, once for an early exit that discarded it. **Two known
blind spots**, both on the conservative side: a `fail_later` inside a subshell or
pipeline loses its record at runtime and nothing static can see it; and `case`
arms are detected as command positions but not merged as alternatives.

### No script parses nvidia-smi's table for the CUDA version — **GATED (L063)**

Driver branch 610 renamed nvidia-smi's `CUDA Version:` field to
`CUDA UMD Version:`, so every scrape of it returned empty on every 610 host at
once — deterministic and fleet-wide, not a flaky box. `cuDriverGetVersion` is a
stable C ABI that returns the same number from the driver itself. **L063**
(`check_no_nvidia_smi_text_parse`) scans every script that ships inside an image
— `ROOT/`, `portal-aio/`, derivative and external overlays, including the
extensionless tools in `ROOT/opt/instance-tools/bin` — and exempts only
`/opt/instance-tools/bin/cuda-driver-version`, which owns the one sanctioned
text fallback. Comments explaining the history are fine; code is not.

### One native-libcuda resolver, not one per caller — **GATED (L064)**

`cuDriverGetVersion` reports whichever `libcuda.so.1` the loader resolved, so any
code deciding *whether forward compat is needed* must exclude a forward-compat
library — otherwise a stop/start reads the compat version, concludes compat is
unnecessary, and the instance comes back with a newer toolkit on an older driver.
Doing that with `LD_LIBRARY_PATH=<dir> cuda-driver-version` fails **open**:
`LD_LIBRARY_PATH` is a search hint, so a directory with no loadable
`libcuda.so.1` sends the loader on to the ld.so cache — to the compat library,
silently. `cuda-driver-version --native` instead dlopens an absolute path (a name
containing `/` performs no search) and then verifies from `/proc/self/maps` which
file was actually mapped, refusing rather than guessing. **L064**
(`check_no_open_coded_native_libcuda`) forbids re-deriving that anywhere else;
probing for `libcuda.so.*` to ask "does this toolkit ship compat libs" is a
different question and stays legal. The rule exists because the same six lines
lived in both `05-configure-cuda.sh` and `base/60-gpu-cuda`, so the test agreed
with the boot script instead of checking it — and only a manual review caught it.
Behaviour is pinned in both directions by
`tools/imagegen/tests/test_cuda_driver_version.py`.

### One TLS cert-usability predicate, not one per caller — **GATED (L066)**

"Can Caddy serve TLS with this pair?" is asked in three places — the boot script
that decides whether to regenerate (`55-tls-cert-gen.sh`), the portal component
that decides whether TLS comes up at all
(`portal-aio/caddy_manager/caddy_config_manager.py`), and the test that asserts
on the result (`base/27-caddy-tls.sh`) — and each had grown a different answer.
Two used `openssl rsa -in KEY -check`, the RSA-*only* entry point: it cannot load
an EC key, so a correct operator-supplied EC pair made the portal give up on TLS
after `MAX_RETRIES` and hard-failed the gate. Neither compared the certificate to
the key, so a mismatched pair — what a half-finished regeneration leaves —
passed both. The third hashed each side's public key before comparing, which
inverts the risk: `sha256sum` of empty input is `e3b0c442…` on *both* sides, so
two failed extractions compare equal and `[[ -n "$c" ]]` guards the digest
rather than the key. That fail-open needs **both** sides to fail: a certificate
whose SPKI **algorithm OID** openssl cannot decode (parses, passes `-checkend`,
yields no public key) supplies the cert side, and an unreadable key the other —
an unknown-OID cert against a *good* key still fails **closed**. (This file
previously called the whole thing unreachable, on the strength of a
corrupted-*modulus* fixture that could not have falsified it — any integer is a
valid modulus. Recorded because the bad inference, not the bug, is the reusable
lesson.) All three call
A helper that is PRESENT but broken is treated as MISSING. `-x` asks only whether
a file exists; a truncated or half-written `cert-usable` passes that and then
fails every predicate it is asked, so the regeneration guard is true on every
boot and the instance churns a fresh keypair and a console CSR forever — the
unbounded churn `55-tls-cert-gen.sh` exists to end, re-entered through the one
door a presence check leaves open (measured before the fix: five boots, five
keys). The gate is now the helper's own contract on inputs that cannot exist:
two absent paths must yield exit 1 with a `cert-usable:` reason.

`/opt/instance-tools/bin/cert-usable <crt> <key>`, which compares PEM SPKI
directly and reports **0 usable / 3 matched-but-expired / 1 unusable** — expiry
is a separate code because it means *regenerate* at boot and *serve anyway* at
the portal, whose only fallback is plaintext on the same public port; **3, not
2**, so a syntactically broken helper's own bash exit 2 cannot be misread as
"expired, serve anyway" (a fail-open at the TLS gate). **L066**
(`check_one_cert_usability_predicate`) blocks the spellings that have already
shipped wrong, across line breaks and in Python argv lists; it is not a proof
that no fourth implementation exists, and generic openssl use — `rand`,
`s_client`, `-checkend`, fingerprints, conversions — stays legal. Behaviour is
pinned by `tools/imagegen/tests/test_cert_usable.py` against real
RSA/EC/mismatched/expired/unreadable/unknown-algorithm fixtures, and the boot
script's across-boot convergence by `test_tls_cert_gen.py` (ADR 0026).

**Caveat, true at the time of writing:** the portal caller is fixed *in the
repo* only. `portal-aio` is also published as a release tarball, and
`portal-aio/VERSION` has not been bumped — so the published `v3.1.4` tarball
still carries the `openssl rsa -check` form this rule bans, and the name
`v3.1.4` now denotes two different payloads. New images carry the fixed copy
(`COPY ./portal-aio`) and first-boot skips the download on version equality, so
nothing regresses; but no *running* instance gets the fix until a portal release
is cut, and a customer pinning `PORTAL_VERSION` to `v3.1.4` on a new image would
overwrite the fixed copy with the banned one. Bumping `VERSION` is an
unrevertable fleet push (`release-portal.yml` monotonic gate) and is deliberately
a separate decision — see ADR 0026.

### A test-invoked provisioner run does not touch the real one's state

`base/13-provisioner-selftest.sh` executes the shipped provisioner at boot stage
**70**; the customer's own provisioning runs at stage **75**. The provisioner
treats the environment as authoritative over the manifest through two separate
mechanisms (`_apply_env_overrides`, 8 × `PROVISIONER_*`; `apply_env_conventions`,
6 × `PROVISIONING_*`) plus `PROVISIONING_SCRIPT`, `HF_TOKEN`, `CIVITAI_TOKEN`,
`WORKSPACE` and the API credentials — and `load_manifest` expands `$VARS` in the
manifest text, so the reachable surface is "the environment", not a list. A
self-test that unsets the variables its author could name therefore runs the
customer's `post_commands` as root five stages early, writes their provisioning
log, validates their tokens against huggingface.co, and can reach
`vastai destroy instance`. The invariant is the **direction**: `env -i` plus a
named allowlist with **pinned values** — `$PATH` forwarded verbatim would leave
`wget`/`git`/`apt-get`/`vastai` resolvable from a template-set directory, so a
name-only allowlist is just a shorter deny-list — plus `PROVISIONER_STATE_DIR`
pointed at a temp dir so stage hashes cannot mark a stage complete that the real
run has not performed. It covers the test's own **fixture** too: `curl` honours
`http_proxy` and does not auto-bypass loopback, so an unscrubbed readiness probe
let a customer-set proxy skip the download section into a pass.

Not gated by the linter — the property is about side effects, not syntax — but
asserted by `tools/imagegen/tests/test_provisioner_selftest.py`, which runs the
real test under a hostile environment and then checks for the files, log lines,
state entries and outbound connections that must not exist. **Every one of those
eight canaries carries a positive control that evaluates the canary's OWN
predicate** — not a file written directly, which proves only that the directory
is writable, never that the provisioner would write it. This matters because
three of the first seven canaries were structurally unable to fire and reported
`ok` regardless: an early-aborting `PROVISIONING_APT` shadowed `post_commands`;
an unresolvable URL; and a *relative download dest* that was believed to resolve
under `$WORKSPACE` but does not — a download dest lands in the provisioner's CWD,
and `manifest.py` reads `WORKSPACE` only in `_repo_dest_from_url`, the **git-repo**
default-dest helper. So the workspace canary is now driven by a
`PROVISIONING_GIT_REPOS` entry with no dest (the one input that does consult
`$WORKSPACE`), the download canary by an absolute dest, and the `_PATH` pin by a
hostile-directory shim prepended to the harness's PATH. A canary without a proof
that it can fire is decoration.

Two canaries were rebuilt again after a later review: the workspace one fired
only when `WORKSPACE` **and** a git-repo variable both leaked, so leaking
`PROVISIONING_GIT_REPOS` alone cloned an attacker-named repo into the customer's
`/workspace` as root and still reported `ok`. It is now paired with a `git` shim
on the pinned PATH that fires on the ACT of cloning, wherever it lands — a canary
must not depend on the variable it is guarding.

`PROVISIONER_STATE_DIR` is validated rather than trusted: `clear_all_state()`
does `shutil.rmtree` and is reachable from `--force`, so it refuses relative and
system paths (a blocklist of exact roots, **not** a containment rule — the
ownership marker is the real defence). The marker is planted **only in a
directory the provisioner itself created** (`makedirs(exist_ok=False)`), so a
foreign directory it was merely pointed at is never adopted and the next
`--force` never `rmtree`s it; a directory holding only stage-hash files is
treated as ours for migration (already-deployed instances predate the marker).
The marker is written `O_CREAT|O_EXCL|O_NOFOLLOW` and verified with `os.lstat`,
so a planted symlink cannot make root write outside the state dir or forge
ownership; hash files are read and written `O_NOFOLLOW`. `clear_all_state()`
returns whether it cleared, and `--force` **aborts** on a refusal rather than
silently skipping every stage against stale hashes.

### Copyleft licence compliance (proposed)

An image that ships GPL-/AGPL-licensed code must (a) convey the licence **text** in
the image (a LICENSE at a known path, vendored to `/licenses/` if the package does
not carry one), and (b) when the Dockerfile patches the copyleft upstream (a
`sed -i` / `patch` / `git apply` against a cloned or installed copyleft source),
carry a **modification notice** in `LICENSES.md` — GPL §5 / AGPL §4 + §5a.
Corresponding source is this public repo; AGPL §13 (network source offer to users)
stays the tenant-operator's obligation, not the image's.

Because "is this upstream copyleft?" is not reliably inferable, the check is
**declarative**: `LICENSES.md` declares the licence, and the rule verifies that for
each copyleft entry (a) the stated in-image licence path resolves and (b) a
`Modifications:` note exists whenever the Dockerfile patches that app's tree.
Applies to GPL-3.0 too (e.g. ComfyUI), not only AGPL. Reference implementation of
the obligations themselves: the `fix/agpl-license-compliance` change (unsloth-studio,
aio-studio, a1111, sd-forge, oobabooga).

### Portal "not ready" interstitial is CDN-safe (200 for Cloudflare only) — **enforced by portal-aio tests (ADR 0017)**

When a proxied backing service has not started yet, Caddy's `handle_errors 502 503
504` serves the `502.html` loading page. It **must** be served as **200 only for
requests that arrive over a Cloudflare tunnel** (they carry `Cf-Ray`) and keep the
real **5xx for every other path** (direct access, Vast's proxy, uptime probes),
because a CDN tunnel replaces an origin **5xx body** with its own "Host is down"
page — so a raw 502 loader never reaches a tunnelled user, and the poll, hitting
the same tunnel, would loop on Cloudflare's page forever. Both paths carry an
`X-Portal-Placeholder` marker (plus `Cache-Control: no-store`); the proxy blocks
strip any upstream copy so only Caddy sets it. `502.html`'s poll **must** reload
only when the marker is **absent AND status < 500**, never on the 502 status code.
Because request matchers do not discriminate inside `handle_errors` on the shipped
Caddy build, the Cloudflare decision is made at site scope via a request var read
back as the `status` placeholder. Enforced by
`portal-aio/tests/test_caddy_config_manager.py` (contract predicate + generator↔poll
round-trip + a regex that pins the poll's reload condition) and a `caddy validate`
step in `.github/workflows/portal-aio-tests.yml` (the portal-aio analogue of the
imagegen linter — which only lints image definitions, not the portal Caddy
generator; hence not an `L0xx` code). **Scope:** direct-bind apps
(`external_port == internal_port`, e.g. launch-mode Jupyter) are not fronted by
Caddy and are not covered — cloudflared hits connection-refused there (error 1033),
a different failure with no origin 5xx to convert.

### No `-auto` tag moves without same-run, digest-matched QA evidence — **enforced by executed tests (ADR 0019)**

`cuda-X.Y.Z-auto` is what Vast's `@vastai-automatic-tag` backend resolves for live
customer templates, so it is the highest-blast-radius write in this repo. The
invariant:

> An auto tag may only move to a digest that PASSED live-GPU QA in this run, on
> that exact digest. Anything else HOLDS the tag at its current digest.

Not statically checkable — it is a property of ~420 lines of bash inside
`promote-base-image.yml`, and a linter cannot see a working gate versus a copy of
one. It has been disarmed three times, each time with the whole suite green:

| how it was disarmed | why the tests missed it |
|---|---|
| the hold check was written into `dry-run`, not `promote` | the test grepped the whole file and found the stray copy |
| the `continue` was deleted from the hold branch | the test asserted the `if` line exists, not that it skips |
| five assignments were deleted from the `dry-run` loop | nothing asserted anything about `dry-run` |

There is **no bypass**: the `SKIP_QA` dispatch input added on 2026-08-05 was
removed on 2026-08-07 (ADR 0019 cond 3, amended). Its untested-ness stuck to the
run rather than the digest, so re-dispatching the same staging date afterwards
rendered as a clean gated promotion. Holding is affordable because a held auto tag
does not block shipping — the dated prod tags promote regardless of the decision,
so only the customer-facing pointer waits for evidence. Moving a pointer without
CI evidence is the separately-approved `Move Base Auto Tag` workflow.

So it is enforced by **executing the workflow's shell**:
`tools/template_manager/tests/wfexec.py` extracts a step's `run:` script, runs it
under bash with a stub `crane` over a fake registry, and the tests assert the
resulting digest of each auto tag. `test_promote_behaviour.py`,
`test_preflight_behaviour.py` and `test_rollback_behaviour.py` each carry the
mutation that the string-matching tests survive.

A third rule follows from a defect found reviewing this: **every artifact a
promotion copies is read from the registry exactly once, at plan time, and copied
by digest.** Staging tags are mutable — a same-day rebuild rewrites them — so a
tag ref read at write time can be different bits from the ones that were resolved,
tested and approved. Previously only the default-python image was pinned; the other
four pythons and the mini variants were copied by tag, justified by "the drift
check already aborted if the dated source moved". That was false: the drift check
only re-resolves the *default-python* tag, using it as a proxy for "the whole
config was rebuilt". The proxy holds for an all-or-nothing rebuild and breaks for a
FILTERed or partially re-run one. A missing pin now fails the step rather than
falling back to the tag.

Two further rules, both themselves tested:

- **A dispatch input must reach a script via `env:`, never `${{ }}`.** Inputs are
  free text; `${{ }}` splices them in before the shell parses the line, so a value
  containing `$(...)` executes — and in `promote` that is inside the job holding
  production credentials. `wfexec.step_script` refuses any non-secret expression
  left in a `run:` block, so this cannot regress silently.
- **The required-test list must be identical in all four places** it appears
  (the QA template, the `qa` job input, `qa-summary`'s `REQUIRE_TESTS`, and the
  linter). `qa-summary`'s copy is the actual flip/hold arbiter; emptying it makes
  a self-skipped GPU suite classify as a pass.

## 7. Application runtime conventions (how apps are launched & fed models)

These govern how an application's supervisor script launches the app and how a model
reaches it. Verified across the LLM-serving fleet (vllm, sglang, ollama, llama-cpp,
oobabooga). The `new-image` skill + generator encode them.

- **`<APP>_ARGS` — runtime args from the template, not baked into code.** The primary
  app-server launch reads `${<NAME_UPPER>_ARGS:-<sensible defaults incl. the explicit
  loopback bind>}`, so a template or user tunes runtime flags without editing the image
  (`VLLM_ARGS`, `SGLANG_ARGS`, `LLAMA_ARGS`, `OOBABOOGA_ARGS`, …). The default **must**
  carry `--host 127.0.0.1 --port <port>` (Caddy is the sole public edge; never `0.0.0.0`).
  **Not statically gated** — it is *not* universal by design: config-file apps (invokeai,
  fluxgym, … pin via a config file, not ARGS) and infra/helper services (desktop stack,
  `model-ui`, `api-wrapper`, `ray`) legitimately have no `<APP>_ARGS`, and there is no
  reliable static way to distinguish "primary launch that should have ARGS" from those. It
  is enforced by the skill + scaffold, not the linter.

- **`<APP>_MODEL` + provision the model at runtime (never bake — see §6 / L053).** A
  model-serving app names a default model via an `<APP>_MODEL` env set in
  `templates/default/template.yml` (`VLLM_MODEL: Qwen/Qwen3-8B-FP8`, `SGLANG_MODEL`,
  `OLLAMA_MODEL`, `LLAMA_MODEL`). The supervisor waits for provisioning
  (`while [ -f /.provisioning ]; do …`), then the app downloads that model to
  `$WORKSPACE/<name>/models` (or its own `--download-dir`) at runtime and serves it,
  **refusing/skipping if the model env is unset** (`vllm.sh`: *"Refusing to start —
  VLLM_MODEL not set"*). Heavier/multi-step setup lives in a `provisioning_scripts/<name>.sh`
  (run via `PROVISIONING_SCRIPT`). So "launch the template" yields a working model, and no
  weights ship in the image.
- **VRAM floor sized to the model — `gpu_ram` / `gpu_total_ram` (validity GATED, L054).** A
  template's `extra_filters` should carry a VRAM floor sized to the model the image actually
  runs, so box selection rents a GPU that can hold it — `gpu_ram: {gte: <MB>}` (must fit a
  single GPU) or `gpu_total_ram: {gte: <MB>}` (summed across GPUs). **Boundary:** a *single*
  fixed/provisioned-model image SHOULD set it; a **multi-model host** (the user picks the model
  via `<APP>_MODEL`) leaves it **unset** on the launch template, and the live-GPU gate supplies
  a floor at rent time (`imagegen qa --min-vram <GB>` → injected `gpu_total_ram`, ADR 0010) so
  the *test* model fits without over-constraining the launch template. Presence + the value are
  judgment (the linter can't know a model's footprint); **L054 gates only FORMAT** — a VRAM
  filter, if set, must use a valid key with a numeric floor (a misspelled key or a key-only
  floor lints falsely clean but selects nothing).
- **External images set `TCLLIBPATH` (GATED, L055).** An external image `FROM`s the upstream's
  prebuilt image, so it does NOT inherit the base image's ENV. It must set
  `ENV TCLLIBPATH=/usr/lib/tcltk/default` itself, or the base pty helper's `unbuffer` (Tcl/Expect)
  fails early in boot and the launch cascade dies — the LLaMA-Factory scaffold shipped without it
  and died on a live box (no supervisord, `can't find package Expect`). The `/opt/sys-venv/shim`
  PATH entry most externals also carry is **NOT gated**: `vast_boot.d/10-prep-env.sh` adds it at
  runtime, so `vllm-omni` omits it from the Dockerfile and works fine. Root cause was a generator
  bug (`_DF_EXTERNAL` set neither); fixed + gated.

- **A source-built Unsloth Studio llama.cpp asserts its CUDA backend (GATED, L056).** Unsloth
  Studio's `setup.sh` gates `-DGGML_CUDA=ON` on a **runtime GPU probe** (`nvidia-smi -L`, then
  `/proc/driver/nvidia/gpus`) that is absent inside `docker build`, so it silently builds a
  CPU-only llama.cpp (only `libggml-cpu-*.so`, no `libggml-cuda.so`) and every runtime inference
  offloads to CPU. Any image running `unsloth studio setup` must force the CUDA build (a build-only
  stub `nvidia-smi` + `nvcc` on PATH + `UNSLOTH_LLAMA_CUDA_ARCHS`) **and** carry a post-build
  `test -f …/libggml-cuda.so` assertion so the CPU-only regression fails the build instead of
  shipping. **L056** gates the assertion. Both `unsloth-studio` and `aio-studio` are fixed (ADR 0016).
