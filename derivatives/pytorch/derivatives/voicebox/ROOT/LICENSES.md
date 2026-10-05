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

## Voicebox

- **License:** MIT
- **Upstream:** https://github.com/jamiepine/voicebox
- **License file in image:** `/opt/voicebox/LICENSE`

## FlashAttention

- **License:** BSD-3-Clause
- **Upstream:** https://github.com/Dao-AILab/flash-attention
- **License file in image:** Included in the pip-installed package under
  `/venv/main/lib/python3.*/site-packages/flash_attn-*.dist-info/LICENSE` (amd64 builds only)
