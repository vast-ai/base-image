# Managing this instance (AI agent guide)

This is a Vast.ai GPU instance built from the Vast base image: an interactive ML
environment that can also expose web apps securely on an external interface.
Long-running processes are managed by **supervisor**; web apps are fronted by a
**Caddy** reverse proxy that adds TLS and token auth.

> Read **every** `*.md` file in `/etc/vast_agents/` for the complete picture.
> This `base.md` covers the base image; each additional file documents something
> this specific image adds. The files are cumulative — later files add to, and
> never repeat, this one.

**Environment & privileges.** This is an **unprivileged Docker container**, not a
VM. You are `root` (or a user with passwordless `sudo`, provided only for programs
that refuse to run as root) — but this is *not* full root: you **cannot** load
kernel modules, run another container engine (no Docker-in-Docker), use kernel
profilers (`perf`, eBPF), mount block devices, or change sysctls/cgroups. The host
kernel is shared and read-only to you. Prefer working *within* these limits — e.g.
manage long-running services with **supervisor** (§4/§7) instead of Docker, and use
userspace profilers. If a task genuinely needs kernel-level access, Vast also
offers **VM instances**; that's the exception, not the default.

## 1. Orient yourself

```
vast-capabilities                  # full live manifest as JSON
vast-capabilities metrics,packages # also include live CPU/GPU/RAM + package versions
```

Lists the image identity (`.image` — name + source repo/README, so you can check
what's preinstalled), installed tools, python environments, hardware, open
external ports (with `in_use` flags), every service (with reachable URL and
state), OpenAI `/v1` endpoints, configured credentials (`.credentials` —
presence of HF/Civitai tokens, rclone config; values never shown), provisioning,
and the auth model. Equivalents: `/etc/vast_capabilities.json` (static snapshot),
`curl -s http://localhost:11111/capabilities`, `…/openapi.json` (full REST API).

## 2. Python

```
source /venv/main/bin/activate    # default Python env at /venv/main (or /venv/$ACTIVE_VENV)
uv pip install <pkg>              # fast installs; conda/mamba also available
```

**Node.js / npm** are installed via **nvm** at `/opt/nvm`, but only land on `PATH`
in an *interactive login shell* (`.bashrc` sources nvm). A non-interactive command
— e.g. `ssh host 'node ...'`, a script, or a supervisor service — will **not** find
`node` unless you load nvm first:
```
. /opt/nvm/nvm.sh && node --version      # puts node/npm/npx on PATH in any shell
```
Or call the binary directly: `/opt/nvm/versions/node/$(ls /opt/nvm/versions/node | tail -1)/bin/node`.
System `python3` is always on `PATH`.

## 3. Storage — what persists

- **stop/start** preserves **everything** — the whole container filesystem comes back intact.
- **recycle** (container rebuilt from the image) or **destroy** (instance removed) **wipes the container filesystem**. The only thing that survives is a mounted host **volume**.

**`${WORKSPACE}` (default `/workspace`) is NOT automatically persistent.** It is
merely *where a volume is mounted if this instance has one* — and many instances
have no volume, in which case `/workspace` is ordinary container storage, lost on
recycle/destroy like everything else. Never assume it survives; check:

```
vast-capabilities | jq '.instance.workspace_is_volume'   # true only if backed by a host volume
```

- **`true`** → `${WORKSPACE}` is a host volume: it persists through recycle and destroy, and may be **shared** with other instances on the same machine (expect concurrent writers). Keep irreplaceable data here.
- **`false`** → **nothing on this instance survives recycle/destroy.** Sync anything you can't lose off-box (rclone, syncthing, Hugging Face Hub, git).

`HF_HOME` defaults to `${WORKSPACE}/.hf_home`. `--sync-home` / `--sync-environment`
at launch help only when a volume is present.

## 4. Services: how they are wired

Supervisor runs each service; Caddy exposes the web ones. The chain is:

- `PORTAL_CONFIG` (env var) is the **boot-time seed**, format
  `hostname:external_port:internal_port:path:label` (pipe-separated entries).
- On first boot it is written to **`/etc/portal.yaml`**, which is the
  **runtime source of truth** (there is no `/etc/portal.conf`).
- Caddy reads `/etc/portal.yaml` and proxies each app.

Inspect and control:
```
supervisorctl status                       # what's running
supervisorctl restart <name>               # restart / stop / start
curl -s http://localhost:11111/capabilities/services   # services + URLs + state
```
A service is hidden/disabled by removing its entry from `/etc/portal.yaml` (it
then self-skips on next start via a `/tmp/supervisor-skip/<name>` marker). Don't
stop `caddy`, `instance_portal`, or `tunnel_manager` — they are the management
and auth surface (the portal blocks this, but `supervisorctl` will not).

**Jupyter is the exception.** When the instance is launched in Jupyter mode from
the Vast interface, Jupyter is started and managed by the **Vast platform, not
supervisor** (signalled by `jupyter` in `/.launch`); it won't show under
`supervisorctl` and the portal hides it — don't try to control it there. Only in
entrypoint mode (no `/.launch`) does supervisor manage Jupyter. Either way the
on-PATH `jupyter` is a thin wrapper (`/opt/instance-tools/bin/jupyter`) that just
adds startup options (root dir `/`, preferred dir `$WORKSPACE`); the config is
otherwise upstream.

## 5. Reach a service from outside the container

A service's `external_port` label (e.g. `8000`) is **not** the host port — the
host-reachable port is `VAST_TCP_PORT_<external_port>`. Each service in the
manifest already carries a ready-to-use `direct_url`. Get the token and call it:

```
TOKEN=$OPEN_BUTTON_TOKEN          # or $WEB_PASSWORD; both are accepted
# scheme is http by default — https only when ENABLE_HTTPS=true (self-signed cert → add -k)
curl -H "Authorization: Bearer $TOKEN" http://$PUBLIC_IPADDR:$VAST_TCP_PORT_8000/<path>
```
Prefer the `direct_url` straight from the manifest — it already has the correct
scheme and mapped port for this instance.

Auth (Caddy edge) accepts the token three ways: `Authorization: Bearer <token>`,
`?token=<token>`, or the `${VAST_CONTAINERLABEL}_auth_token` cookie. Basic-auth
user is `vastai` (`$WEB_USERNAME`). Requests to a service's internal port over
`localhost` bypass the edge and need no token. Disable auth with
`ENABLE_AUTH=false`; exempt ports with `AUTH_EXCLUDE`.

**Cloudflare tunnels** (alternative to direct ports). `cloudflared` is installed
(`/opt/instance-tools/bin/cloudflared`). The instance makes a **best-effort**
attempt to open a Cloudflare *quick tunnel* (`*.trycloudflare.com`) for each
exposed service at startup — list the current ones:
```
curl -s http://localhost:11111/get-all-quick-tunnels   # [{"targetUrl":...,"tunnelUrl":"https://*.trycloudflare.com"}]
```
Quick tunnels point at the Caddy port, so the **token auth above still applies**.
They are ephemeral, rate-limited, and lost on restart — don't depend on them.

For a stable address on **your own domain**, pass `CF_TUNNEL_TOKEN` (a Cloudflare
tunnel token) at launch; configure the ingress — and any Cloudflare private
network / WARP routing — in the Cloudflare Zero Trust dashboard. A named tunnel's
ingress routes straight to the app's internal port, **bypassing Caddy auth**, so
secure it in your app or with Cloudflare Access. Look up a named-tunnel URL by the
internal port its ingress targets:
```
curl -s http://localhost:11111/get-existing-named-tunnel/<internal_port>
```

## 6. Available external ports (fixed at creation)

External ports are allocated when the instance is created — **you cannot add more
at runtime**. See what you have, and which are free:

```
vast-capabilities | jq '.instance.open_ports'              # all open ports
vast-capabilities | jq '.instance.open_ports[]|select(.in_use==false)'   # free ones
```

Each entry has `container_port`, `public_port`, and `in_use` (plus the occupying
`service` when taken). To expose your own app, pick one where `in_use` is `false`.

**Two kinds of port — bind to the right number:**
- **Normal** (`container_port` ≤ 65535): the engine NATs a *different* external
  number to it. Your app binds inside the container to `container_port`; the public
  side is `public_port` (= `VAST_TCP_PORT_<container_port>`). These can go behind
  the Caddy auth edge (§7).
- **Self-mapped** (`container_port` > 65535 — a port requested `> 70000`, like
  syncthing's default `72299`): usable, but the > 65535 number is only a *request
  token*, not bindable. The engine forwards a random port **1:1**, so it is the **same
  number inside and out** — read it from **`$VAST_TCP_PORT_<container_port>`** (or
  `$VAST_UDP_PORT_<container_port>` for UDP), bind your app to that, and it's reachable
  at `$PUBLIC_IPADDR:$VAST_TCP_PORT_<container_port>` (the manifest also gives it as
  `bind_port`). **Direct forward — no Caddy, no token auth** (add your own), but serves
  **any protocol, HTTP included**. Example: syncthing binds `tcp://0.0.0.0:${VAST_TCP_PORT_72299}`.

**Choosing:** token-authed HTTP → a **Normal** port behind Caddy (§7). Raw TCP/UDP, an
app that advertises its own address, self-authed HTTP, or no free normal port →
**self-mapped**. Need both? Request some of each at creation.

## 7. Expose your own app (run it as a managed service)

**Run your app as a supervisor service — don't just launch it loose.** A bare
`python app.py &` dies when your shell exits, won't restart on crash, and its logs
never reach the portal/Vast. The instance's own services follow one pattern; yours
should too. It's two files plus a Caddy entry.

**Wiring a well-known app (ComfyUI, etc.)?** Don't reverse-engineer it — the base-image
repo (https://github.com/vast-ai/base-image) ships derivative images that already run
these behind the portal: PyTorch apps under `derivatives/pytorch/derivatives/<app>`,
others under `derivatives/<app>`, non-base apps under `external/<app>`. Each is a working
template for the supervisor script + `portal.yaml` entry — copy its pattern. Ready-made
**provisioning manifests** (§10) live in `provisioning/` (generic) and per app, e.g.
`derivatives/pytorch/derivatives/comfyui/provisioning/`.

**Step 1 — wrapper script** `/opt/supervisor-scripts/<name>.sh`. Source the shared
utils (env + logging + the portal skip-guard) and run your app **in the
foreground** on a `127.0.0.1` internal port:
```bash
#!/bin/bash
utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/environment.sh"                 # exports /etc/environment + ${WORKSPACE}/.env
. "${utils}/exit_portal.sh" "My App"        # self-skips if "My App" isn't in /etc/portal.yaml

source /venv/main/bin/activate              # if it needs the python env
cd "${WORKSPACE}"
pty my-app --host 127.0.0.1 --port 17070 2>&1   # foreground; pty flushes output to logs
```
Make it executable (`chmod +x`).

**Step 2 — supervisor config** `/etc/supervisor/conf.d/<name>.conf`. Model it on an
existing one (`/etc/supervisor/conf.d/tensorboard.conf`); the essentials:
```ini
[program:myapp]
environment=PROC_NAME="%(program_name)s"
command=/opt/supervisor-scripts/myapp.sh
autostart=true
autorestart=unexpected
stdout_logfile=/dev/stdout          # required so logs reach the portal/Vast
redirect_stderr=true
stdout_logfile_maxbytes=0
```
Then load it: `supervisorctl reread && supervisorctl update` (later:
`supervisorctl restart myapp`, logs at `/var/log/portal/myapp.log`).

**Step 3 — expose it externally.** Pick a **free** open port (`in_use: false`, §6 —
don't reuse one a service holds).

*If the only free port is `self_mapped` (§6):* no Caddy path — bind your app to its
`bind_port` (= `$VAST_TCP_PORT_<container_port>`, the script's `--port`); it's reachable
at `$PUBLIC_IPADDR:$VAST_TCP_PORT_<container_port>` with **no token auth** (enforce your
own). Skip the portal.yaml step below.

*Otherwise (a normal free port), put it behind the Caddy auth edge:* add an entry to
`/etc/portal.yaml`; the label must match the `exit_portal.sh` term above. Caddy only
grants an authed external vhost when `external_port` ≠ `internal_port` and
`VAST_TCP_PORT_<external_port>` exists:
```
# app on 127.0.0.1:17070; 7070 is a free open port (VAST_TCP_PORT_7070 set)
python -c "import yaml; d=yaml.safe_load(open('/etc/portal.yaml')) or {'applications':{}}; \
d['applications']['My App']={'hostname':'localhost','external_port':7070,'internal_port':17070,'open_path':'/','name':'My App'}; \
yaml.safe_dump(d, open('/etc/portal.yaml','w'), sort_keys=False)"
supervisorctl restart caddy
# now reachable at $PUBLIC_IPADDR:$VAST_TCP_PORT_7070 (http unless ENABLE_HTTPS=true) with the token (§5)
```
This lasts the life of the instance. To survive a **recycle/reboot** too, bake both
files in via provisioning (§10) and set `PORTAL_CONFIG` at instance creation.

**No free port — or you just need to reach it yourself?** Don't burn a port or a
public tunnel. **SSH local forwarding** reaches any `127.0.0.1`-bound internal port
privately, with no open port, no token, and full SSH encryption/auth — the most
secure option for single-user access. From the user's own machine:
```
# forward local :8080 -> the app on 127.0.0.1:17070 inside the container
ssh -p $VAST_TCP_PORT_22 -L 8080:127.0.0.1:17070 root@$PUBLIC_IPADDR
# then browse http://localhost:8080 locally
```
(Use the instance's real SSH host/port — `$VAST_TCP_PORT_22` on `$PUBLIC_IPADDR`.)
Because it lands on `localhost` inside the container it bypasses Caddy entirely, so
no token is needed and nothing is exposed publicly. Cloudflare quick tunnels (§5)
are the alternative when the user *can't* SSH (e.g. sharing a link).

(For a genuine throwaway test only, you can skip the service and run the app loose
on the internal port — but it won't restart or survive, so don't leave anything
real that way.)

## 8. Persistent environment variables

Write to **`${WORKSPACE}/.env`** — it is sourced by both login shells and
supervisor services. Restart the affected service to pick it up:
```
echo 'MY_VAR="value"' >> ${WORKSPACE}/.env
supervisorctl restart <service>
```

## 9. OpenAI-compatible inference endpoints

If this image runs an inference server (see the other files in `/etc/vast_agents/`):
```
curl -s http://localhost:11111/capabilities/endpoints
```
Each entry gives the externally callable `base_url`, `capabilities`, and auth.
Call it like the OpenAI API (`POST {base_url}/chat/completions`; models at
`{base_url}/models`).

## 10. Install more dependencies (provisioning)

```
curl -XPOST http://localhost:11111/capabilities/provision -d '{"pip":["<pkg>"]}'
# or, declaratively:
provisioner <manifest.yaml>
```
Boot-time alternative: set `PROVISIONING_MANIFEST=<url>` or the `PROVISIONING_*`
env vars. Status flags: `/.provisioning` (running), `/.provisioning_complete`,
`/.provisioning_failed`; progress in `/var/log/portal/provisioning.log`. To force
a re-run, delete `/.provisioning_complete` and re-invoke the provisioner. Some
services are configured to wait for provisioning, so during boot they stay down
until `/.provisioning_complete` exists — check that flag before assuming a
service is broken.

## 11. Logs and metrics

```
curl -s http://localhost:11111/system-metrics      # CPU, GPU, RAM, disk
ls /var/log/portal/                                 # per-service logs
tail -f /var/log/portal/<service>.log
```

## 12. GPU: CUDA, drivers, and rendering

### What CUDA is installed (don't assume)

The NVIDIA driver is host-injected (libcuda), so CUDA *compute* works on every
image. What varies is **which CUDA libraries ship**: the bare "stock" image has
none; the "mini"/cuda runtime base has a **curated subset** (nvcc, cudart + dev
headers, nvrtc, cuFFT, NPP, nvJPEG, NCCL — but **not** cuBLAS, cuDNN, cuSPARSE,
cuSOLVER, cuRAND); a full `nvidia/cuda`-derived image has everything. Read the
precise inventory rather than assuming one lib exists because `nvcc` does:
```
vast-capabilities | jq '.hardware.gpu.cuda'
# driver_version, driver_max_cuda, compute_capability, installed_version,
# cuda_home, forward_compat, and components{} — a per-library true/false map
```
If `components` shows `cublas`/`cudnn` false, install them (nvidia apt repo:
`cuda-<lib>-X-Y`, `libcudnn*`) or use a framework wheel that bundles its own.

### Driver/toolkit compatibility (read before "fixing" a mismatch)

Two distinct NVIDIA mechanisms agents routinely conflate ([docs](https://docs.nvidia.com/deploy/cuda-compatibility/)):

- **Minor-version compatibility:** a toolkit/wheel need **not** match the driver's
  exact version. Any CUDA **12.x** build runs on any driver advertising CUDA
  **12.0+** (13.x on 13.0+). So if `driver_max_cuda` ≥ 12.0, every 12.x wheel just
  works — **do not "upgrade the driver to match CUDA"** (the driver is host-injected
  and must not be touched).
  - **PTX-JIT caveat (the one that catches people out):** this covers the runtime/
    libraries but **not PTX JIT**. The PTX→SASS compiler lives in the *driver*, so
    code shipped as **PTX** from a toolkit *newer* than the host driver fails with
    `CUDA_ERROR_UNSUPPORTED_PTX_VERSION (222)` / "PTX compiled with an unsupported
    toolchain" — even though the runtime loaded. Only bites when the host driver is
    *older* than the build. Fix with native-cubin builds (most wheels), forward
    compat (below), or a toolkit ≤ the host driver.
- **Forward compatibility:** the separate `cuda-compat` libs let a **newer CUDA
  major** run on an **older** driver, on **datacenter (Volta+) GPUs only**. These
  images **auto-enable** it at boot (`05-configure-cuda.sh` registers it via
  `ldconfig`) **only when needed** (installed toolkit major > host driver max, on a
  capable GPU). Check, don't toggle by hand:
  ```
  vast-capabilities | jq '.hardware.gpu.cuda.forward_compat'   # {available, enabled, ...}
  ```
  `enabled: false` when the host driver is already new enough is correct. Opt out
  with `DISABLE_FORWARD_COMPAT=true` at launch.

### Two hard rules when installing CUDA libs

The nvidia/cuda apt repo is configured, so this is easy to get wrong:

1. **Never install/upgrade the NVIDIA driver from apt** — the `cuda` metapackage
   pulls `cuda-drivers`, and `nvidia-driver-*`/`libcuda*` replace the host driver
   (which must match the host kernel module). Reinstalling the driver to "fix" CUDA
   breaks compute for everything on the box.
2. **Install only a CUDA toolkit ≤ `driver_max_cuda`**; use `cuda-toolkit-<X-Y>`
   (toolkit only), not `cuda`.

Easiest path: prefer framework wheels that bundle their own CUDA runtime — then you
need no system CUDA. But the bundled runtime must be **new enough for this GPU**:

### GPU architecture is the real wheel constraint (Blackwell / cu124)

`compute_capability`, not the driver version, decides which build you need.
Blackwell (RTX 50-series, B200; cc `10.0`/`12.0`) and newer need **CUDA ≥ 12.8**
wheels. An older build like `torch cu124` *installs cleanly* but has no kernels for
the arch and dies at the first GPU op with `no kernel image is available for
execution on the device`. The manifest flags this:
```
vast-capabilities | jq '.hardware.gpu.cuda'   # compute_capability, min_cuda_for_wheels
```
If `min_cuda_for_wheels` is set (e.g. `"12.8"`), use a matching/newer build —
`uv pip install torch --index-url https://download.pytorch.org/whl/cu128`, or just
the default index (which tracks current CUDA). An explicit *old* `--index-url` is
the usual cause of this failure.

Two related traps: a **new GPU on too-old a driver** (`driver_max_cuda` below the
card's needs — nothing in-container fixes it, pick another machine); and
**`nvidia-smi` works but CUDA tensors fail**, which is almost always an arch/CUDA
mismatch above, *not* broken hardware (`libGL`/OptiX/Vulkan errors are different —
see Rendering).

### Rendering (GL, OptiX, Vulkan)

Some hosts install only the compute driver, so OpenGL/GLX/EGL, OptiX, and Vulkan
userspace libs can be missing (undetectable before renting). Symptoms:
`libGL.so.1: cannot open shared object file`, `libEGL.so.1: ...`, "OptiX not
available", or `glxinfo`/`vulkaninfo` failing while `nvidia-smi` and CUDA work.
```
vast-capabilities | jq '.hardware.gpu.render'   # {gl, optix, vulkan} (+ fix hint if missing)
```
If any are `false`, run `install-display-drivers` (extracts the matching host-driver
libs into /opt/nvidia-drivers; root + internet, ~300 MB once), then restart whatever
needs them. (The desktop image runs this automatically at boot.)

## 13. Manage the instance itself

The `vastai` CLI can manage *this* instance from within, authenticated by the
Vast-set `CONTAINER_API_KEY` (the CLI is not pre-authenticated — pass it
explicitly):
```
vastai show instance $CONTAINER_ID --api-key $CONTAINER_API_KEY   # this instance's state/config
vastai stop instance $CONTAINER_ID --api-key $CONTAINER_API_KEY   # halts GPU charges (storage is still billed)
```
`stop` halts GPU charges but the instance and its disk remain — **storage is
still billed**. `destroy` deletes the instance entirely. Use both deliberately.
See `vastai --help` for the full command set.

## 14. Further reference (in-container docs)

- Provisioning manifest schema: `/opt/instance-tools/lib/provisioner/README.md`
- Adding supervisor services (wrapper-script conventions): `/opt/supervisor-scripts/README.md`
- Portal architecture, tunnels, auth, full API: `/opt/portal-aio/README.md`
- Live REST schema: `http://localhost:11111/openapi.json`
- Reference images for running known apps (ComfyUI, etc.) behind the portal:
  https://github.com/vast-ai/base-image (layout and use in §7)
- Vast.ai platform docs: https://vast.ai/docs

