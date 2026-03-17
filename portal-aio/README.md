# Instance Portal (portal-aio)

The Instance Portal is a web-based management interface for Vast.ai GPU instances. It provides real-time service management, log viewing, system monitoring, and secure access tunneling — all through a single reverse-proxied web UI.

## Architecture

The portal consists of three cooperating services behind a Caddy reverse proxy:

```
Internet
  │
  ▼
Caddy (port 1111 external)    ← TLS termination, auth, reverse proxy
  ├── /                       → Portal        (127.0.0.1:11111)
  ├── /tunnel-manager/*       → Tunnel Mgr    (127.0.0.1:11112)
  └── /<app-ports>            → User apps     (127.0.0.1:*)
```

| Component | Port | Purpose |
|-----------|------|---------|
| **Portal** | 11111 | Main UI, log viewer, system metrics, supervisor control |
| **Tunnel Manager** | 11112 | Cloudflare quick tunnels and named tunnel management |
| **Caddy** | 1111 (external) | Reverse proxy with TLS, authentication, and compression |

All three are managed by supervisor and write logs to `/var/log/portal/`.

## Features

### Service Management

The portal integrates with supervisor via XML-RPC over a Unix socket to provide start/stop/restart control for all managed processes.

- Lists all supervisor processes with their current state (running, stopped, fatal, starting)
- Protected processes (`instance_portal`, `caddy`, `tunnel_manager`) cannot be stopped from the UI
- Additional unstoppable processes can be configured via `PORTAL_UNSTOPPABLE` env var
- Hides Jupyter when `/.launch` exists (Vast backend manages it directly)
- Hides processes with skip markers in `/tmp/supervisor-skip/{name}`

### Real-Time Log Viewer

The log viewer monitors all files in `/var/log/portal/` and streams updates to connected browsers via WebSocket.

**Terminal emulation:** The viewer maintains a virtual terminal state per log file, supporting:
- **ANSI colors** — SGR escape sequences are converted to HTML `<span>` elements with a color palette tuned for dark backgrounds
- **Progress bars** — `\r` (carriage return) overwrites the current line in-place, exactly like a real terminal
- **Nested progress bars** — `\x1b[A` (cursor-up) sequences allow tqdm-style multi-line progress displays
- **Block updates** — Lines modified by cursor-up are sent as atomic multi-line blocks to prevent flicker

**WebSocket protocol** (`/ws-logs`):

```json
{"type": "append", "html": "...", "file": "vllm.log"}
{"type": "overwrite", "html": "...", "file": "vllm.log"}
{"type": "overwrite_block", "lines": ["...", "..."], "file": "vllm.log"}
{"type": "system", "html": "..."}
```

Connections are kept alive with 10-second heartbeats. The client reconnects automatically with exponential backoff on disconnect.

**Other log features:**
- Pause/resume streaming
- Copy all logs to clipboard
- Download full `/var/log` directory as a zip file (`GET /download-logs`)

### Three-Tier Logging

Processes wrapped with the `pty` helper and `log-tee` produce three output streams:

| Destination | Content | Purpose |
|-------------|---------|---------|
| `/var/log/portal/<name>.log` | ANSI colors preserved, cursor/erase stripped, `\r` preserved | Portal log viewer |
| `/var/log/<name>.log` | All ANSI stripped, `\r` converted to `\n` | Human-readable clean log |
| stdout | Same as clean log | Vast.ai native log viewer |

This is handled by `log-tee` (for shell scripts via `logging.sh`) and `subprocess_runner.py` (for the Python provisioner). The `pty` wrapper (backed by `unbuffer -p`) gives child processes a real PTY so they enable progress bars, colored output, and other terminal features. Set `DISABLE_PTY=true` to disable the PTY wrapper at runtime (useful for debugging or environments where `unbuffer` causes issues).

### System Monitoring

`GET /system-metrics` returns live metrics, polled by the UI every few seconds.

**CPU** — Container-aware measurement via cgroups v2/v1, normalized by CPU quota for fractional core allocations. Falls back to `psutil` on bare metal.

**Memory** — Reads cgroup soft/hard limits to report container memory, not host memory. Filters out unrealistic sentinel values.

**GPU** — Detects both NVIDIA (via GPUtil) and AMD (via `rocm-smi`) GPUs. Reports per-GPU and aggregate utilization, VRAM usage, and GPU count.

**Disk** — Reports usage for the root filesystem and optionally a separate volume mount (shown as a distinct gauge in the UI).

### Application Dashboard

The main page displays cards for each configured application with:

- **Open buttons** — Direct links via public IP, Cloudflare tunnel, or SSH tunnel
- **Connection info** — Shows direct URL, tunnel URL, and internal port
- **Status indicators** — Cards dim when the backing supervisor process is stopped
- Auto-refresh every 30 seconds

### Tunnel Management

**Quick tunnels** — On-demand Cloudflare Argo tunnels created via `cloudflared`. Each application gets its own tunnel URL (`*.trycloudflare.com`). Tunnels can be created, stopped, and refreshed from the UI.

**Named tunnels** — When `CF_TUNNEL_TOKEN` is set, the tunnel manager connects to a Cloudflare account tunnel and reads its ingress configuration to map tunnel hostnames to internal ports.

**Direct URLs** — Maps `VAST_TCP_PORT_*` environment variables to `public_ip:port` for direct access without tunneling.

### Authentication & Security

Caddy enforces authentication on all proxied ports (unless excluded via `AUTH_EXCLUDE`).

**Token methods** (checked in order):
1. Query parameter: `?token=<token>`
2. Cookie: `{VAST_CONTAINERLABEL}_auth_token` (set on first successful auth, 7-day HttpOnly)
3. Bearer header: `Authorization: Bearer <token>`

**Dual tokens** — Both `WEB_PASSWORD` and `OPEN_BUTTON_TOKEN` are accepted. If not set, `OPEN_BUTTON_TOKEN` is auto-generated with `shortuuid`.

**HTTPS** — When `ENABLE_HTTPS=true` and valid TLS certificates exist at `/etc/instance.crt` and `/etc/instance.key`, Caddy terminates TLS and redirects HTTP to HTTPS.

**Auth exclusions** — Ports listed in `AUTH_EXCLUDE` (comma-separated) skip authentication entirely. Certain paths (`/.well-known/acme-challenge`, `/manifest.json`, `/portal-resolver`) are always unauthenticated.

## Configuration

### Portal Configuration (`/etc/portal.yaml`)

Generated by the Caddy config manager from `PORTAL_CONFIG` env var or written directly. Defines which applications appear in the UI.

```yaml
applications:
  Jupyter:
    hostname: localhost
    external_port: 8080
    internal_port: 18080
    open_path: /
    name: Jupyter
  vLLM API:
    hostname: localhost
    external_port: 8000
    internal_port: 18000
    open_path: /docs
    name: vLLM API
```

### PORTAL_CONFIG Format

Pipe-separated application definitions: `hostname:internal_port:external_port:open_path:Label`

```
localhost:18080:8080:/:Jupyter|localhost:18000:8000:/docs:vLLM API
```

### Environment Variables

#### Core

| Variable | Purpose | Default |
|----------|---------|---------|
| `PORTAL_CONFIG` | Application definitions (pipe-separated) | — |
| `VAST_CONTAINERLABEL` | Instance identifier, used for cookie names | Required |
| `VAST_TCP_PORT_*` | External port mappings (set by Vast platform) | Required |
| `PUBLIC_IPADDR` | Public IP for direct URLs | Auto-detected |
| `CONTAINER_ID` | Instance ID shown in UI | — |
| `WORKSPACE` | Workspace volume path | `/` |

#### Authentication

| Variable | Purpose | Default |
|----------|---------|---------|
| `ENABLE_AUTH` | Enable token authentication | `true` |
| `WEB_PASSWORD` | Auth token / basic auth password | Auto-generated |
| `OPEN_BUTTON_TOKEN` | Secondary auth token (used by Vast "Open" button) | Auto-generated |
| `WEB_USERNAME` | Basic auth username | `vastai` |
| `AUTH_EXCLUDE` | Comma-separated ports to skip auth | — |

#### HTTPS & Proxy

| Variable | Purpose | Default |
|----------|---------|---------|
| `ENABLE_HTTPS` | Enable TLS termination | `false` |
| `CADDY_ENABLE_COMPRESSION` | Enable zstd/gzip compression | `true` |
| `CADDY_FLUSH_INTERVAL` | HTTP flush interval for SSE | `-1` (immediate) |
| `CADDY_CORS_ALLOWED_ORIGINS` | CORS allowed origins | Disabled |
| `CADDY_HEADER_UP_LOCALHOST` | Forward Host header as localhost | — |

#### Tunnels

| Variable | Purpose | Default |
|----------|---------|---------|
| `CF_TUNNEL_TOKEN` | Cloudflare account tunnel token | — |
| `TUNNEL_TRANSPORT_PROTOCOL` | Cloudflare tunnel protocol | `http2` |
| `TUNNEL_MANAGER` | Tunnel manager URL | `http://localhost:11112` |
| `CLOUDFLARE_METRICS` | Cloudflare metrics endpoint | `localhost:11113` |

#### Process Management

| Variable | Purpose | Default |
|----------|---------|---------|
| `PORTAL_UNSTOPPABLE` | Additional protected process names (comma-separated) | — |

## API Reference

### Portal Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Main UI |
| GET | `/health` | Health check |
| GET | `/get-applications` | List apps with connection info |
| GET | `/system-metrics` | CPU, GPU, RAM, disk metrics |
| WS | `/ws-logs` | Real-time log stream |
| GET | `/download-logs` | Download `/var/log` as zip |
| GET | `/supervisor/processes` | List supervisor processes |
| POST | `/supervisor/process/{name}/{action}` | Start/stop/restart a process |
| GET | `/get-direct-url/{port}` | Get public IP:port URL |

### Tunnel Manager Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/get-quick-tunnel/{target_url}` | Create or return quick tunnel |
| GET | `/get-quick-tunnel-if-exists/{target_url}` | Return existing tunnel or 404 |
| GET | `/get-all-quick-tunnels` | List all active quick tunnels |
| GET | `/get-named-tunnels` | List named tunnel ingress entries |
| GET | `/get-named-tunnel/{port}` | Get named tunnel URL for port |
| POST | `/stop-quick-tunnel/{target_url}` | Stop a quick tunnel |
| POST | `/refresh-quick-tunnel/{target_url}` | Restart a quick tunnel |
| GET | `/get-direct-url/{port}` | Map VAST_TCP_PORT_* to public URL |

## File Structure

```
portal-aio/
├── launch.sh                          # Standalone launcher (legacy, pre-supervisor)
├── requirements.txt                   # Python dependencies
├── VERSION                            # Current version
├── portal/
│   ├── portal.py                      # Main FastAPI app
│   ├── templates/
│   │   └── index.html                 # Jinja2 template
│   └── static/assets/
│       ├── portal.js                  # Frontend application
│       ├── style.css                  # Theming and layout
│       └── favicon.png
├── tunnel_manager/
│   └── tunnel_manager.py             # Cloudflare tunnel management
└── caddy_manager/
    ├── caddy_config_manager.py        # Generates /etc/Caddyfile
    └── public/
        └── 502.html                   # Error page for unavailable backends
```

## Supervisor Integration

Each portal component has its own supervisor config in `/etc/supervisor/conf.d/`:

- `instance_portal.conf` — Runs `fastapi run portal.py` on port 11111
- `tunnel_manager.conf` — Runs `fastapi run tunnel_manager.py` on port 11112
- `caddy.conf` — Runs `caddy run --config /etc/Caddyfile`

All use `stdout_logfile=/dev/stdout` for Vast native logging and pipe through `log-tee` via `logging.sh` for the three-tier logging system.

## Frontend

The UI is a single-page application built with vanilla JavaScript (~2300 lines). It uses CSS custom properties for automatic light/dark mode theming via `prefers-color-scheme`.

**Pages** (hash-routed):
- `#/apps` — Application dashboard with launch buttons
- `#/tunnels` — Tunnel creation and management
- `#/supervisor` — Process start/stop/restart
- `#/logs` — Real-time log viewer
- `#/tools` — Links and utilities

**Stats sidebar** — Always-visible gauges for GPU utilization, VRAM, CPU, RAM, disk, and volume usage. Responsive layout collapses to a top bar on mobile.

## Development

The portal runs in a Python virtual environment at `/opt/portal-aio/venv`. To update dependencies:

```bash
cd /opt/portal-aio
./venv/bin/pip install -r requirements.txt
supervisorctl restart instance_portal tunnel_manager
```

Static assets are served directly by FastAPI — no build step required. Changes to `portal.js` or `style.css` take effect on browser refresh. Changes to Python files require a service restart.
