# ADR 0028 — The exposure verdict comes from declaration, not from process attribution

## Status

Accepted (conditional — see Binding conditions). **Supersedes ADR 0006 decision
step 2 and amends binding conditions 1 and 5.**

**Build status: shadow mode, 2026-08-24.** The scanner is implemented in
`ROOT/opt/instance-tools/tests/base/exposure_scan.py` and runs on every cell
alongside the legacy scan, printing its verdict and deciding nothing — binding
condition 3. It takes over the exit code after one full base promotion cycle across
all twelve configs plus the comfyui and vLLM gates. Binding conditions 1, 2 and 5
are discharged; 4 (derivative fallout for linux-desktop and UnrealPixelStreaming) is
what the shadow cycle is for.

## Date

2026-08-17

## Context

`ROOT/opt/instance-tools/tests/base/28-inadvertent-exposure.sh` is the
negative-direction security scan of ADR 0006, realizing ADR 0002 binding condition 1
at runtime: every public TCP listener must be Caddy (the auth gate) or declared in
`exposure-allowlist/`. `templates/base-qa/template.yml` sets
`EXPOSURE_ENFORCE: "true"`, so on the base QA gate a violation fails the build and
blocks promotion.

ADR 0006 decision step 2 says: **"no owning process (platform-injected) → warn,
never fail."** That rule rests on an inference — that a socket with no owning process
belongs to the platform — and the inference is false.

**Measured on a live instance** (rented from `vastai/base-image:cuda-12.6.3-auto`,
base-qa template, root shell; since destroyed):

```
CapEff=0xa80405fb   CAP_SYS_PTRACE=False   CAP_DAC_READ_SEARCH=False
ls /proc/783/fd          -> OK       (directory listing succeeds)
readlink /proc/783/fd/3  -> DENIED   (what `ss -p` actually needs)
readlink /proc/self/fd/0 -> OK       (control: same-uid succeeds)
```

`ss -p` attributes sockets by resolving `/proc/<pid>/fd/*`, which needs
`PTRACE_MODE_READ_FSCREDS` for a process of a different uid. Vast containers do not
grant `CAP_SYS_PTRACE`. So the scan — running as root — cannot attribute **any**
socket owned by a non-root process. Every service that drops privileges lands in the
`not proc and not pid` branch, is labelled "platform/injected? — unattributable",
and is downgraded from VIOLATION to WARN. WARNs do not set the return code, so the
enforced gate passes on exactly the listeners it cannot see.

Corroboration that this is the scanner and not the platform, repo-side and
GPU-free: `linux-desktop` runs **x11vnc** (`-rfbport 5900`, no `-localhost`) as
`user=user`, and `UnrealPixelStreaming` runs **coturn** as `user=user`. Those are
raw public listeners — one of them is the literal worked example in ADR 0006's own
allowlist documentation — and across the entire repo there is **exactly one**
allowlist fragment, `00-base.conf`. No derivative has ever written one. On the
measured instance every attributed service ran as root; the only unattributable ones
were syncthing's, including its **loopback** GUI on `127.0.0.1:18384`.

A second, independent defect surfaced in the same investigation and is entangled
with the sequencing. `ROOT/opt/supervisor-scripts/syncthing.sh:60` builds
`LISTEN_ADDR="tcp://0.0.0.0:${VAST_TCP_PORT_72299}"` with no guard. When that
variable is unset — true on any template that does not map port 72299, including
`base-qa` — it persists `tcp://0.0.0.0:` into syncthing's config, and syncthing
resolves the empty port to its default:

```
config.xml:    <listenAddress>tcp://0.0.0.0:</listenAddress>
syncthing.log: TCP listener starting (address="[::]:22000")
```

The allowlist keys the sync port on `env:VAST_TCP_PORT_72299`, which correctly
matches nothing when unset, so 22000 can never be allowlisted. Making unattributable
public TCP a violation today would therefore red the baseline on every base image.

Not currently reachable from the internet: Vast publishes only declared ports and an
external probe of 22000 timed out while the control port connected. But the port map
is **not** an independent control — on this platform a container binds the same
number inside and out, so a `0.0.0.0` bind on a *declared* port is internet-reachable
with no auth. Containment holds only for undeclared ports, which is not the case the
gate exists to catch.

## Options considered

### A. Attribution-free verdict — verdict = f(bind address, allowlist) (rejected as the whole answer, adopted as the core)

Delete the attribution term entirely; every public listener must be declared.

Rejected as a complete answer because it discards the one thing attribution was
genuinely buying: catching an app that binds a **Caddy front port** directly,
bypassing auth. Under a pure declaration rule, allowlisting 6006 passes whatever is
on 6006 — which `tests/README.md` already warns about in bold. Its verdict shape is
nonetheless correct and is adopted below.

### B. Recover attribution from `/proc/net` uid + inode (adopted)

`CAP_SYS_PTRACE` gates **pid** attribution only. `/proc/self/net/{tcp,tcp6,udp,udp6}`
are mode 0444 and expose the owning **uid** and socket **inode** of every listening
socket to any caller with no capabilities at all. Verified three ways: on a live
kernel by the design review's gate, by an architect running as an unprivileged uid
(the inversion of the container's case), and locally — `ss -ltnpe` on another uid's
socket prints `uid:1000 ino:108823317` with the process field absent. Note `ss -e`
prints `uid:` only when non-zero, so `/proc/net` is the more explicit source.

### C. Re-exec the scan per-uid under `setpriv` (rejected)

`setpriv --reuid=N ss -ltnp` would recover full pid/comm, since uid-matched readlinks
pass `ptrace_may_access` with no capability. Rejected as a verdict input: it puts
privilege-drop machinery inside a security scan, depends on target processes being
dumpable and on `setpriv` being present, and a failed drop must never become a
fail-open path. It buys message quality only. Retained as an optional, off-by-default
enrichment.

### D. Measure reachability from outside the instance (rejected now, recorded as the follow-on)

Probe `public_ipaddr:HostPort` from the QA harness and assert every reachable port is
authenticated or declared. This tests the property that actually matters and is
immune to attribution entirely; the harness already has the public IP and the port
map per cell.

Rejected as the answer to *this* defect because it certifies the **QA template's**
port map, not the customer's — base's real launch templates live Vast-side (an ADR
0010 exemption the template documents itself) — and because it cannot see an
unpublished public bind at all, which is the syncthing class of defect. It is
strictly additive and is recorded as the follow-on, with one idea worth keeping: a
deliberately **over-publishing** QA cell that maps every port the image is known to
bind, converting latent surface into realized surface on purpose.

### E. Per-boot cryptographic front identity (rejected)

Have Caddy echo an unforgeable per-boot nonce readable only by root. Rejected as
overkill today: it puts the gate on a portal-release dependency (ADR 0015), so any
derivative pinned to an older base reds every front port at once — a large false-red
mechanism aimed at a threat (unprivileged in-container port squatting) that is not
this gate's defect class. The right upgrade if squatting ever becomes real.

## Decision

**The verdict is `f(public bind, declaration)`. Attribution is diagnostic, not
decisive — with one exception, Caddy, which is confirmed behaviourally rather than
by identity.**

1. **Delete the `not proc and not pid` branch and the `warns` counter.** A public TCP
   listener that is neither a confirmed Caddy front nor declared is a VIOLATION,
   whatever can or cannot be attributed. This is the clause that supersedes ADR 0006
   decision step 2.

2. **Recover ownership from `/proc/self/net/*`** (uid + inode), cross-checked against
   `ss` for the listener set only (state/address, no `-p`, no `-e`). Ownership feeds
   the *message*, not the verdict: `owner=uid1000(user) ino=… no visible process
   (non-root in-container service)` instead of the misleading "platform/injected?".

3. **`foreign` becomes a narrow, named category** — uid 0 with no visible root
   process holding the inode — rather than a catch-all. It is reported, and it is
   still a violation unless declared. The platform's own injected listeners were
   always handled by *declaring* them (jupyter `8080` is in `00-base.conf`), never by
   exempting them.

4. **Caddy passes on behaviour, not identity.** A port passes as a Caddy front iff it
   is a Caddyfile site address **and** an unauthenticated request is challenged
   (401), or the port is declared auth-excluded. This is strictly stronger than the
   pid check, which only ever proved "caddy owns this socket", never "this front
   actually gates". It also removes a latent trap: Caddy is attributable today only
   because it runs as root, so an identity-based pass would flip every front port to
   violation the day Caddy is dropped to an unprivileged uid.

5. **Ephemeral UDP is suppressed by declaration, not by scanner behaviour.** A UDP
   socket is a `transient` note iff its port is inside
   `/proc/sys/net/ipv4/ip_local_port_range` **and** it is not in the published port
   map. Both predicates are required: a genuine fixed service can sit inside the
   ephemeral range (netbird's 51820 does), so the range test alone is wrong. The
   suppression exists because `00-base.conf` carries an `ephemeral/udp transient`
   line — remove the line and the noise returns as violations, so the exception stays
   greppable and reviewable.

6. **Two self-defence mechanisms, because a silent degradation is what produced this
   ADR.** If `pgrep -x caddy` returns a pid but no socket resolves to it, exit 2
   ("the scan decided nothing"). If `/proc/net` shows a listening socket the parser
   did not report, exit 2. Both are tooling failures, which ADR 0006 condition 6
   already makes non-advisory.

7. **The allowlist grammar gains** `env:VAR|literal` (coturn binds
   `${VAST_UDP_PORT_70000:-3478}`, which `env:` alone structurally cannot describe),
   `envlist:VAR` (for template-declared `AUTH_EXCLUDE`), `range:LO-HI`, and
   `ephemeral`; class gains `transient`. The scan prints the **resolved** allowlist
   before the verdicts, so an entry that silently resolved to nothing is visible
   rather than being an inexplicable red hours later. This amends ADR 0006 condition
   5, which fixed the fragment format before rollout — with exactly one fragment in
   existence, changing it is nearly free now and will not be later.

## Binding conditions

1. **The syncthing fix and its linter rule land together, before anything else.** The
   rule fires on the real `syncthing.sh`, so adding it alone reds the baseline, which
   the repo protocol says means STOP. Rule + fix in one PR, baseline clean at the end
   of it. Fixing the scan first would red the base gate; fixing syncthing first leaves
   the gate blind but no worse than today.
2. **The syncthing fix must reconcile, not add.** The malformed entry is persisted in
   `/opt/syncthing/config/config.xml` on overlayfs, which survives stop/start. The
   existing guard greps only for the *new* address, so the stale entry lives forever;
   worse, `tcp://0.0.0.0:` is a **prefix** of every well-formed `tcp://0.0.0.0:NNNN`,
   so the guard substring-collides regardless. Strip the malformed entry and fix the
   guard.
3. **The scan change ships in shadow mode for one full base promotion cycle** — new
   verdict printed, old verdict still driving the exit code — across all twelve
   configs plus the comfyui and vLLM gates, before it decides anything. This replaces
   "advisory forever", which ADR 0006's own amendment identified as a permanent green.
4. **Derivative fallout is budgeted, not discovered.** `linux-desktop` (x11vnc 5900)
   and `UnrealPixelStreaming` (coturn) will produce violations the moment they are
   visible. They stay advisory (no template change) until each has written its
   fragment and had a clean run. For the LLM engines, a public bind is a **finding to
   fix, not an allowlist entry** — ADR 0006's "what would reverse this" applies.
5. **Three false claims in the repo are corrected in the first PR**, because they
   currently document a guarantee that does not exist: the "false-red source is
   already handled" comment in `templates/base-qa/template.yml`, the
   "platform/injected?" message in the scan, and the "reasons from the live owning
   process" section of `ROOT/opt/instance-tools/tests/README.md`.
6. **Attribution must never be reintroduced as a verdict input** without superseding
   this ADR — including if Vast later grants `CAP_SYS_PTRACE`. The lesson is not
   "ptrace was missing"; it is that a security verdict resting on a capability the
   image does not control degrades silently.

## Consequences

**Positive.** The gate stops passing on the listeners it cannot see. Violation
messages name a uid and say plainly why the process is invisible, instead of blaming
the platform. Caddy's pass gets stronger, not weaker. The scan gains two ways to
report that it decided nothing. `EXPOSURE_ENFORCE` can eventually extend past base
with the derivative work costed up front.

**Accepted negative.** The allowlist becomes the sole pass path, so a typo is a false
red — mitigated by a grammar linter rule and by printing the resolved table.

**Accepted negative.** A genuine platform-injected public listener now reds base
promotion until someone adds one allowlist line. Disarming is a one-line template
revert with no rebuild, as ADR 0006's amendment already established.

**Accepted negative.** A `/proc/net` layout dependency, where a parsing bug fails
silently **open**. Mitigated by the `ss` cross-check and fixture tests, not
eliminated.

**Accepted negative (wart, stated rather than smoothed over).** TCP ignores
reachability — a public bind on an unpublished port is still a violation — while UDP
uses it. The asymmetry is justified only by `ss -lu` being unable to distinguish a
client socket from a server, and by TCP having a real `LISTEN` state where UDP does
not.

**Known gap, unchanged.** "Public" still means a wildcard bind. A listener on the
container's routable address (`172.17.0.5:9000`) is DNAT-reachable and invisible.
Widening this is a separate, evidence-first change.

**Known gap.** This certifies the QA template's surface. Base's customer templates
live Vast-side and drift from them is undetectable from this repo.

## What would reverse this

- Evidence that `/proc/self/net/*` does **not** expose the owning uid inside a Vast
  container — `hidepid`, a userns mapping that renders the uid meaningless, or an LSM
  masking `/proc/net`. One `head /proc/self/net/tcp` from a fresh base-qa instance
  settles it, and it should be the first thing the build does.
- The behavioural Caddy check proving flaky in the shadow run. The honest response is
  option **E** (per-boot nonce), not loosening the check.
- The allowlist growing to the point where it is a rubber stamp rather than a
  declaration — at which point the external probe of option **D** becomes the real
  gate and this becomes defence in depth.
- Vast granting `CAP_SYS_PTRACE`, which would restore pid attribution as
  *enrichment*. Per binding condition 6, that is not a reason to put it back in the
  verdict.
