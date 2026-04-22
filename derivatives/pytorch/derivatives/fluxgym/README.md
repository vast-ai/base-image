# FluxGym Image

A FluxGym image derived from the Vast.ai [PyTorch image](../../README.md). This image ships FluxGym pre-installed alongside Kohya `sd-scripts` (sd3 branch), ready for FLUX LoRA training through a simple Gradio UI.

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../../../README.md).

## Available Tags

Pre-built images are available on [DockerHub](https://hub.docker.com/repository/docker/vastai/fluxgym/tags).

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

## Python Version

The FluxGym image is built on the **Python 3.10** variant of the PyTorch mini base (`*-mini-py310-*`). FluxGym and Kohya sd-scripts are validated against Python 3.10 and newer interpreters have occasionally surfaced incompatibilities in the wider training-tools ecosystem.

## Pinned Dependencies

The following pins are load-bearing — changing them will typically break training or auto-captioning:

| Package | Pin | Reason |
|---------|-----|--------|
| `transformers` | `==4.49.0` | Later versions regress florence-2 auto-caption output |
| `peft` | `<0.18` | 0.18+ breaks the LoRA injection path used by sd-scripts sd3 branch |
| `torch-backend` | `cu128` | Matches FluxGym's upstream `gpu.txt` |

## FluxGym Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Directory for models, datasets, and training outputs |
| `FLUXGYM_PORT` | `17860` | Gradio server port (internal) |
| `PROVISIONING_SCRIPT` | (none) | URL to a setup script to run on first boot |

### Port Reference

| Service | External Port | Internal Port |
|---------|---------------|---------------|
| Instance Portal | 1111 | 11111 |
| FluxGym | 17860 | 17860 |
| Jupyter | 8080 | 8080 |

### Service Management

FluxGym runs as a Supervisor-managed service:

```bash
supervisorctl status fluxgym
supervisorctl restart fluxgym
supervisorctl tail -f fluxgym
```

## Building From Source

```bash
cd base-image/derivatives/pytorch/derivatives/fluxgym

docker buildx build \
    --build-arg PYTORCH_BASE=vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py310 \
    --build-arg FLUXGYM_REF=<commit-or-tag> \
    -t yournamespace/fluxgym .
```

### Build Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `PYTORCH_BASE` | `vastai/pytorch:2.7.1-cu128-cuda-12.9-mini-py310-2026-04-15` | PyTorch mini base image (Python 3.10) |
| `FLUXGYM_REPO` | `https://github.com/cocktailpeanut/fluxgym` | Upstream FluxGym repo |
| `FLUXGYM_REF` | | Git ref (commit/branch) to build |
| `SD_SCRIPTS_REPO` | `https://github.com/kohya-ss/sd-scripts` | Upstream Kohya sd-scripts repo |
| `SD_SCRIPTS_REF` | `sd3` | Kohya sd-scripts branch (FluxGym expects `sd3`) |

## Building with GitHub Actions

Fork this repository and use the included GitHub Actions workflow to build and push FluxGym images to your own DockerHub account. Scheduled builds poll upstream for new commits every 12 hours.

## Licenses

This image ships vendor application(s) under the following license(s):

- **FluxGym** — Apache-2.0 ([upstream](https://github.com/cocktailpeanut/fluxgym))
- **Kohya sd-scripts** — Apache-2.0 ([upstream](https://github.com/kohya-ss/sd-scripts))

See `/LICENSES.md` in the image for license details and file locations.

## Useful Links

- [FluxGym GitHub](https://github.com/cocktailpeanut/fluxgym)
- [Kohya sd-scripts](https://github.com/kohya-ss/sd-scripts)
- [PyTorch Image Documentation](../../README.md)
- [Base Image Documentation](../../../../README.md)
- [Image Source](https://github.com/vast-ai/base-image/tree/main/derivatives/pytorch/derivatives/fluxgym)
