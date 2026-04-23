# Whisper Image

A Whisper-WebUI image derived from the Vast.ai [PyTorch image](../../README.md). This image ships **both** the Whisper-WebUI Gradio UI and its FastAPI backend, each managed as an independent Supervisor service, along with all features from the Vast.ai base image.

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../../../README.md).

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/whisper/tags).

## CUDA Compatibility

Images are tagged with the CUDA version they were built against (e.g. `<ref>-<date>-cuda-12.9`). This does not mean you need that exact CUDA version on the host.

### Minor Version Compatibility

NVIDIA's [minor version compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html) guarantees that an application built with any CUDA toolkit release within a major version family will run on a system with a driver from the same major family.

- A `cuda-12.9` image will run on any machine with a CUDA 12.x driver (driver >= 525).
- A `cuda-13.1` image will run on any machine with a CUDA 13.x driver (driver >= 580).
- The 12.x and 13.x families are separate.

**PTX caveat:** Applications that compile device code to PTX rather than pre-compiled SASS for the target architecture will not work on older drivers within the same major family.

### Forward Compatibility

[Forward compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html) allows newer CUDA toolkit versions to run on older drivers. It is only available on **datacenter GPUs** (and select NGC Server Ready RTX cards). All of our images include the CUDA Compatibility Package (`cuda-compat`) to support this.

## Whisper Configuration

### Services

| Service | Description |
|---------|-------------|
| `whisper-api` | FastAPI backend (`uvicorn backend.main:app`) |
| `whisper-ui` | Gradio WebUI (`python app.py`) — waits for the API before starting |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Directory for models, outputs, and configurations |
| `WHISPER_API_ARGS` | `--host 0.0.0.0 --port 8000` | uvicorn args |
| `WHISPER_UI_ARGS` | `--whisper_type whisper --server_port 7860` | Gradio app.py args |
| `WHISPER_API_HEALTH_URL` | `http://localhost:8000/docs` | URL the UI polls before starting |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| Whisper WebUI | 17860 | 7860 |
| Whisper API | 18000 | 8000 |
| Jupyter | 8080 | 8080 |

### Service Management

```bash
# Check service status
supervisorctl status whisper-api whisper-ui

# Restart a service
supervisorctl restart whisper-api
supervisorctl restart whisper-ui

# Run UI only: remove the Whisper API entry from /etc/portal.yaml, then restart both
# (the API exits via exit_portal.sh; the UI detects it's not configured and starts solo)
supervisorctl restart whisper-api whisper-ui
```

Removing a service's display name from `/etc/portal.yaml` causes its Supervisor script to exit on start (via `exit_portal.sh`), so you can run the API alone, the UI alone, or both.

## Building From Source

```bash
cd base-image/derivatives/pytorch/derivatives/whisper

docker buildx build \
    --build-arg PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312 \
    --build-arg WHISPER_REF=v1.0.8 \
    -t yournamespace/whisper .
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `PYTORCH_BASE` | `vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py312-2026-04-15` | PyTorch mini base image |
| `WHISPER_REPO` | `https://github.com/jhj0517/Whisper-WebUI` | Upstream repo |
| `WHISPER_REF` | | Git ref (release tag e.g. `v1.0.8`) to build |

## Building with GitHub Actions

Fork this repository and use the included GitHub Actions workflow to build and push Whisper images to your own DockerHub account. The workflow polls the upstream GitHub repo for new release tags every 12 hours and rebuilds automatically.

## Licenses

This image ships vendor application(s) under the following license(s):

- **Whisper-WebUI** — Apache-2.0 ([upstream](https://github.com/jhj0517/Whisper-WebUI))
- **OpenAI Whisper** — MIT ([upstream](https://github.com/openai/whisper))

See `/LICENSES.md` in the image for license details and file locations.

## Useful Links

- [Whisper-WebUI GitHub](https://github.com/jhj0517/Whisper-WebUI)
- [OpenAI Whisper](https://github.com/openai/whisper)
- [PyTorch Image Documentation](../../README.md)
- [Base Image Documentation](../../../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/whisper)
