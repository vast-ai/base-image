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

Lists installed tools, python environments, hardware, open external ports, every
service (with reachable URL and state), OpenAI `/v1` endpoints, provisioning, and
the auth model. Equivalents: `/etc/vast_capabilities.json` (static snapshot),
`curl -s http://localhost:11111/capabilities`, `…/openapi.json` (full REST API).

## 2. Python

```
source /venv/main/bin/activate    # default Python env at /venv/main (or /venv/$ACTIVE_VENV)
uv pip install <pkg>              # fast installs; conda/mamba also available
```

System `python3` and `node`/`npm` (via nvm) are also on PATH after login.

## 3. Storage — what persists

The whole container filesystem (overlayfs) persists across a normal instance
stop/start — installed packages, the home dir, and venvs all survive a restart.
It is lost only if the instance is **destroyed**.

A **volume** is optional separate storage, usually mounted at `${WORKSPACE}`
(default `/workspace`). It lives on the physical host, survives instance
destruction, and can be mounted by several instances on that machine at once — so
it's the place for data you want to keep beyond a single instance or share
between them. Not every instance has one; check whether `${WORKSPACE}` is a
distinct mount (and treat it as shared — other instances may be writing to it):

```
vast-capabilities metrics | jq '.hardware.volume'   # present only when a volume is mounted
```

Put models, datasets, and code you care about under `${WORKSPACE}`; `HF_HOME`
defaults to `${WORKSPACE}/.hf_home`. Launching with `--sync-home` /
`--sync-environment` copies home dirs / venvs onto the volume so they too persist
beyond the instance.

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
at runtime**. See what you have:

```
vast-capabilities | jq '.instance.open_ports'   # proto, container_port, public_port
env | grep -E 'VAST_(TCP|UDP)_PORT_'             # the raw mappings
```

## 7. Expose your own app, or add a managed service

To expose an app **externally** it must use one of the already-open ports above.
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

## 12. Manage the instance itself

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

## 13. Further reference (in-container docs)

- Provisioning manifest schema: `/opt/instance-tools/lib/provisioner/README.md`
- Adding supervisor services (wrapper-script conventions): `/opt/supervisor-scripts/README.md`
- Portal architecture, tunnels, auth, full API: `/opt/portal-aio/README.md`
- Live REST schema: `http://localhost:11111/openapi.json`
- Vast.ai platform docs: https://vast.ai/docs

