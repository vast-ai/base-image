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
- **License file in image:** `/licenses/AGPL-3.0.txt` — the canonical AGPL-3.0
  text, vendored so a copy ships *with the program* (AGPL §4), since the
  pip-installed `unsloth` package does not reliably carry the Studio LICENSE.
  (That package's `dist-info` under
  `/venv/main/lib/python3.*/site-packages/unsloth-*.dist-info/` carries the
  Apache-2.0 license for the core library.)
- **Modifications:** This image modifies the AGPL-3.0 Studio component (AGPL §5a):
  `studio/install_python_stack.py` is patched to pin the CUDA torch backend
  (`--torch-backend=cu128`), and `studio/setup.sh` to build llama.cpp with
  portable CPU dispatch (`-DGGML_NATIVE=OFF -DGGML_CPU_ALL_VARIANTS=ON …`) so it
  does not SIGILL on CPUs lacking AVX-512. The complete corresponding source —
  including these modifications — is public at
  https://github.com/vast-ai/base-image (see the image's Dockerfile).
- **Notes:** The Unsloth core library is Apache-2.0. The Studio component
  (frontend and related tooling under `studio/`) is separately licensed under
  AGPL-3.0.
