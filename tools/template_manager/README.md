# template_manager

Vast.ai template publish + live-test tooling — the QA/publish tail of the new-image
pipeline. Copied from the private `template_manager` tooling and trimmed to
two tools; the model-library generator and recommended/autoscaler pipelines were left
behind. Scope, the duplicate-source split, and the port-10199 test contract are pinned in
[ADR 0008](../../docs/adr/0008-template-publish-tooling.md). **Not** part of the image
build or any CI path — never import it from a build script or workflow.

Both read `VAST_API_KEY` (see `.env.example`; `.env` is loaded by `create.py`). Use a
dedicated QA-only key. The key is sent as an `Authorization: Bearer` header, never in a URL.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt        # pydantic, PyYAML, python-dotenv
```

Unknown keys in a `template.yml` are rejected (`extra="forbid"`) so a typo surfaces
instead of being POSTed to the API.

### Tests

```bash
cd tools/template_manager && PYTHONPATH=. python3 -m pytest -q
```

## `create.py` — create templates from a directory

Creates Vast.ai templates from a **directory of template subdirectories**, each containing
a `template.yml` (the template spec) and an optional `README.md` (injected as the template
readme). Always POSTs new templates.

```bash
python3 create.py path/to/templates/        # a dir of <name>/{template.yml,README.md} subdirs
python3 create.py my-template.yml --readme my-readme.md   # a single template
python3 create.py path/to/templates/ --dry-run            # validate + render, don't POST
python3 create.py path/to/templates/ --image vastai/comfyui --tag staging-abc123  # CI: override image/tag
python3 create.py --delete 484392                         # teardown a throwaway template by id
```
`--image`/`--tag` override the loaded template before POST (CI points a QA template at a
freshly-built staging tag); `--delete <id>` removes a template (QA teardown). Depends on
`template_manager.py` + `models.py` (same directory).

## `test_template.py` — live-GPU template test

Launches a Vast.ai instance from a template hash and streams the in-instance test results
(it pairs with the base image's `ROOT/opt/instance-tools/tests/` runner on port 10199).
Stdlib-only.

```bash
python3 test_template.py <template_hash>            # launch, stream results, tear down
python3 test_template.py <template_hash> --dry-run
```
See `python3 test_template.py --help` for offer/price/timeout/keep flags.
