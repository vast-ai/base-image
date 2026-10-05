# Third-Party Licenses

This image bundles the following vendor application(s). Each is the property of
its respective authors and is distributed under the license shown below. Where
the vendor's source or LICENSE file is shipped inside this image at a known
location, the path is given. Otherwise, the upstream repository is referenced
as the canonical source for the license text.

## PyTorch

- **License:** BSD-3-Clause
- **Upstream:** https://github.com/pytorch/pytorch
- **License file in image:** Included in the pip-installed package under
  `/venv/main/lib/python3.*/site-packages/torch-*.dist-info/LICENSE`

## ComfyUI

- **License:** GPL-3.0
- **Upstream:** https://github.com/Comfy-Org/ComfyUI
- **License file in image:** `/opt/workspace-internal/ComfyUI/LICENSE`
- **Modifications:** This image (GPL-3.0 §5a) strips the torch/torchvision/torchaudio/
  torchcodec pins from ComfyUI's `requirements.txt` and from ComfyUI-Manager's
  `requirements.txt` so the apps inherit the base image's torch build. Complete
  corresponding source, including these changes, is public at
  https://github.com/vast-ai/base-image (see the image's Dockerfile).

## ComfyUI-Manager

- **License:** GPL-3.0
- **Upstream:** https://github.com/Comfy-Org/ComfyUI-Manager
- **License file in image:** `/opt/workspace-internal/ComfyUI/custom_nodes/ComfyUI-Manager/LICENSE.txt`
- **Modifications:** See the ComfyUI entry above: the torch pins are
  stripped from this node's `requirements.txt` as well.

## ComfyUI workflow-to-API converter

- **License:** Unlicense
- **Upstream:** https://github.com/SethRobinson/comfyui-workflow-to-api-converter-endpoint
- **License file in image:** `/opt/workspace-internal/ComfyUI/custom_nodes/comfyui-workflow-to-api-converter-endpoint/LICENSE`

## ComfyUI API wrapper

- **License:** None declared - the upstream repository ships no LICENSE file
- **Upstream:** https://github.com/ai-dock/comfyui-api-wrapper
- **License file in image:** None
