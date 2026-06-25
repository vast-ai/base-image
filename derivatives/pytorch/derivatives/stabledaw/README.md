# StableDAW Image

A StableDAW image derived from the Vast.ai [PyTorch image](../../README.md). It
ships [theDAW](https://github.com/gantasmo/theDAW) — an all-in-one, browser-based,
AI-powered digital audio workstation built on the Stable Audio 3 diffusion engine —
pre-installed, along with all features from the Vast.ai base image (Instance Portal,
Supervisor, Caddy proxy, Jupyter).

For detailed documentation on Instance Portal, Supervisor, environment variables, and
other features, see the [base image README](../../../../README.md).

> **Upstream note:** `gantasmo/stabledaw` is archived; active development is the fork
> [`gantasmo/theDAW`](https://github.com/gantasmo/theDAW), which this image pins. The
> repo ships no releases, so builds are pinned to a `main` commit (`STABLEDAW_REF`).

## Available Tags

Images are tagged `<commit>-<date>-cuda-<ver>` (e.g. `8b2cca1-2026-06-23-cuda-12.9`).
The tag's CUDA version is what the image was built against, not a host requirement —
see the base image's CUDA-compatibility notes.

## How it works

A single Supervisor service (`stabledaw`) runs `/opt/stabledaw-serve.py`, which serves
the pre-built React SPA at `/` and the FastAPI REST API under `/api/*` from one
process. It binds **`127.0.0.1:18600`**; the Instance Portal's Caddy proxy fronts it on
external port **8600** with authentication. The frontend is built at image-build time
(`npm run build`); the app source lives at `/opt/stabledaw`.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `STABLEDAW_DIR` | `/opt/stabledaw` | App source directory |
| `STABLEDAW_HOST` | `127.0.0.1` | Backend bind address (keep loopback) |
| `STABLEDAW_PORT` | `18600` | Backend bind port (internal) |
| `PORTAL_CONFIG` | see `05-stabledaw-env.sh` | Instance Portal tab + proxy map |

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| StableDAW | 8600 | 18600 |
| Jupyter | 8080 | 18080 |

**Bypassing authentication:** SSH-forward internal port 18600 to reach StableDAW
directly without the Caddy auth layer.

### Service Management

```bash
supervisorctl status stabledaw
supervisorctl restart stabledaw
supervisorctl tail -f stabledaw
```

## Notes

- The Medium model needs ~8 GB VRAM. Local-only by default — models download only when
  explicitly allowed in the UI.
- `flash-attn` is not installed (upstream declares it Windows-only); generation works
  unaccelerated. The optional Magenta RealTime sidecar is not vendored.
- Library/render data lives under `/opt/stabledaw/data` (overlay-persistent across
  stop/start; not on the shared `$WORKSPACE` volume).
