# Third-Party Licenses

This image bundles the following vendor application(s). Each is the property of
its respective authors and is distributed under the license shown below. The
full license text for each application is included with its source inside this
image at the path indicated.

## PyTorch

- **License:** BSD-3-Clause
- **Upstream:** https://github.com/pytorch/pytorch
- **License file in image:** Included in the pip-installed package under
  `/venv/main/lib/python3.*/site-packages/torch-*.dist-info/LICENSE`

## Stable Diffusion WebUI Forge

- **License:** AGPL-3.0
- **Upstream:** https://github.com/lllyasviel/stable-diffusion-webui-forge
- **License file in image:** `$WORKSPACE/stable-diffusion-webui-forge/LICENSE.txt`
- **Notes:** This image supports multiple Forge variants. The upstream URL and
  license may differ depending on the `FORGE_REPO` build argument used.
