# Managing this instance (AI agent guide)

This is a Vast.ai GPU instance built from the Vast base image: an interactive ML
environment that can also expose web apps securely on an external interface.
Long-running processes are managed by **supervisor**; web apps are fronted by a
**Caddy** reverse proxy that adds TLS and token auth.

> Read **every** `*.md` file in `/etc/vast_agents/` for the complete picture.
> This `base.md` covers the base image; each additional file documents something
> this specific image adds. The files are cumulative — later files add to, and
> never repeat, this one.

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

## 7. Expose your own app, or add a managed service

To expose an app **externally** it must use one of the already-open ports above
that is **free** (`in_use: false`) — don't reuse a port a service already holds.
Caddy gives a service an authed external vhost only when its `external_port`
differs from its `internal_port` and `VAST_TCP_PORT_<external_port>` exists.

A) Put a new app behind the proxy (bind it to `127.0.0.1` on an internal port,
then map an open port to it and reload Caddy):
```
# app listening on 127.0.0.1:17070; 7070 is an open port (VAST_TCP_PORT_7070 set)
python -c "import yaml,sys; d=yaml.safe_load(open('/etc/portal.yaml')) or {'applications':{}}; \
d['applications']['My App']={'hostname':'localhost','external_port':7070,'internal_port':17070,'open_path':'/','name':'My App'}; \
yaml.safe_dump(d, open('/etc/portal.yaml','w'), sort_keys=False)"
supervisorctl restart caddy
# now reachable at $PUBLIC_IPADDR:$VAST_TCP_PORT_7070 (http unless ENABLE_HTTPS=true) with the token
```
This persists for the life of the instance; to survive a full reboot, also set
`PORTAL_CONFIG` at instance creation (or bake it via provisioning).

B) Run a long-lived managed service: add `/etc/supervisor/conf.d/<name>.conf`
and `/opt/supervisor-scripts/<name>.sh` (source `utils/logging.sh` and
`utils/environment.sh`, activate the venv), then `supervisorctl reread &&
supervisorctl update`. Log to `/dev/stdout` (`redirect_stderr=true`) or output
won't reach the portal/Vast logs. Expose it via step A if it serves web traffic.

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

## 12. GPU: CUDA toolkit, drivers, and rendering

### CUDA toolkit & driver matching

The NVIDIA driver comes from the host (libcuda is injected), so CUDA *compute*
works on every image. What differs is **which CUDA libraries are installed**, and
it varies a lot: the bare "stock" image ships none; the "mini"/cuda runtime base
ships only a **curated subset** (nvcc, cudart + dev headers, nvrtc, cuFFT, NPP,
nvJPEG, NCCL — but **not** cuBLAS, cuDNN, cuSPARSE, cuSOLVER, cuRAND); a full
`nvidia/cuda`-derived image ships everything. Don't assume — read the precise
inventory:
```
vast-capabilities | jq '.hardware.gpu.cuda'
# driver_version, driver_max_cuda, compute_capability, installed_version,
# cuda_home, forward_compat, and components{} — a per-library true/false map
vast-capabilities | jq '.hardware.gpu.cuda.components'   # exactly what's present
```
`components` tells you precisely what you have: e.g. `nvcc`/`dev_headers` true
means you can compile, but if `cublas`/`cudnn` are false you must install them
(from the configured nvidia apt repo — `cuda-<lib>-X-Y`, `libcudnn*`) or rely on a
framework wheel that bundles its own. Never assume a library is present because
`nvcc` is.

#### CUDA driver/toolkit compatibility (read this before "fixing" a mismatch)

Two distinct NVIDIA mechanisms — agents routinely conflate them:

- **Minor-version compatibility** ([docs](https://docs.nvidia.com/deploy/cuda-compatibility/)):
  a CUDA toolkit/wheel does **not** need to match the driver's exact version. Any
  CUDA **12.x** build runs on any driver that advertises CUDA **12.0+**; any 13.x
  build on a 13.0+ driver. So if `driver_max_cuda` is `13.0`, every CUDA 12.x and
  13.x wheel just works. **Do not "upgrade the driver to match CUDA"** — the driver
  is host-injected and must not be touched, and you almost never need to.
- **Forward compatibility** ([docs](https://docs.nvidia.com/deploy/cuda-compatibility/)):
  the separate `cuda-compat` libs, which let a **newer CUDA major** version run on
  an **older** driver, on **datacenter (Volta+) GPUs only**. These images
  **auto-enable** it at boot (`/etc/vast_boot.d/05-configure-cuda.sh` registers the
  compat dir with `ldconfig`) **only when actually needed** — i.e. an installed
  toolkit's major exceeds the host driver's max and the GPU supports it; otherwise
  it's left off. Check the live state, don't toggle it by hand:
  ```
  vast-capabilities | jq '.hardware.gpu.cuda.forward_compat'
  # {available, enabled, compat_driver_version, host_driver_version, ...}
  ```
  `enabled: false` with a host driver newer than `compat_driver_version` (as on most
  current boxes) is correct and expected. Opt out entirely with
  `DISABLE_FORWARD_COMPAT=true` at launch.

The one thing that genuinely constrains your wheel choice is the **GPU architecture**
(`compute_capability`), covered next — not the driver version.
Two hard rules when installing CUDA libraries (the nvidia/cuda apt repo is
configured, so this is easy to get wrong):
1. **Never install/upgrade the NVIDIA driver from apt** — the `cuda` metapackage
   pulls `cuda-drivers`, and `nvidia-driver-*`/`libcuda*` packages replace the
   host driver. It must match the host kernel module; replacing it breaks CUDA.
2. **Install only a CUDA toolkit ≤ `driver_max_cuda`** (shown above / by
   `nvidia-smi`). Use `cuda-toolkit-<X-Y>` (toolkit only), not `cuda`.

Easiest path: prefer framework wheels that bundle their own CUDA runtime
(`uv pip install torch` pulls a matching one) — then you need no system CUDA at
all. Use the system toolkit only when you need `nvcc`/dev headers. But the
bundled runtime must also be **new enough for this GPU** — see the gotchas below.

### Common GPU gotchas

These bite regardless of cloud provider; check the GPU before you install.

- **New GPUs need new CUDA — pin the wheel to the architecture, not just the
  driver.** Blackwell (RTX 50-series, B200; compute capability `10.0`/`12.0`) and
  newer need **CUDA ≥ 12.8** framework builds. An older build such as `torch`
  `cu124` *installs cleanly* but has no kernels for the new architecture and dies
  at first GPU op with `CUDA error: no kernel image is available for execution on
  the device`. Don't pin an old `cuXX` index out of habit. Check first:
  ```
  vast-capabilities | jq '.hardware.gpu.cuda'   # compute_capability, min_cuda_for_wheels, driver_max_cuda
  ```
  If `min_cuda_for_wheels` is set (e.g. `"12.8"`), install a matching or newer
  build — e.g. `uv pip install torch --index-url https://download.pytorch.org/whl/cu128`
  (or just the default index, which tracks current CUDA). The plain
  `pip install torch` default is usually current; an explicit old `--index-url` is
  the usual cause of this failure.
- **Toolkit must be ≤ the driver's max CUDA** (the inverse trap). The host driver
  caps the CUDA toolkit version you can install — see `driver_max_cuda` above and
  rule 2 in the section above.
- **A new GPU on an old driver.** Occasionally `driver_max_cuda` is *below* the
  GPU's needs; if even current wheels fail, the host driver is too old for the
  card — nothing you install in-container fixes that. Pick a different machine.
- **`nvidia-smi` works but CUDA tensors fail** is almost always one of the above
  (architecture/CUDA mismatch), **not** a broken GPU. `libGL`/OptiX/Vulkan errors
  are a *different* problem — see Rendering below.
- **Don't reinstall the driver to "fix" CUDA.** The driver is host-injected; see
  rule 1 above. Replacing it breaks compute for every workload on the box.

### Rendering (GL, OptiX, Vulkan)

Separately, some hosts install only the compute driver — so OpenGL/GLX/EGL,
OptiX, and Vulkan userspace libs can be missing, and you can't tell before renting. Symptoms: `libGL.so.1: cannot open shared object
file`, `libEGL.so.1: ...`, "OptiX not available", or `glxinfo`/`vulkaninfo`
failing while `nvidia-smi` and CUDA tensors work fine.

Check before assuming graphics work:
```
vast-capabilities | jq '.hardware.gpu.render'   # {gl, optix, vulkan} booleans (+ fix hint if missing)
```

If any are `false`, restore them (downloads + extracts the matching host-driver
libs into /opt/nvidia-drivers; needs root + internet, ~300 MB once):
```
install-display-drivers
```
Then restart whatever needs them. (The desktop image runs this automatically at boot.)

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
- Vast.ai platform docs: https://vast.ai/docs

