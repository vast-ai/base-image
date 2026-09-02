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

### The engines do not implement the same OpenAI contract — DECLARED (ADR 0031 6a)

Measured on the first contract run of each engine, not inferred from documentation.
Every one of these is upstream and unfixable from this repo, so each is a declared
deviation in that image's `ENGINE["deviations"]` block: reported on every run, not
blocking, and **automatically a violation again the day it stops reproducing**, so
the entry cannot outlive the defect.

| deviation | vLLM | SGLang | llama.cpp |
|---|---|---|---|
| unknown `model` refused with 4xx | yes | **no — 200** | **no — 200** |
| malformed JSON refused with 4xx | yes | yes | **no — 500** |
| `max_tokens` > context refused with 4xx | yes | yes | **no — 200, clamped** |

**vLLM is the only one that implements all three.** That is worth knowing when
choosing an engine for a customer who integrates against error codes.

Each declaration must state UPSTREAM and BOUNDED, and two of the three are properly
bounded: the unknown-model case by `identity` (which asserts `/v1/models` advertises
exactly the requested name, so a client can always tell what it is talking to), and
the context-overflow case by `token-arithmetic` and `finish-reason` (which report
what was actually produced, so a caller can see it got fewer tokens than it asked
for).

**llama.cpp's malformed-body deviation is the one with a WEAK bound, recorded here
because it was accepted knowingly rather than because it is harmless.** Nothing else
in the suite covers it. Its only mitigation is that it fails LOUDLY — the caller
receives a 5xx, never a wrong answer — so a malformed request is never silently
mis-served. It is the single place in the contract suite where an assertion is
switched off without another assertion standing behind it, and it should be the first
thing revisited if llama.cpp's error handling changes.

### Ray's public listeners are an OPEN defect, not a declared exception

Recorded here because the tempting resolutions are both worse than the finding, and
because an undocumented open defect gets rediscovered as a surprise.

`base/28` reports six to seven public listeners on the vLLM and vLLM-omni images —
raylet (2), the dashboard agent (2), and two agent processes — on ports that change
every boot.

**The range is known, and it is not the kernel's.** Ray does not `bind(0)` and let
the OS assign from `ip_local_port_range`; it draws its own numbers
(`ray/_private/services.py`):

```python
def new_port(lower_bound=10000, upper_bound=65535, denylist=None):
    port = random.randint(lower_bound, upper_bound)
```

So the window is **10000-65535**, 55,536 ports, which matches every port observed
live (33365, 39753, 42437, 44217, 44227, 46163, 53265). It is far too wide to
declare: an allowlist entry covering it would pass essentially any high port, which
is not a declaration but a disabled check.

**A second consequence, and the more serious one.** That helper checks its choice
against an in-process `denylist` only — the ports Ray has already picked this run.
It does **not** test whether the port is free on the machine. (The bind-test in
`services.py` around line 1235 is specific to the dashboard port and does not cover
this path.) So Ray can select a port another service holds, or is about to hold.

Our own fixed ports inside that window include `10199` (the instance-test harness),
`18000` (vLLM's internal listener), `11111`, `16006`, `18080`, `18384` and `28265`.
Boot order makes the exposure concrete: `ray.sh` starts Ray, Ray draws roughly six
ports, and only then does `vllm.sh` start vLLM on 18000. A collision is around one
boot in a thousand and presents as an unreproducible startup failure on an image
that is otherwise fine — rare, loud rather than silent, and very hard to diagnose
without this note.

Two attempts to remove the listeners failed, both measured on live cells rather than
reasoned about:

- **Bind loopback.** Refused upstream. `--node-ip-address 127.0.0.1` is routed
  through `services.resolve_ip_for_localhost()`, documented as "Convert to a
  remotely reachable IP if the address is localhost or 127.0.0.1". The cell logged
  `Local node IP: 172.17.0.2` with the flag passed.
- **Pin the ports.** `--node-manager-port`, `--object-manager-port`,
  `--dashboard-agent-*`, `--metrics-export-port`, `--runtime-env-agent-port` and
  `--min/--max-worker-port` are all accepted — Ray rejects unknown options, and
  `--dashboard-port` in the same string took effect — and the observed ports were
  still ephemeral. Those services open more listeners than any flag governs.

**Only `6379` (GCS) is declared**, as class `internal`, because it is the only one
provably pinned. The rest stay VIOLATIONS and stay visible.

**Two fixes that must not be adopted**, both of which make the report go away
without changing what a customer runs:

1. **Do not stop starting Ray on single-GPU deployments.** It would clean the QA
   cells while leaving the exposure intact for anyone running tensor parallelism —
   hiding the defect from ourselves, which is worse than the defect.
2. **Do not widen the allowlist to cover the ephemeral range.** That declares 120
   ports nothing binds and states a pin that was measured not to hold.

The consequence is accepted: `EXPOSURE_ENFORCE` cannot go true on these images until
this is resolved upstream or a real control is found. A gate that cannot be enforced
yet is an honest state; a gate enforced over a declaration nobody established is not.

### A gating template requires the image's own suite — **GATED (L057, L072)**

**L057** was base-only from ADR 0019 until 2026-08-21, on the stated grounds that
widening it had to re-validate two live, currently-passing gates rather than turn
them red from a linter. ADR 0031 is that re-validation, and the scope is now the
gate *wiring* rather than the image class: a template is gating iff some workflow
hands it to `qa-gate.yml` as `template_dir:`. That is read from the workflows, not
from the template's name, because the generator points the gate at
`templates/default` (ADR 0010/0011 — the template users launch IS the template QA
boots), so a `*-qa` name match would exempt precisely the images with no separate
QA template. A `${{ }}` expression is skipped rather than guessed at.

**L072** closes the level above it. The GPU trio is inherited from base and is the
same three names on every image, so a template that names only the trio has a gate
certifying that the *rented box* has a working GPU while asserting nothing about
the app the image exists to ship. Measured on the case that prompted ADR 0031:
`vllm.d/10-vllm-serving.sh` opens `[[ -n "${VLLM_MODEL:-}" ]] || test_skip`, so
dropping one env var from the template deletes every vLLM assertion at once and
the image promotes green — and `build-vllm.yml` passed no `require_tests` either,
so *neither* of the two enforcement layers would have caught it. Scoped to images
that ship an own suite: a suite dir is a subdirectory of the image's
`ROOT/opt/instance-tools/tests/` holding at least one `NN-*.sh`, which excludes
`exposure-allowlist/` (`.conf` data) without a name blocklist. An image whose only
tests are base's inherits base's coverage and owes nothing extra.

### A required test must be able to fail — **GATED (L059)**

L057 makes a gating QA template *name* the tests that must have PASSED. This
closes the next hole down: a named test containing no failure path at all
reports `passed` on every box, so requiring it asserts nothing beyond the script
reaching its `test_pass`, and the gate reads as coverage while certifying
nothing. Not hypothetical — `base/62-gpu-libraries.sh` was in base-qa's
require-pass set with every branch an `echo`/`WARN` and zero `fail_later` calls,
so the third of three gating tests asserted exactly `has_gpu`, which the other
two already assert. **L059** (`check_required_tests_can_fail`) resolves each name
in `INSTANCE_TEST_REQUIRE_PASS` against the base overlay and every derivative
**and external** tests dir, and requires at least one real
`test_fail`/`fail_later` **call** — a
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

### A base/ test must hold on a BARE base image — **GATED (L067)**

`tests/base/` runs on every image, so every assertion in it has to be true of the
base image alone. `86-serverless-pyworker` asserted a running `pyworker` and a
listener on `:3000`. Base ships the pyworker unit, but what binds `:3000` is the
inference engine behind it, which base does not have — so the test was
structurally unsatisfiable. Proven live on a driver-610 host: `pyworker: RUNNING`
followed by `port 3000 not listening after 60s`.

The real cost was not one red test. Because it could not pass, `base-qa` could
never set `SERVERLESS=true` — so **85 and 86 had never executed once, anywhere**,
and serverless mode was entirely unexercised by the gate. `85` stays in base
(non-serverless services stopped, their ports closed, is a property base genuinely
owns); `86` now lives in the four engine `.d/` suites (vllm, sglang, llama,
comfyui), where the backend exists. Its `is_serverless` guard keeps it inert —
not skip-as-pass, since there is nothing to assert when the mode is off — until a
serverless template turns the mode on. **L067**
(`check_base_tests_have_no_serverless_backend`) forbids reintroducing a pyworker
or `:3000` assertion under `tests/base/`; prose explaining the history is fine.

### Process presence is not readiness — **GATED (L069)**

`65-supervisor-launch.sh` backgrounds `supervisord`; boot then walks straight on
to `70-instance-test.sh`, which backgrounds the test runner. The two are
effectively simultaneous, so the suite's **first** test runs while supervisord is
still starting. The gate it used was `pgrep -f supervisord`, which is satisfied
the instant supervisord forks — while the RPC socket that every other service
assertion needs appears later. Measured in the shipped image
(`vastai/base-image:cuda-13.2.0-auto`), idle, 16 cores:

    pgrep -f supervisord  visible at   1.7 ms
    supervisorctl status  usable at  383   ms

380 ms of window on an idle desktop; seconds on a contended host reading the
Python stdlib cold off overlayfs while it provisions. On the 2026-08-18 pytorch
promote, `base/10-supervisor` failed **0.09 s into the suite** with
`supervisorctl cannot communicate with supervisord (exit 4)`, took `20-portal`
and `26-caddy-auth` down as collateral, and blocked every tag in the batch under
all-or-nothing promotion — on an image the same suite proved healthy 53 seconds
later. Reproduced locally at 2 failures in 3 runs, idle.

The same shape one level down: the long-running `conf.d` programs carry
`startsecs=5` (`cron` and `pyworker` are `startsecs=0`), so `supervisorctl status`
legitimately reports `STARTING` for the first five seconds of such a program's
life and a single-shot check reads that as "not running". `25-caddy-proxy` gated
on `pidof caddy` and had the same defect with a five-second window instead of a
sub-second one.

One layer further in again: RUNNING answers for the *wrapper*. `caddy.sh` runs
`caddy_config_manager.py` — cost-14 bcrypt per proxied app, 43 s measured on a
contended host — before it execs `caddy run`, so the caddy BINARY appears well
after supervisord calls the program started. Every readiness question in this
suite has to name which layer it is asking about.

And when the socket is down, `supervisorctl status X | awk '{print $2}'` yields a
word from an **error message**, not an empty string — so "cannot tell" was
already being rendered as a definite answer.

The fix is in `lib.sh`, so every caller inherits it: `wait_for_supervisor`
(bounded wait for the socket, memoised in the caller's shell — inside `$( )` the
memo dies with the subshell and each of eight services would pay the full
timeout), and `assert_service_running` waiting for `RUNNING` with a `FATAL`
short-circuit.

**L069** (`check_no_presence_as_readiness_gate`) forbids asserting that a
supervisord-managed process is UP via `pgrep`/`pidof` unless something
socket-backed appears earlier in the same file. Scope, deliberately narrow:

- **Presence for IDENTITY stays legal.** Caddy's pid is genuinely needed to
  attribute its listening sockets, and supervisord cannot supply it — `caddy.sh`
  is a wrapper, so `supervisorctl pid caddy` returns the shell. Only being the
  *readiness gate* is the defect, so the rule asks about ORDER.
- **An assertion is any of `test_fail`, `test_fatal`, `fail_later`**, anywhere on
  the logical line — not only after `||`. The first version required the
  `|| test_fail` spelling, which left `|| { test_fail ...; }`, `|| fail_later`
  and `if ! pgrep ...; then test_fail; fi` all silent. The last is the most
  idiomatic rewrite of the banned line and reads *more* careful; `fail_later` is
  the house idiom in `26-caddy-auth` and `65-conditional-services`, so the rule
  was blindest in the two files most likely to grow one.
- **Negation is judged by POSITION, not by the token.** `! pidof caddy ||
  test_fail "still running"` asserts ABSENCE and is exempt; `if ! pgrep X; then
  test_fail` asserts PRESENCE and must fire. Same tokens, opposite meanings — an
  exemption keyed on a bare `!` got the second one wrong.
- **`if pgrep` predicates are exempt** — a branch is not an assertion. `if pgrep
  X; then test_fail` is an absence assertion and is likewise exempt.
- **Only WAITING helpers count as reaching the socket.** A bare `supervisorctl
  status` does not: accepting it made the rule satisfiable by hoisting a
  non-waiting call, producing a file that is lint-clean and strictly worse than
  the one that tripped it. That is how a rule gets trained out of people.
- **Helper names inside strings are ignored.** These files carry long narrative
  prose and explicit failure messages, so `echo "run wait_for_supervisor"` used
  to disarm the rule for the whole rest of the file.
- **Program names are read from `ROOT/etc/supervisor/conf.d/`**, never restated
  in the linter, so adding a program arms the rule for it.
- Weak by design, following L059: it checks that the file reached the socket
  first, not that the wait covers the same service. A wait for the wrong service
  is a review problem; no wait at all is a structural one, and only the second is
  decidable by reading the file.
- **Known blind spots, stated rather than implied.** A service name held in a
  variable (`pgrep -f "$svc"`) cannot be resolved statically, and the
  `grep "[s]upervisord"` bracket idiom does not match the program name as
  written. Neither is in the tree; both would pass.

### The home/environment sync is exercised by the gate — base-qa runs it

Boot stages 35 and 37 move `/root`, `/home/*` and `/venv/*` onto `$WORKSPACE` and
symlink them back. **Nothing in this repo turned them on**, so a change to that
code first executed on a customer instance — which is how a bounded-wait fix
shipped with a `return 1` sitting between the `.ssh` MOVE and the symlink back,
leaving an instance that boots green with key-based SSH dead and
`46-user-propagate-ssh-keys.sh` dying on `realpath` under `set -euo pipefail`.

`templates/base-qa` now sets `--sync-home --sync-environment`, deliberately
**without a volume**. No volume is needed to exercise it: `$WORKSPACE` defaults to
a container directory, and the lock, the completion markers, the wait, the
relinking and the `.ssh` round-trip are all identical. Only the multi-instance
race needs a shared volume, and that is a different question from "does this code
still work at all". Measured cost on base: ~17s.

`base/36-home-env-sync.sh` asserts the outcome — that `/root` and `/venv/main`
resolve, that `.synced` is present and `.syncing` is gone, that `.ssh` was
relinked rather than stranded, and that the interpreter in the synced venv
actually executes, because a relink to a half-copied tree resolves fine and runs
nothing. It self-skips when the flags are off, which is correct for every other
image, so `base-qa` names it in `INSTANCE_TEST_REQUIRE_PASS` where that would be
a hole (ADR 0019).

### Runtime races found by audit 2026-08-20 — fixed, NOT gated

Auditing the whole image surface for the pattern behind L069 and L071 —
*readiness inferred from something other than the thing about to be used* —
turned up four more, three of them reaching a paying customer's instance rather
than a CI cell. None is gated: they are one-off structural fixes, not a rule a
linter can express. Recorded so the pattern is recognisable next time.

**`/etc/portal.yaml` was a presence-gated handshake with a non-atomic writer.**
`exit_portal.sh` waited for the file to EXIST, then decided from its CONTENT.
`caddy_config_manager.py` wrote it with `open(path,"w")`, which truncates
immediately and only lands content at close — measured at ~0.5 ms idle and 90 ms
under CPU throttling, with an independent observer reading an incomplete file in
300/300 trials. Six services key their entire startup on that grep. Losing is not
a retry: the self-skip is `sleep 6; exit 0`, and `autorestart=unexpected` +
`exitcodes=0` makes supervisord treat it as intentional, so the service is gone
for the life of the instance — and the portal HIDES it, because a skip marker
means "not configured" rather than "failed". Worse, `caddy.sh` ran
`touch /etc/portal.yaml` unconditionally, including when the configurator had
raised, so one malformed `PORTAL_CONFIG` entry left a zero-byte file on `/etc`
(which survives stop/start) and every subsequent boot lost the whole portal.
Now: `tempfile` + `os.replace` (the pattern `provisioner/manifest.py` already
used) and a bounded wait for a NON-EMPTY file.

Three corrections came out of review, and each is the same mistake in a new
place. `mkstemp` creates **0600** and `os.replace` publishes that inode, so the
atomic write made `/etc/portal.yaml` root-only — and `syncthing.conf` is the one
base unit that drops privileges (`user=user`), so its grep got EACCES and it
self-skipped permanently. That is the exact failure being fixed, reintroduced by
the fix, deterministically rather than as a race, and persisted on `/etc` where
an image rollback would not clear it. The write now preserves an operator's mode
or falls back to 0644, and resolves symlinks first, because `os.replace` unlinks
a symlink where `open(w)` wrote through it.

And the placeholder must not be published when the configurator FAILED.
`applications: {}` is well-formed and non-empty, so it sails past the very
non-empty wait added above; the grep then misses and all six services self-skip
silently — making the loud path unreachable in precisely the case it was written
for. `caddy.sh` now publishes it only when `PORTAL_CONFIG` is genuinely empty,
and leaves the file absent otherwise so the waiters time out and report.

**The `.syncing` marker was not the lock.** `35-sync-home-dirs.sh` and
`37-sync-environment.sh` acquire with `mkdir "$dir"` — atomic — and then `touch
"$dir/.syncing"` a syscall later. A co-located instance sharing the volume that
observed the directory between the two found no marker, exited its wait on the
first iteration, and ran `rm -rf /venv/main; ln -s ...` against a tree still
being copied. Everything from boot stage 45 onward then runs against a dangling
symlink with no python. Absence of an in-progress marker cannot distinguish "not
started" from "finished", so both now wait for a `.synced` COMPLETION marker,
with a legacy fallback for trees written before markers existed, and a bounded
budget — `.syncing` lives on a shared volume, so an instance destroyed mid-sync
would otherwise block every later instance forever at boot stage 35, before
supervisord launches.

The bound needed two things review caught. `boot_default.sh` DISCARDS a stage's
exit status, so `return 1` is not "fail loudly" — it is "carry on with the stage
half-applied", and by then `35-sync-home-dirs.sh` has already MOVED every `.ssh`
directory to `/home_ssh` with the symlinks back still below the return. The
instance would boot to a green portal with key-based SSH dead and the customer's
home invisible, and `46-user-propagate-ssh-keys.sh` would die on its first line
(`realpath` under `set -euo pipefail`) rather than recreating anything. The
timeout path now restores those links before returning. And the remediation text
now distinguishes a partial tree (delete the directory) from a lock taken by an
instance that died before writing anything (also delete it) — the original text
said "remove `.syncing`", which for a partial tree tells the operator to make it
pass the legacy discriminator and get symlinked.

**Provisioner-registered services could never reach FATAL.** The generated conf
used `autorestart=true` + `startsecs=0` against the base convention of
`unexpected` + `5`. `startsecs=0` is process presence as readiness, encoded as
the default for every manifest-registered customer app; `autorestart=true`
restarts even on a clean exit 0, so the self-skip idiom became a permanent
six-second loop. Measured side by side against a crashing command: the base
convention reaches FATAL and stops, while the generated one re-execs about once a
second for the life of the instance with `supervisorctl status`, the portal
process list and every health check reporting RUNNING.

**The provisioner registered services against a supervisord that could not hear
it.** `supervisorctl reread`/`update` ran at boot stage 75 — inside the window
L069 measured — with `check=False`, and the result was discarded. The conf files
were on disk, so it logged success, the caller wrote the stage hash (never
retried), and the boot set `/.provisioning_complete`. Both detectors fail open:
`base/12-provisioning` passes on the marker, and `65-conditional-services`
downgrades the exact tell to a WARN. It now waits for the RPC socket, and asserts the thing that is actually ours:
that each service it just wrote is known to supervisord. Not the global
`reread` verdict — that parses EVERY file in `conf.d`, so a malformed unit from
a derivative or a customer's own `write_files` would have failed the phase and
aborted the remaining provisioning, including `post_commands` and
`PROVISIONING_SCRIPT`, over a file with nothing to do with that manifest. Note where this sat: L069/L070/L071 all gate
`supervisorctl` readiness in `tests/` only, and the one place in the SHIPPED
RUNTIME that called it with no wait and ignored the exit code was covered by
none of them.

### A restart is not a readiness event — **GATED (L071)**

Two halves of one defect, both measured on live QA hosts, and the second is the
one that cost cells.

`supervisorctl restart` returns when the WRAPPER clears its `startsecs`, not when
the service is usable. Measured in the shipped image with a wrapper that binds
late: restart returned after **5145 ms** with the port still answering `000`.
`26-caddy-auth`'s skip path restarted caddy with no wait at all, which also
leaked a rebinding caddy into `27-caddy-tls` — the file that runs next.

`wait_for_caddy`'s default port is **2019**, Caddy's admin endpoint. It is
enabled by default (`caddy_config_manager.py` emits no `admin` directive) and it
binds before the site listeners are provisioned. `26-caddy-auth` called it bare
six times and then probed `:1111` and `:6006`; it returned success on 2019 with
the site ports unbound, and the checks recorded `expected 401, got 000` — twice
on one machine, on an image the same run proved healthy 12 seconds later.
`27-caddy-tls` never had this defect: it passes `"$test_port"` explicitly.

The silent variant is worse than the loud one. `find_caddy_ports` keys on
`ss -tln`, so a caddy that has not finished binding yields NO ports and
`26-caddy-auth` takes its `test_skip "no external caddy ports found"` exit — a
green run that asserted nothing about auth at all.

The fix is `wait_for_caddy_ports`, which waits until every port the Caddyfile
DECLARES is listening — answerable from the Caddyfile before the listeners
exist, which is the whole point.

**L071** requires every `supervisorctl restart` in a shipped test to be followed
by a readiness wait, and forbids a bare `wait_for_caddy`. Scoped honestly, in
L066's phrasing: it gates that a wait EXISTS and that it NAMES a port, not that
the port is the one the next assertion uses.

### The derivative phase does not start on a failed base — ADR 0030

`base/` is the platform contract and is cheap: 66 s mean across the 70 cells of
the 2026-08-20 pytorch promote. The `*.d/` suites are the expensive part — 79 s
on pytorch, but tens of minutes on an image that downloads model weights and runs
inference.

Under ADR 0019 a failed test meant BLOCK, so finishing the run bought diagnostic
detail. Under ADR 0029 any failure redraws, so once base is red the cell is
already lost and the tail can only spend a rented GPU to reach a conclusion
already reached — then be discarded and paid for again on the redraw. A
derivative PASS on a platform failing its own base contract is not evidence
either.

`runner.sh` now finishes the base phase and refuses to enter the derivative
phase if it failed. The cut is at the phase boundary, NOT at the failing test:
base tests cost seconds, and aborting on the first red would have destroyed the
evidence that produced ADR 0029's amendment — `10-supervisor`, `20-portal` and
`26-caddy-auth` failing together is what identified one shared root cause, and
`67-service-functionality` passing 53 s later is what proved the host innocent.

The report says **NOT ATTEMPTED** in those words. The required-pass gate already
treats a skipped required test as a failure, so the machinery cannot mistake an
unrun test for a passing one; the wording is for the human reading the summary.

### Readiness budgets are levers, and their floors are gated — **GATED (L070)**

The same 2026-08-18 batch lost two more cells to timeouts that are judgement, not
structure. This section previously said they were "fixed but NOT gated", on the
grounds that a linter cannot decide whether a number is large enough. That was
half true and, as an argument for gating nothing, wrong: a linter cannot decide
**sufficiency**, but it can decide whether a budget has been put back **below the
cost that was actually measured**. It had to be: reverting `HTTP_CHECK_MAX_TIME`
to 5 and the portal budget to 30 — the two exact values that failed real cells —
passed every test in this repo.

Two properties, pulling opposite ways, which is why they are one rule:

- **Overridable.** The suite ships INSIDE the image, so a baked number can only
  be corrected by rebuilding and re-promoting every image in the family. Behind
  `${VAR:-N}` it is a template edit. Every other tunable in this harness
  (`PROV_TIMEOUT`, `INSTANCE_TEST_DEFAULT_TIMEOUT`, `EXPOSURE_ENFORCE`) is
  already env-driven; these were the exception.
- **Floor-pinned.** An overridable default is also easy to edit downwards, and
  the values that failed are the ones someone would reach for.

**L070** (`check_readiness_budget_floors`) enforces both, plus the obvious
corollary that `http_check` must READ the variable — a lever nothing uses is not
a lever. It is a FLOOR, not an equality: raising a budget after a new measurement
must never require a linter change.

`caddy hash-password` emits bcrypt at cost **14**, and Caddy verifies an unknown
username against a fake hash anyway so timing cannot enumerate accounts. It
caches successes only, so every distinct WRONG credential pays a full
verification — which is precisely what the auth-rejection checks send. bcrypt is
single-threaded, so the cost tracks the CPU share the container gets. Measured in
the shipped image:

| CPU quota | mean wrong-credential latency |
|---|---|
| 16 cores idle | 690 ms |
| `--cpus=0.50` | 1410 ms |
| `--cpus=0.25` | 2232 ms |
| `--cpus=0.12` | 4666 ms |

`http_check`'s `--max-time 5` sat inside that range, so two rejection checks
returned `000` — a curl timeout, not a server answer — and failed a base test on
a healthy image. `HTTP_CHECK_MAX_TIME` now defaults to **20 s**, covering roughly
a 0.03-core share, and the same raised budget applies to the raw `curl` calls in
`26-caddy-auth` that hit the same auth-protected URLs. The old
budget was also self-amplifying: when curl gives up Caddy carries on computing
the abandoned bcrypt on the same starved core the next check needs, which is why
the production failure came in pairs. Reproduced at `--cpus=0.12`: forcing one
check back to 5 s made the *next* fail at 20 s and reproduced the production line
verbatim; at 20 s throughout, clean. An earlier version of this fix declared `# TEST_TIMEOUT=900` on `26-caddy-auth`
to cover the raised budgets. That was wrong twice: 900 is exactly
`INSTANCE_TEST_DEFAULT_TIMEOUT` in the promote workflow, so it changed nothing in
CI, and it CUT the runner's own 3600 s default by 4x for anyone running the suite
by hand — the slow case it was meant to protect. The ceiling is bounded in the
code instead: the first caddy restart that does not come back now fails the test
immediately, so the worst case is one caddy budget plus the `http_check`s rather
than six budgets.

**A third instance of the same defect was left behind by the first pass.**
`wait_for_caddy` was still 30 s against that measured 43 s restart, and its expiry
only WARNed — every caller ignored it, so the next `http_check` hit a port with no
listener and recorded `expected 401, got 000`, indistinguishable from the bcrypt
timeout and NOT fixable by a longer `--max-time`, because a refused connection
returns instantly. It is now `CADDY_READY_TIMEOUT` (120 s) and every caller fails
the test on expiry.

`20-portal`'s 30 s was a budget for a process, but the wait is on a chain. The
portal binds in 1.9 s (4 cores) / 2.9 s (`--cpus=0.5`) measured cold. What it
waits on is `exit_portal.sh`, which blocks until `/etc/portal.yaml` exists —
created by `caddy.sh` only after `caddy_config_manager.py` has run, which hashes
once per proxied app. On 2026-08-18 the test gave up at 30 s and
`67-service-functionality` passed on the same endpoints 53 s later, on the same
instance; a caddy restart on that box took 43 s. Now `PORTAL_READY_TIMEOUT`,
**120 s**.

`wait_for_url` also could not honour any budget: it had no `--max-time` on the
probe and only advanced its counter when a probe COMPLETED, so a service that
accepts a connection and then wedges blocked the first `curl` forever and the
test ran to the runner's timeout — reporting `timedout`, which names no failing
check at all. Both wait helpers now bound the probe and use a wall-clock
deadline.

### A serverless QA cell cannot reach the production autoscaler — **GATED (L079)**

The worker POSTs its status to `${REPORT_ADDR}/worker_status/`, and **both** layers default
that variable to the live autoscaler:

```
start_server.sh:19   REPORT_ADDR="${REPORT_ADDR:-https://run.vast.ai}"
backend.py:94        os.environ.get("REPORT_ADDR", "https://run.vast.ai")
```

So a cell that sets nothing does not post nowhere — it posts to production. Until
2026-08-27 the declared serverless cells on sglang and llama-cpp did exactly that on every
run since they existed: disposable QA instances announcing themselves to the real
autoscaler as workers, with an unset `MASTER_TOKEN` and a real `CONTAINER_ID`.

**The cause was a good rule applied to the wrong variable.** Those cells deliberately pass
nothing beyond `SERVERLESS=true`, so that `BACKEND`, `MODEL_NAME` and `MODEL_LOG` are read
from the image's own bakes instead of being overridden by the gate — that minimalism is
correct, and it is the whole claim of the serverless-enablement work. `REPORT_ADDR` is not
product configuration; it is the address of a live external service, and there "inherit the
default" means "inherit production". The detection cells added the same day set it and were
never affected, which is why the contrast made the gap visible at all.

**An allowlist on the value, not a blocklist of known hosts.** The address must be under the
RFC 2606 reserved `.invalid` TLD or a loopback literal. Blocklisting `run.vast.ai` by name
would pass every other live endpoint someone reaches for next — the same
enumerate-the-failures shape that let a cancelled run announce a promotion earlier the same
day. `.invalid` cannot resolve, so the POST dies in DNS and no live endpoint can be touched
whatever the value is later renamed to.

**What this costs, stated honestly:** `metrics.py` retries 3x at 2s intervals, logs at DEBUG
and carries on, so the worker still starts, binds :3000, serves and benchmarks. No serverless
QA cell has ever proved that worker status REACHES the autoscaler, and none can — that would
require POSTing fabricated status to production with a fake token. The cells now decline to
imply otherwise rather than quietly doing the real thing.

### A CUDA label is read off the ARTIFACT, never inferred from the tag name — ADR 0035

An upstream tag's spelling is not a stable statement about its contents, because we do
not control the vocabulary and upstream can re-point a name without renaming it.

`vllm/vllm-omni` did exactly that: the bare tag meant CUDA 12.9 up to `v0.18.0` and
CUDA 13.0 from `v0.20.0`, with no `-cu130` variant published to signal the change.
`build-vllm-omni.yml` hardcoded bare -> 12.9, so five published tags
(`v0.20.0` through `v0.28.0`, `-cuda-12.9`) contain CUDA **13.0.2**. The CUDA minor
drives host driver matching, so an understated label can place an image on a host whose
driver cannot run it. The genuine 12.9 image (`minimax-h3-cu129`) was never built,
because the mapper had no `-cu129` branch.

The trap worth recording: `build-vllm.yml` had already met this change and answered it
with `$has_cu130` — treat the bare tag as 13.0 only when no explicit `-cu130` exists.
Copying that to omni looks like the obvious fix and is wrong, because omni has never
published `-cu130`; verified against live tags, it relabels the genuinely-12.9
`v0.14.0`/`v0.16.0`/`v0.18.0` images as 13.0. **A heuristic that is correct for one
upstream is not correct for another that spells things differently.**

So: read `CUDA_VERSION` from the upstream image config, truncate to `major.minor`, and
skip any variant whose CUDA cannot be read rather than defaulting it. Selection is
anchored to `^<version>(-cu[0-9]+)?$` (as `build-sglang.yml:118` already does), which is
what makes reading the artifact safe — `startswith` alone admits `v0.28.0rc1` and
`v0.26.0post1.*`, whose configs resolve to a real CUDA and would enter the matrix as
duplicates.

**A second failure mode, found converting `build-sglang.yml`'s release path
(2026-09-02).** The same inference can DROP an image rather than mislabel one, which is
quieter still. That rule read "treat the bare tag as 13.0 only when no `-cu130` exists",
so for the pre-v0.5.11 shape — bare plus `-cu130`, no `-cu129` — the bare tag matched no
branch and fell through to `empty`. `lmsysorg/sglang:v0.5.8` reports CUDA 12.9.1 today,
so rebuilding it would have silently produced ONE image instead of two, losing the 12.9
build with no error anywhere. Reading the artifact is correct in both eras without
knowing which one it is looking at.

That conversion also has to keep an alias from becoming a duplicate: upstream publishes
the bare tag and `-cuNNN` as the SAME digest (`v0.5.18` and `v0.5.18-cu130` are one
image). Dedupe on **digest**, preferring the explicit name, and reserve the hard error
for two DIFFERENT digests landing on one CUDA label. A blanket collision error — correct
for `vllm-omni`, which has no aliases — would fail every sglang release build.

**Not yet gated, and deliberately so.** The natural rule — no workflow assigns a literal
CUDA version to an upstream bare tag — would fire on `build-vllm.yml:128-134`, which still
uses the `$has_cu130` heuristic, and on `build-vllm-omni.yml:80`, whose NIGHTLY path
hardcodes 12.9 while `vllm/vllm-omni:nightly` reports 13.0.2 (missed when that file's
release path was converted). Those are the two known holdouts. Gating means converting
both, and per the Bug -> Invariant protocol the baseline must be clean before the rule
lands. Note the rule must not fire on `build-comfyui.yml`'s `{cuda: "12.9", py: "py312"}`
matrix: that selects OUR pytorch base and is a build input we control, not a claim about
someone else's artifact.

### A Slack headline may claim a promotion only where the promotion SUCCEEDED — **GATED (test_promote_notification_truth.py)**

The rule is one sentence: **enumerate the way it goes RIGHT, never the ways it goes wrong.**
Every headline that can render "promoted" must be keyed on `needs.<promoting job>.result !=
'success'` taking the negative branch first. The promoting job is `promote` or
`merge-manifests` — two names across the whole tree, and the convention is load-bearing.

This repo has now been bitten by the same class four times, each time in a workflow the
previous fix did not reach:

1. **2026-08-14** — a QA cell drew a GPU-less host, the gate correctly blocked, `promote` was
   skipped, and Slack said "Base image promoted — 1 auto tag(s) HELD". Both arms of the
   expression opened with "Base image promoted" and never consulted `needs.promote.result`.
2. **The first fix** enumerated `'skipped'` and `'failure'` and fell through to the success
   text. A **cancelled** run is neither, so the same false line came back.
3. **2026-08-17** — `build-result` read `needs.build.result` alone, so a run that lost a
   manifest to a GitHub 429 reported "Base Image Build Successful".
4. **2026-08-27** — three branch dispatches were cancelled at the production approval gate.
   Every QA cell had passed, so `qa.outputs.gated` was `'true'` and `merge-manifests` ended
   `'cancelled'`, which is not `'failure'`. Slack: `:x: vLLM promoted — live-GPU QA passed`
   and the same for SGLang, next to red run cards, for images that were not promoted and
   could not have been. `build-llama-cpp.yml` had already been fixed and its comment named
   the other three files by name, adding "the shared guard is the real fix" — which was never
   built, so the fix stayed a local patch and the defect stayed shipped.

**The icon being right does not rescue a false sentence.** In (4) the ❌ was correct
(`build-result` reads the promoting job) while the words said the opposite. That is the worst
combination available: a reader who trusts the words is misinformed, and a reader who notices
the contradiction learns to discount the words entirely — which disarms every future headline.

**The guard is now over ALL callers of `notify-slack.yml`**, discovered by walking the
workflow directory, not over an enumerated list — the enumeration is the same defect one
level up. Two supporting properties, each of which was independently missing:

- `test_the_walker_actually_finds_the_callers` pins a floor on how many notifiers are in
  scope. A discovery-based guard that matches nothing reports green forever.
- `imagegen-tests.yml` must trigger on `.github/workflows/**`, not on four named files. The
  guard reads every workflow, so a PR touching only `build-vllm.yml`'s headline has to run
  it — under the old list it did not, which is precisely how (4) shipped.

### The studio's CUDA backend must be USABLE, not merely present — **GATED (L056, L081)**

ADR 0016 found `unsloth studio setup` shipping a CPU-only llama.cpp: `setup.sh` gates
`-DGGML_CUDA=ON` on a runtime GPU probe that `docker build` cannot satisfy, so it fell
through silently and every inference ran on CPU. The guard was `test -f …libggml-cuda.so`,
and while the binary was COMPILED here that was sufficient — the failure produced no file
at all.

**ADR 0018 invalidates that reasoning.** Once the backend arrives as a prebuilt bundle,
`test -f` is satisfied by `tar x` and proves nothing. llama.cpp is built
`GGML_BACKEND_DL=ON`, so the CUDA backend is a dlopen'd plugin: one that fails to load is
skipped **silently** and the binary serves correct answers, slowly, forever. Same defect,
now expressible with the file present.

Three failures, three instruments, and none of them subsumes another:

| Failure | Instrument | Why the others miss it |
|---|---|---|
| the install did not happen | `test -f …libggml-cuda.so` | — |
| it happened against the wrong CUDA | `ldd -r`, **output inspected** | the file exists; `cuobjdump` reads cubins, not the link closure |
| no cubin for an admitted GPU | `cuobjdump --list-elf` vs literal `sm_NN` | it resolves and loads; it CRASHES at kernel launch (no-kernel-image) rather than falling back |

**`ldd -r` exits 0 while printing `undefined symbol`.** Running it is not the check —
reading its output is. An unchecked run is decoration of the same shape as the
`file … || true` that once "verified" an architecture.

**Read the arch set from the ARTIFACT, never the vendor's metadata.** Measured on a real
release, the bundle's own manifest claimed `sm_103` that `cuobjdump` shows the binary does
not contain.

**Name the BRACKET, not one arch** — the template's `compute_cap` floor and its ceiling. A
build can satisfy one end of the admitted range and miss the other.

Scope: both rules trigger on `unsloth studio setup`, so they cover `unsloth-studio` (now
prebuilt) and `aio-studio` (still source-built). The `ldd -r` requirement applies to both
because the failure it catches does not depend on where the binary came from.

### A bind verdict comes from ss's LOCAL column, never the whole line — **GATED (L082)**

`ss -tln` prints `State Recv-Q Send-Q Local:Port Peer:Port`, and for a **listening** socket
the peer column is always `0.0.0.0:*` (or `[::]:*`) whatever the socket bound to. So

```bash
ss -tln | grep ":8080 " | grep -q "0.0.0.0:"      # true for EVERY listener
```

matches the peer column and is true regardless of the address under test. The address is
field 4.

**Both directions of this were shipping at once**, from one root cause:

- A test expecting LOOPBACK makes the always-true match **always fail**. Measured live on
  a QA cell: `LISTEN 0 2048 127.0.0.1:18888 0.0.0.0:*` — a correct bind — reported as
  `bound to a PUBLIC interface`, failed the cell, and then burned two host redraws
  reproducing an image-independent test bug on fresh hardware.
- `base/65-conditional-services` expects `0.0.0.0` (`.launch`-managed jupyter is
  deliberately public, with TLS), so the same match returned the desired answer **by
  accident** and its `WARN` branch could never fire — a check that cannot fail, in the
  shipped base image.

The fix is a shared helper rather than a corrected regex per caller, because the trap is
in the column layout and every hand-written match re-encounters it: `listener_local_addr`
extracts field 4, `listener_is_public` anchors a wildcard match at the start of it —
unanchored, `0.0.0.0` also matches inside a port or a longer address form, and the port
match is anchored too so `118888` is not a listener on `18888`.

**Scope honestly:** this gates ADDRESS matching only. Reading an `ss` line for a PORT
(`grep -q ":${port} "`) or a pid is unambiguous across columns and is deliberately not
caught — over-firing would push authors toward `awk` for cases that never needed it.

### A supervisor state is not a functional verdict, and "expected" is decided positively — **GATED (L086)**

`service_running` reports a supervisord STATE. Using it as an `if` guard —
`if service_running x && wait_for_port p; then … else skip; fi` — collapses three different
worlds into one silent pass: **not configured**, **RUNNING but never bound its port**, and
**supervisord has never heard of it**. A jupyter that hangs without exiting binds nothing,
and the suite reported ALL TESTS PASSED. `autorestart=unexpected` catches CRASHES, so the
hang is precisely the state nothing else covers. `assert_service_serving NAME PORT` fails on
either half.

**The correction to the first fix is the more important half.** Deciding "is this service
expected?" from `/etc/supervisor/conf.d/NAME.conf` alone is WRONG and would have broken the
build: `syncthing.conf` and `tensorboard.conf` ship unconditionally in base, but 5 of the 7
QA templates carry no portal entry for them, so `exit_portal.sh` correctly exits 0 and the
service sits EXITED by design. Requiring the port there is a 60s wait and a hard fail on a
healthy image — and `runner.sh` skips the entire derivative phase on any `base/*` failure,
so it would have reddened every derivative QA cell. `base-qa` and `pytorch-qa` list every
entry, which is exactly why it looked safe when only base was considered.

The predicate is **configured AND routed AND not-serverless**:

```bash
[[ -f /etc/supervisor/conf.d/<name>.conf ]] && portal_has_entry "<term>" && ! is_serverless
```

The `! is_serverless` half is not optional: `base/85-serverless-services` asserts these SAME
programs are STOPPED in serverless mode, so without it two base tests assert opposite
verdicts on one instance.

### Three more shapes codified from the same audit — **GATED (L083, L084, L085)**

- **`fail_later` takes LABEL and MSG.** A third argument is dropped, the message truncates,
  and the prose fragment becomes the FAILURES label, so `report_failures` emits a sentence
  where a greppable label belongs.
- **`curl -w '%{http_code}' … || echo 000` yields `000000`.** curl writes the template and
  THEN exits non-zero, so the fallback appends and the value matches no arm the author
  wrote. curl already emits `000`.
- **A readiness default must be under the file's own `# TEST_TIMEOUT`.** `runner.sh` execs
  each test under `timeout ${TEST_TIMEOUT}`, so a longer wait is killed mid-flight and
  reported as a bare timeout naming no check — the least actionable failure the harness can
  produce.

**Both new rules shipped broken and were caught before merge**, which is the argument for
running a rule against real input rather than reasoning about it: `L084` was INERT against
the two-line form it was written from (capture and fallback either side of a line
continuation) while FIRING on correct code (a `|| echo WARN` belonging to a later
statement), and the arity counter ran past `;` and ignored single quotes. A rule that cannot
fire is decoration; a rule that fires on correct code is worse than none.

### The cloudflared binary is unpinned, and a CONTRACT is what guards it

`Dockerfile` fetches `cloudflared-linux-${TARGETARCH}` from `releases/latest`, so
what ships is whatever Cloudflare published that morning (observed: 2026.8.2,
built the previous day). That is deliberate — a pin is a knob someone has to turn
on every rebuild, and a version bumped only under time pressure is one nobody
validates. What replaces it is `portal-aio/tests/test_cloudflared_contract.py`,
asserting the three things `tunnel_manager` actually depends on against whatever
binary just shipped: both argv shapes, `--no-autoupdate` **applied** (its absence
removes the key from cloudflared's echoed `Settings: map[...]`, so "accepted" and
"honoured" are distinguishable), and a quick-tunnel announcement matching
`QUICK_TUNNEL_URL_RE` — a module constant both the code and the test read, never
a copy.

**Per architecture.** amd64 and arm64 are different release artifacts, so the
build-time job runs one cell per arch (QEMU/binfmt for the arm64 ELF) and no more:
Cloudflare rate-limits quick-tunnel creation, so a cell per config x python would
rate-limit itself and then report the rate limit as a defect.

**Three outcomes, default BLOCK.** A usage rejection, an unapplied flag, or a
tunnel that announces nothing parseable fails. Only an explicit rate limit
(`429|too many requests|rate.?limit|quota exceeded|error code: 1015`) or an
explicit transport failure (`dial tcp|i/o timeout|no such host|…`) is
inconclusive. The first version folded `failed to request quick Tunnel` into the
rate-limit pattern, so the one failure the gate exists to catch was being skipped.
(An earlier draft of this section called that string the *universal* wrapper for
the creation path. It is not — the rate-limit path emits `failed to unmarshal
quick Tunnel` instead. The claim was reasoned, not measured; the discriminator is
the cause, never the wrapper.)

**Positive evidence is checked BEFORE negative evidence.** If cloudflared says
`quick Tunnel has been created`, the contract question is live and gets answered,
whatever else the log holds. A tunnel is created before its edge connections are
dialled, so transport noise *after* the announcement says nothing about whether
the portal can parse it — and checking the negative patterns first let late noise
overrule a tunnel that demonstrably existed, turning a genuine
`QUICK_TUNNEL_URL_RE` regression into a skip. Proven by mutation: with the old
ordering, a release that moves the announcement format reports `unverified`; with
the fix, `broken`.

**Skip is not pass.** The gate's normal degraded outcome is `skip`, and pytest
exits 0 on it — measured under a real per-IP rate limit: `3 passed, 3 skipped`,
exit 0, where the three passes introspect the portal's own argv and say nothing
about the binary. An exit code therefore cannot express what this gate needs to
say. `portal-aio/tests/classify_contract_run.py` reads the junit report and
returns one of **verified** (every live assertion executed), **unverified**
(nothing failed, but something did not run), or **broken** (a live assertion
failed). Liveness comes from the `@pytest.mark.live` marker carried into the
report by an autouse fixture — never from a list of test names, which a rename
would silently downgrade.

**The result must reach the notification, in three states.** A `needs:` edge alone
is not a control: the Slack status rendered from `needs.build.result` only, so a
failing contract reported green while the staging tags were already pushed. Two
states were not enough either, and got both edges wrong — a rate-limited run
rendered ✅ having proven nothing, and an ordinary build failure (which *skips*
the contract job) rendered "the tunnel binary is UNVALIDATED" over a compile
error. The aggregate is severity-ordered `broken > unverified > verified`, with
`not-run` kept distinct so an upstream failure never claims a tunnel defect.

**The per-PR suite must not spend the tunnel quota.** The live tests create real
trycloudflare tunnels against a per-IP limit; `portal-aio-tests.yml` deselects
them with `-m "not live"`. Running them on every portal PR spends the quota on
`releases/latest` — not the binary that ships — and leaves the build-time run,
which tests what *does* ship, rate-limited and unable to prove anything.

**Known gaps, recorded rather than implied:**

- **Freshness.** `--no-autoupdate` removes the only thing that kept a long-lived
  instance's cloudflared current. Derivatives pin dated base tags, so the fleet's
  spread will widen with no floor and no alert; `base/50-custom-binaries.sh` only
  asserts the binary exists. Cloudflare does age out old builds. Nothing here
  detects that, and the first symptom would be tunnels failing on older images
  only.
- **No integrity check.** The fetch has no checksum or signature. A behavioural
  contract passes just as happily on a substituted binary. (`syncthing` and
  `miniforge` in the same Dockerfile are unpinned-latest too, so this is the house
  convention rather than a new exposure — but the contract does not close it.)
- **The fix does not reach running instances.** `portal-aio` also ships as a
  release tarball (ADR 0015) and `VERSION` is not bumped, so no running instance
  gets `--no-autoupdate`, and `v3.1.4` now names another distinct payload — the
  same caveat already recorded above for ADR 0026.
- **Cloudflare's availability is coupled to the build's headline.** The skip list
  is a closed enumeration of rate-limit and transport strings; anything else that
  fails to produce a tunnel BLOCKs by design. So an `api.trycloudflare.com` 5xx, a
  403, or an expired-certificate error would red a set of images that are fine.
  Accepted deliberately — the alternative is widening the inconclusive bucket,
  which is what let a real break through in the first place — but it is a third
  party's uptime touching the release signal, and if it fires in practice the
  answer is to route those causes to `unverified` (⚠️), not to widen the skip.
- **No skip floor.** Nothing fails the gate when it has been `unverified` for N
  consecutive builds. A gate that is *usually* inconclusive is one nobody reads,
  and the ⚠️ makes that visible without making it enforceable. Deferred, not
  solved: it needs run-history state the build workflow does not currently keep.
- **Promotion does not read this.** `promote-base-image.yml` is a separate
  human-gated dispatch with its own QA gate, and it has no view of the contract
  result. The control here is the build notification plus the human who reads it —
  stated plainly because the wiring could be mistaken for an automated block.

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

### A failing cell redraws before it blocks (ADR 0029)

`qa-gate.yml` is shared by base, comfyui and vllm (and imagegen-tests), none
of which carries retry logic of its own — so base-image is the reference for
how a QA cell behaves everywhere. (pytorch's gate is real but lives on an
unmerged branch; it is NOT on main, so a reader following this to
`promote-pytorch.yml` will not find a QA job there yet.)

A cell redraws on any non-zero exit except `config_error` (4) and `interrupted`
(130), because reproducibility rather than symptom is what separates a bad host from a bad image: measured on 2026-08-18,
six of seventy pytorch cells blocked and every one investigated passed on other
hardware, all of them with FAILED tests that the previous zero-failure rule could
not redraw. **Amended 2026-08-19:** on re-examination only THREE of those six were
host faults (across three machines). The other three were defects in our own test
suite — the supervisord readiness race now gated by L069, and the two budgets
above — and two of those three ran on the same machine, so they had been counted
as independent exhibits. The redraw stands; its evidence base is half what it
claimed.

Every failed attempt records the `machine_id` it ran on (`SUSPECT-HOST`, and a
job-summary table), because the instance is destroyed immediately afterwards and
the evidence is otherwise lost. The table separates the readings deliberately: a
cell that PASSED after a redraw exonerates the image; a cell that never passed
does not, and de-verifying those hosts would be wrong.

**A redraw pass is necessary but NOT sufficient to blame the host** (ADR 0029,
amended 2026-08-19). It has three causes, not two: a host fault, a flaky image
defect, and a defect in our own suite that a slower host merely exposed. A cell
whose failing test was later contradicted by a passing test of the same property
**in the same run** is self-contradicted — a harness defect, no host suspicion.
That happened twice on 2026-08-18: `20-portal` failed and
`67-service-functionality` passed on the same endpoints, on the same instance,
53 seconds later. The unamended rule would have nominated a healthy machine
twice. De-verification acts on someone else's hardware, so a suspect-list entry is
a **candidate for investigation, never an instruction** — nothing yet computes
self-contradiction, and until it does the two known harness pairs are checked by
hand.

Accepted cost, stated: a FLAKY image defect can now pass by luck, where the old
rule would have blocked it. A deterministic one still fails every draw and blocks.
The suspect record is what makes the trade defensible — an image failing across
many DIFFERENT machines shows as a pattern rather than a green tick.


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

- **A llama.cpp image proves its CUDA backend is IN USE, not merely present (GATED, L076).**
  llama.cpp is built `ggml_backend_dl: ON`, so the CUDA backend is a dlopen'd plugin. When that
  dlopen fails, `llama-server` does not crash and does not exit non-zero — it falls back to the CPU
  backend and serves correct answers, slowly, indefinitely. Nothing else in the suite can see it:
  `10-llama-serving` asserts the service, `/health`, a non-empty `/v1/models` and a non-zero token
  count; `12-llama-contract` asserts token arithmetic, a grammar, a named tool, a status class and
  a bind address; the serverless cell asserts a benchmark score was written. **A 0.5B q8_0 GGUF on
  CPU satisfies every one of those in seconds, so before `llama.d/11-llama-offload` existed a fully
  CPU-only image passed the entire gate on every cell** — the gate certified a GPU image that was
  not using the GPU. The dlopen fails for causes no other check sees: a libcublas minor the bundle
  was not built against, a driver too old for the bundle's CUDA major, or a host compute capability
  with neither a cubin nor JITable PTX in the binary. L056 does **not** reach this: it triggers on
  `unsloth studio setup`, so any image installing a PREBUILT bundle is exempt from it by
  construction. **L076** gates both halves — the assertion must exist with a real failure path, and
  a gating template must name it in `INSTANCE_TEST_REQUIRE_PASS`, because an offload test that is
  not required can `test_skip` and the gate stays green. `test -f …libggml-cuda.so` does not
  satisfy it: the file is present in exactly the failure being caught.
  **Two instruments, and the choice between them was made the wrong way round first.**
  Gating: (a) `llama-server --list-devices` enumerates a GPU — portable, depends only on the
  binary and the driver, and catches the dominant failures; note it exits 0 printing `(none)`
  when it finds nothing, so the enumerated device is the check, not the exit code. The match is
  LINE-ANCHORED and case-sensitive, because stderr is folded in and ggml's loader prints the
  `.so` path on failure: an unanchored `grep -i '(CUDA|...)[0-9]'` finds `cuda12` inside
  `/opt/llama.cpp/x64-cuda12-portable/libggml-cuda.so` and reports a GPU on the exact failure it
  is looking for (ADR 0033 names the bundles). An output matching NEITHER a device line nor an
  explicit empty listing is a WARN, not a verdict — hard-failing on an unrecognised format would
  repeat the log mistake below. (b) The DRIVER's own view, `nvidia-smi --query-compute-apps`,
  attributes VRAM to the llama-server pid **above a floor derived from the model on disk**, not
  merely above zero: a bare CUDA context plus cuBLAS workspace is a few hundred MiB, so `> 0`
  passes a server whose weights never left system RAM — the `-ngl 0` / partial-offload family,
  which is the larger one. An EMPTY compute-app list is disambiguated by device-total
  `memory.used`, which is namespace-proof: empty beside a loaded device is the documented NVML
  PID-namespace effect and WARNs; empty beside an idle device is the CPU fallback and fails. A
  non-numeric figure (`[N/A]` on MIG/vGPU) is the driver declining to answer, not zero. NOT gating: the server's LOG. The first version inverted this — it gated on
  `ggml_cuda_init` / `load_tensors: offloaded N/N layers to GPU`, the wording llama.cpp printed
  for years, and demoted compute-app attribution on a general concern about PID-namespace
  isolation. Both halves were wrong, measured on run 32835411583: llama.cpp v0.2.0 rewrote its
  logging and prints NO device or offload line at verbosity 3, so the gating check failed on a
  box where `--query-compute-apps` reported the model resident in 836 MiB from inside the
  instance. A gating assertion whose evidence upstream is free to stop printing is a false-red
  generator. Where PID-namespace skew genuinely lands — VRAM held but not attributable to our
  pid — is a WARN, not a failure, because arm (a) has already proved the backend loads (ADR 0016).

- **The engine listens where the OpenAI-core worker proxies, and the pin cannot be erased
  (GATED, L078).** pyworker's `workers/openai/core.py` proxies to `MODEL_SERVER_URL =
  "http://127.0.0.1"` and `MODEL_SERVER_PORT = 18000`. Those two are module CONSTANTS — the only
  values in that file that are not `os.environ` reads, while `MODEL_LOG`,
  `MODEL_HEALTH_ENDPOINT`, `MODEL_LOAD_LOG_MSG` and the model name (across four spellings) all
  are. So `127.0.0.1:18000` is an obligation on the image and its template; no variable can move
  the worker to meet an engine that listened elsewhere. Every worker hardcodes its own address —
  `comfyui-json`, `ace` and `wan` at 18288, `tgi` at `0.0.0.0:5001` — so the rule is scoped by
  BACKEND, not applied to the shape. **Two arms, because the three engines got it wrong from
  opposite sides.** (a) `llama.sh` shipped `${LLAMA_ARGS:---port 18000}`, and a `:-` default
  applies only while the variable is entirely UNSET: a template setting `LLAMA_ARGS` for any
  unrelated reason (`-ngl 99`, `--ctx-size 8192`) erased the port, and llama-server fell back to
  its own default of 8080 (llama.cpp `common.h`: `int32_t port = 8080`), breaking the portal's
  API entry and the serverless worker at once. It was **known**: `llama.d/10-llama-serving.sh`
  carried a comment quoting the defective line, and three more docs told operators to work around
  it — a defect documented into permanence, which is what an invariant plus a rule is for. The
  QA gate could not see it because the QA template passes the port itself. (b) `vllm.sh` and
  `sglang.sh` interpolate `${VLLM_ARGS:-}` / `${SGLANG_ARGS:-}` BARE, with no image-side default
  at all, so for those two the address exists only in the template — and vLLM's own default is
  `0.0.0.0:8000`, a PUBLIC bind, so an unpinned template fails the loopback-behind-Caddy rule as
  well as the worker. L078 therefore requires both: no listen-address pin inside a `${VAR:-…}`
  default in the image's supervisor scripts, and `--host 127.0.0.1` plus `--port 18000` pinned in
  **this engine's own args variable** (`LLAMA_ARGS`/`VLLM_ARGS`/`SGLANG_ARGS`) in the gating QA
  template — the template a production template is copied from. It must be that variable and not
  merely "some key ending in `_ARGS`": a first draft joined every such key and searched the
  concatenation, which a decoy `DUMMY_ARGS`, or the two flags split across two unrelated
  variables, satisfied while the engine's own args stayed empty — L076's satisfied-by-cosmetics
  trap reintroduced inside the rule written to avoid it. Both spellings count (`--host=…` as well
  as `--host …`), because argparse accepts both and rejecting one would be a false red.
  **Scope honestly:** it reaches this repo's templates only; it checks that the pin is PRESENT,
  not that the engine honoured it (`llama.d/10` and the serverless cell do that live); and arm (a)
  never requires an image-side pin to EXIST — it cannot, because `vllm.sh` and `sglang.sh`
  legitimately have none. Arm (b) is what guarantees a pin exists at all; arm (a) guarantees an
  image that HAS one cannot lose it. The fix shape is to add each flag only when the variable does
  not already carry it, so a template that deliberately pins its own address still wins — and to
  log only what the script ADDED, never the operator's args, which may carry `--api-key` and are
  tee'd to a log the portal serves and the gate collects.

- **Serverless mode is decided once, at boot stage 01, and the user can always overrule it
  (GATED, L077 for the expiry; asserted by `base/15-boot-markers`).** `SERVERLESS=true`
  switches the whole runtime: `boot_default.sh`'s update flags, every service sourcing
  `utils/exit_serverless.sh` (caddy, portal, jupyter, syncthing, tensorboard, tunnel
  manager, the engine images' model-ui), `pyworker.sh`, and supervisor units authored from
  a provisioning manifest, which default to `skip_on_serverless: True`. The autoscaler
  injects `MASTER_TOKEN` into every worker but not `SERVERLESS`, so
  `01-detect-serverless.sh` infers it — **only when `SERVERLESS` is unset or empty**. An
  inference from a proxy must never overrule an explicit declaration: it is the
  lower-confidence signal and its false positive costs every interactive service on the
  box, permanently (`exit_serverless.sh` exits 0 and those units are
  `autorestart=unexpected` + `exitcodes=0`, so supervisord never restarts them). That rule
  is also what makes the mechanism inert the day the backend injects `SERVERLESS` itself.
  **The stage EXPORTS and never writes `/etc/environment`** — stage 10 sources that file
  afterwards, so a user's edit prevails, which is the ownership boundary the platform seeds
  at first boot and the user owns thereafter. Re-deciding every boot would be wrong twice:
  `endpt_id` is written only at instance-create, so the answer cannot change, and rewriting
  the file would reclaim territory the user owns. `VAST_SERVERLESS_DETECT=false|off`
  disables it without a rebuild. Deleting the stage on expiry owes an explicit `unset`
  retraction — a first-boot snapshot outlives the mechanism, the same trap ADR 0025 hit
  (ADR 0034).

