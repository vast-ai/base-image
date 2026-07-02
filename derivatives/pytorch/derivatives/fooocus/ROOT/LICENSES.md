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

## Fooocus

- **License:** GPL-3.0
- **Upstream:** https://github.com/lllyasviel/Fooocus
- **License file in image:** `/opt/workspace-internal/Fooocus/LICENSE`
- **Modifications:** This image (GPL-3.0 §5a) strips the torch/torchvision/torchaudio/
  torchcodec pins from `requirements_versions.txt` so the app inherits the base image's
  torch build. Complete corresponding source, including this change, is public at
  https://github.com/vast-ai/base-image (see the image's Dockerfile).
