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

## GLiNER2

- **License:** Apache-2.0
- **Upstream:** https://github.com/fastino-ai/GLiNER2
- **License file in image:** Included in the pip-installed package under
  `/venv/main/lib/python3.*/site-packages/gliner2-*.dist-info/LICENSE`

## Transformers

- **License:** Apache-2.0
- **Upstream:** https://github.com/huggingface/transformers
- **License file in image:** Included in the pip-installed package under
  `/venv/main/lib/python3.*/site-packages/transformers-*.dist-info/LICENSE`
- **Note:** Installed as part of the `gliner2[local]` extra; it supplies the
  DeBERTa-v3 encoder runtime every GLiNER2 checkpoint loads.

## GLiNER2 model weights

The server downloads checkpoints from the Hugging Face Hub at runtime
(`GLINER_MODEL`, default `fastino/gliner2.5-base-v1`). Weights are **not** baked
into this image and carry their own licenses, declared per-repository on the Hub.
Check the license of any checkpoint you point `GLINER_MODEL` at before
redistributing its outputs.

## Server

The FastAPI server at `ROOT/opt/workspace-internal/gliner/server.py` is part of
this repository, not a vendored third-party application. Upstream ships no
server to clone.
