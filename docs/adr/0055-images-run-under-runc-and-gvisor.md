# ADR 0055 — Images run under runc and gVisor

- **Status:** Accepted (conditional)
- **Date:** 2026-10-19
- **Decision owner:** Rob Ballantyne

## Context

Our images can be started under gVisor (`runsc`) as well as under runc. The runtime is chosen
outside the image, an image cannot know in advance which one it gets, and the same image can see
both. runc remains the common case.

A container is single-tenant: everything inside it belongs to one user. The non-root session user
exists so that applications that refuse to run as root (mostly desktop ones) will start; it is not
a privilege boundary. Nothing in this ADR defends one process in a container against another in the
same container.

Differences under gVisor that affect our images (behaviour inside the container, as seen by an
image):

- **GPU and graphics.** The GPU is reachable through the NVIDIA device nodes, but there is no DRM:
  `/dev/dri/*` cannot be opened. `/proc/driver/nvidia/version` may be absent, so anything that reads
  it, or tests for it to decide a GPU exists, gets nothing. `nvidia-smi topo -m` fails, and NCCL sees
  a flattened PCI/NUMA topology.
- **Privilege and binaries.** setuid/setgid bits may be ignored for non-root users, so `sudo` as the
  session user can fail. 32-bit ELF cannot execute.
- **Filesystem.** Depending on how the root filesystem is provided, `link()` on a unix socket bound
  on it can fail with `EOPNOTSUPP` (upstream google/gvisor#10143). supervisord publishes its control
  socket by binding a temporary name and hard-linking it into place, so in that case it retries
  forever and starts no program. Regular-file hard links are not affected.
- **Observability.** Listener tables (`ss`, `/proc/self/net/*`) can be incomplete, so a check that
  enumerates listeners may not see every socket. The cgroup filesystem inside the sandbox may be v1,
  v2 or absent (upstream google/gvisor#13007 changes the default over time), `nproc` reflects the CPU
  quota, and `MemTotal` reflects the memory limit.
- **Runtime detection.** The image needs a check it can make from inside the container: current
  gVisor releases always create `/proc/gvisor`
  (`pkg/sentry/fsimpl/proc/checkpoint.go`); older releases did so only with an opt-in flag.

Without a recorded contract, each fix would invent its own runtime check, and the QA harness would
report results under gVisor that it cannot actually see. Two of our ADRs also assume a single
runtime: ADR 0050 condition 7 ("the image moves to gVisor only after a live re-test") and the
exposure gate of ADR 0028.

## Options considered

### A. Separate gVisor images or tags (rejected)

A `-gvisor` variant per image, built with gVisor-specific choices. Rejected: the runtime is not
chosen by the template, so a template cannot choose the right tag. It would double
the build and QA matrix, and the variants would drift. Every gVisor-safe change we found is either
neutral under runc or can be made so.

### B. Branch on the runtime everywhere (rejected as the default)

One runtime check, and gVisor-specific code paths wherever behaviour differs. Rejected as the
default because a runtime branch is code that runc never exercises, so it rots, and because a
runtime test answers the wrong question. What matters is whether a capability is there (can we read
the driver version, can we hard-link a socket here), and that can differ between gVisor
configurations too. Kept for the few cases
with no capability to probe; see the decision.

### C. Trust a runtime label (rejected)

Read a runtime label from the environment. Rejected: a label is set outside the image and the image
cannot verify it, while a check inside the container observes the runtime directly.

### D. Decide exposure under gVisor by probing published ports from outside (rejected; ADR 0028's rejection stands)

When the listener tables are incomplete, replace test 28's verdict with an outside probe of the
instance's public ports. Rejected for the reasons ADR 0028 gave for its option D: it certifies the
QA template's port map rather than the customer's, it cannot see a public bind on an unpublished
port, and telling authenticated from unauthenticated would need protocol sniffing, which test 28
rejects as fail-open. It may be added as extra signal, never as the verdict.

### E. Always put the supervisord socket in `/dev/shm` (rejected)

Simple, and it works under both runtimes. Rejected: `/dev/shm` is meant for POSIX shared memory and
is world-writable, and `rm -rf /dev/shm/*` is a common ML clean-up that would silently cut the
portal off from supervisord. The conventional place for runtime sockets is `/run`, which is where
the socket stays wherever it works.

### F. Rely only on runtime-side fixes (rejected as sufficient)

Runtime configuration can remove some of these failures for every image, including third-party
ones, and is preferred where it applies. Not sufficient alone: the images still have to work where
it does not apply, and a runtime-provided `/run` hides directories the images create at build time.

### G. Fail the boot when the supervisord socket cannot be created anywhere (rejected)

Exit from the boot stage so the failure is unmissable. Rejected: boot stages are sourced into the
container's main process, so an exit (or a return that lets the final stage fall through) stops the
container. Today's failure mode, supervisord looping, at least leaves SSH and Jupyter up for
debugging; a stopped or restart-looping container is worse.

## Decision

Every image in this repo must work under both runc and gVisor. **A change is acceptable only if it
is neutral or better under runc.** Concretely:

1. **Capability checks first.** Code tests for the capability it needs, not for the runtime. A
   runtime check is allowed only where there is no capability to probe (for example choosing
   VirtualGL's readback mode, or wording in docs and metrics). The runtime check is one executable,
   `/opt/instance-tools/bin/is-gvisor` (`[ -d /proc/gvisor ]`), callable from shell and Python. The
   portal's own Python check (`os.path.isdir("/proc/gvisor")`) is the one other sanctioned form.
   `uname` strings and environment labels are never used. The check assumes a gVisor release in
   which `/proc/gvisor` always exists.
2. **One driver-version helper.** A single tool next to `cuda-driver-version` prints the NVIDIA
   driver version: `/proc/driver/nvidia/version` first (a file that exists but does not parse falls
   through), then `nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1` with a
   timeout; it prints nothing and exits non-zero if neither answers. Every shell reader in the repo
   calls it. **GPU presence** is decided by the helper (or `/dev/nvidiactl`), never by testing for
   the `/proc` file. The portal's capability manifest prefers the helper but keeps an in-process
   `nvidia-smi --query-gpu` fallback when the helper is absent, because the latest portal is
   installed at first boot on images built before the helper existed.
3. **supervisord control socket: conventional path, capability-checked fallback.** The boot stage
   that launches supervisord, every boot:
   - removes any existing `/var/run/supervisor.sock` (socket or symlink) and **any generated socket
     include** from an earlier boot;
   - tests whether a unix socket can be bound and hard-linked in `/run` (supervisord's own pattern).
   - If it can, nothing changes: the socket stays a plain socket at `/var/run/supervisor.sock`, no
     include exists, and the effective configuration is the shipped one.
   - If it cannot, it creates its own directory under `/dev/shm` with `mkdir` (not `-p`), checks it
     is root-owned, mode 0700 and not a symlink, probes it the same way, and writes an include under
     `/etc/supervisor/conf.d/` that sets only `[unix_http_server] file=` to the socket there. A
     symlink at `/var/run/supervisor.sock` keeps every client on the usual path. `serverurl` stays
     literal: expanding it from an environment variable would break every `supervisorctl` caller
     without that variable, under runc too.
   - If no location passes, it logs the failure loudly, does not launch supervisord, and keeps the
     boot alive (the final wait stage must not fall through), so SSH and Jupyter stay up.
   - The chosen path is logged at boot and reported by `vast-capabilities`.
   A `/run` that can hold the socket remains the preferred state; with it the probe succeeds.
   The checks on the `/dev/shm` directory guard against confusing failures (a pre-existing or
   recreated path), not against another tenant: `/dev/shm` is private to the container under both
   runtimes.
4. **`/run` is treated as volatile.** A root boot stage before supervisord recreates what the images
   need under `/run` (`/run/user/1001` mode 0700 owner 1001:0, `/run/dbus`), idempotently,
   non-recursively and without following symlinks (`install -d`; no `-R`). Images that need more
   under it (a desktop's `dbus` directory) create it at runtime.
5. **No privilege escalation in the rendering or session pipeline.** Steps that need root run as
   root (supervisor `user=root`, or the root boot stage), not through `sudo` from the session user.
   Such root steps do not act on input the session user controls without validating it.
6. **Supported and unsupported under gVisor** is documented in the base README and the agent guides,
   as runtime behaviour only:
   - Unsupported: 32-bit programs (they stay installed and work under runc), anything that opens
     `/dev/dri` (VA-API, GBM, Wayland/KMS sessions), kernel profilers, `io_uring`,
     `nvidia-smi topo`, FUSE mounts.
   - Degraded: X11 round trips (slower), NCCL topology detection, listener visibility.
   - `sudo` for non-root users works only where the sandbox honours setuid.
7. **QA per runtime.**
   - A runsc QA cell must prove it is running under gVisor (`is-gvisor`), and fail or redraw the
     host otherwise.
   - Listener visibility is checked **per address family** (tcp4, tcp6, udp4, udp6) with a sentinel
     socket of each family, against the source each test actually reads (`ss`, or `/proc/self/net/*`).
     "IPv6 enabled" means an IPv6 sentinel bind succeeds, not a sysctl read. A family that is
     enabled but cannot be seen makes the dependent checks **undecided**, never passed and never
     skipped. An `ss` that fails (for example "Cannot open netlink socket") is undecided, not empty.
     This extends `exposure_scan.py`'s existing exit 2 ("decided nothing"); it is not a parallel
     mechanism.
   - **Exposure and bind-address verdicts are decided by the runc cell.** They normally come from
     the app's configuration, not the runtime. Under gVisor they are reported as undecidable when
     the sentinel fails. Option D stays rejected.
   - **In a runsc cell, every check that can decide under gVisor gates** (supervisord starts, the
     GPU is reachable, no privilege failure in a pipeline, and so on). Only the checks that report
     undecided are non-gating there.
   - Where a QA cell cannot be run under gVisor, gVisor verification is manual.
8. **Amendment to ADR 0050 condition 7.** Replace "the image moves to gVisor (`runsc`) only after a
   live re-test" with: "Before the desktop images are declared working under gVisor, a live run
   under runsc re-verifies all three of: subset-GPU NVENC, the `nvreach` shim (it intercepts
   `access()` on `/dev/nvidia<N>`, which gVisor serves from its own device registry) and the
   filter." The image does not decide when it runs under gVisor; the three re-checks are kept.
9. **ADR 0014 condition 4 is unchanged.** The thread-cap valve never falls back to `nproc` as the
   cap basis. Where the sandbox reports no numeric `pids.max`, it takes the "nothing to protect"
   path it was designed with. That does not mean thread exhaustion cannot happen: a limit the image
   cannot see may still apply, and is outside the image's control. Thread pools stay modest because
   `nproc` reflects the CPU quota.
10. **Code reading cgroups handles v2, v1 and none.** Readers try v2, then v1, and treat nothing
   readable as "no limit known" (no-op, or fall back to process-level figures). No code assumes the
   sandbox's cgroup version, because it can change between gVisor releases and configurations.

## Binding conditions

1. **Neutral under runc, shown not claimed.** Every change ships with a runc arm that shows runc
   behaviour unchanged. For the supervisord change the runc arm asserts that
   `/var/run/supervisor.sock` is a plain socket and not a symlink (`[ -S p ] && [ ! -L p ]`), that
   no generated socket include exists, that the effective configuration equals the shipped one, and
   that `env -i /usr/local/bin/supervisorctl status` succeeds.
2. **Codified, each with a mutation test** (Bug -> Invariant protocol), landing in the same change
   as the fix it polices, so the lint baseline stays clean:
   - The linter's shipped-script scope covers the aio image's `ROOT_BASE` (today it is not scanned).
   - No read of, or existence test on, `/proc/driver/nvidia/version` outside the driver-version
     helper. The portal manifest's exemption covers only its `nvidia-smi` fallback, not any `/proc`
     read.
   - No `sudo` in a supervisor script whose program runs as a non-root user.
   - No runtime check other than `is-gvisor` and the portal's single Python check.
3. **Off-box tests.**
   - Driver-version helper (modelled on the `cuda-driver-version` tests): `/proc` wins over
     `nvidia-smi`; the open-kernel-module `/proc` format parses; an unparseable `/proc` falls
     through; with `/proc` absent and two GPUs, only the first line; whitespace trimmed;
     `nvidia-smi` missing, failing, empty or hanging gives nothing and a non-zero exit.
   - Portal manifest: with `/proc` absent and `nvidia-smi` answering, the GPU section is reported;
     with the helper absent, the fallback answers. The existing test that equates "no `/proc` file"
     with "no GPU" is replaced.
   - supervisord stage, with injectable paths: probe success writes no include and no symlink; a
     fallback on one boot followed by a success on the next removes the include; a pre-existing or
     wrongly owned fallback directory is refused; all probes failing leaves supervisord unlaunched
     and the boot alive.
   - `/run` stage: run twice and over an existing `/run/user/1001`; mode and owner unchanged; a
     symlink planted inside is not followed.
4. **The supervisord fallback is proven live** on a host whose root filesystem fails the probe:
   `supervisorctl`, the portal's XML-RPC connection through the symlink and the provisioner's
   readiness wait all work; the next boot with the probe succeeding (or after a stop/start) leaves
   no include and a plain socket. Met: shown through real boots of the base image (runc, gVisor,
   gVisor after a reboot, runc again).
5. **Visibility fixtures replay the observed gVisor state** (IPv4 rows present, tcp6 absent, `ss`
   netlink error): a missing tcp6 table with IPv6 enabled yields exit 2; an `ss` failure yields
   exit 2; and a runc fixture with IPv6 disabled (no tcp6 table, IPv6 bind refused) still decides.
6. **cgroup fixtures cover three cases** for every cgroup reader touched by this work (the portal's
   metrics, the thread-cap valve's tests): a v2 host, a v1 sandbox with `pids.max` = `max`, and no
   cgroup filesystem. Each gives a decided, correct result or the documented no-op.
7. **Live confirmations before the docs claim them:** `/proc/gvisor` present under gVisor and absent
   under runc; regular-file hard links under both runtimes (a file
   hard link, a real `uv pip install` with the default link mode, a conda environment create).

## Consequences

Positive:

- One image per app keeps working whichever runtime it is started under; no tag choice.
- Fixes that remove `sudo` from pipelines, recreate `/run` and centralise the driver version are
  correct under runc as well.
- The QA harness stops reporting passes it cannot see, and runsc cells still gate everything they
  can decide.

Accepted negatives:

- Under gVisor some checks stay undecided rather than green; exposure verdicts come only from runc.
  An app that chooses its bind from the network it finds could expose differently under gVisor and
  not be caught.
- The `/dev/shm` fallback socket is removed by a user's `rm -rf /dev/shm/*` until the next restart;
  supervisorctl and the portal then report no services. Documented in the agent guide.
- Third-party images with supervisord's stock layout still depend on a `/run` that can hold the socket.
- Known gVisor gaps stay gaps: 32-bit, DRM, NCCL topology, slower X11 round trips.
- The portal manifest carries a small duplicate of the driver-version fallback, for images that
  predate the helper.
- CI coverage of gVisor is manual where QA cannot run under it.

## What would reverse this

- gVisor stops being a possible runtime, or a template can choose its runtime; then option A
  becomes viable.
- gVisor exposes complete listener tables (tcp6 included) and the driver version under `/proc`; the
  visibility sentinel and the manifest fallback then become dead code to remove.
- `/run` can always hold the socket under gVisor; the socket fallback can then be retired.
- A gVisor release without an always-present `/proc/gvisor` is in use; the runtime check must then
  change.
- The sandbox switches to cgroup v2; re-check the thread-cap valve and the
  portal metrics against the v2 view inside the sandbox.
- A runc regression traced to any part of this contract voids that part until it is fixed.
