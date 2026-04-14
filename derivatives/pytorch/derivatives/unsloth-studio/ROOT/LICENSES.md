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

## Unsloth Studio

- **License:** AGPL-3.0
- **Upstream:** https://github.com/unslothai/unsloth
- **License file in image:** See `studio/LICENSE.AGPL-3.0` in the upstream
  repository. The pip-installed `unsloth` package
  (`/venv/main/lib/python3.*/site-packages/unsloth-*.dist-info/`) carries the
  Apache-2.0 license for the core library.
- **Notes:** The Unsloth core library is Apache-2.0. The Studio component
  (frontend and related tooling under `studio/`) is separately licensed under
  AGPL-3.0.
