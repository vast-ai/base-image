## PyTorch (this image)

This image is the base image plus a preinstalled PyTorch stack — that's the only
material difference, so everything in base.md (supervisor, Caddy, ports, storage,
GPU/CUDA, provisioning) applies unchanged.

**`torch`** and its companions (**torchvision**, **torchcodec**, and **torchaudio**
on builds from before torchaudio's upstream wind-down) are preinstalled in the
default venv **`/venv/main`** — already active in login shells, otherwise
`source /venv/main/bin/activate` (base.md §2). PyTorch wheels bundle their own CUDA
runtime, so `torch.version.cuda` reflects the **wheel's** backend (cu126 / cu130 /
cu132), which need not match the base image's system CUDA. On the slim **mini**
images the two deliberately differ — the wheel rides a minor version below the base
(minor-version compatibility, e.g. a cu130 build on a CUDA 13.2 base). Both the
torch version and the backend are encoded in the image tag.

Don't assume a version — confirm what's actually here:
```
python -c 'import torch, torchvision; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())'
vast-capabilities packages | jq '.python_environments[] | select(.name=="main").package_versions'
```
The second prints probed versions for the main env (torch, torchvision, torchcodec,
torchaudio where present). Install more into the same env with `uv pip install <pkg>`.
For a GPU that needs a newer CUDA than the installed build targets (e.g. Blackwell on
a cu126 image), see base.md §12 on matching the wheel/CUDA to the architecture.
