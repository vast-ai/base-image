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

### Run a one-off Template QA in CI

`.github/workflows/template-qa.yml` ("Template QA") runs the launcher's one command
against a **published** template, from GitHub: Actions → Template QA → Run workflow,
or

```bash
gh workflow run template-qa.yml -f template_hash=<32-hex hash> -f gpu="RTX 5090"
```

Inputs: `template_hash` (required), `capabilities`, `gpu`, `min_inet` (default 2000
Mbps), `min_reliability` (default 0.99), `max_price` (default 2.00 $/hr),
`extra_env` (one `KEY=VALUE` per line) and `timeout` (seconds). It runs
`test_template.py <hash> --destroy --proof-dir /tmp/qa-proofs ...` once — no
throwaway template, no redraw — always destroys the instance, and labels it
`base-image-qa-template` so the reaper covers a leaked one. The job fails when the
test fails. Whatever the outcome, the job summary starts with the template hash and
the proof artifact's name, followed by the proof's summary (template name, verdict,
every check), and the proof is uploaded as `template-qa-proof-<hash>-run<id>-attempt<n>`
for 90 days. Needs the `VAST_API_KEY` secret; `HF_TOKEN` is passed to the instance when
set.

### Proofs — every run leaves one

Every run that resolves its template writes a proof directory, on every exit path
(pass, fail, config error, no offers, interrupt, timeout, crash). The path is the
launcher's last stderr line. `--proof-dir DIR` chooses where; the default is
`./qa-proofs/` under the current directory (gitignored).

```
qa-proofs/<UTC timestamp>-<template hash prefix>-<run id>/
  proof.json            the record (schema_version 1)
  summary.md            a short table: subject, hardware, capabilities, every check, verdict
  logs/<name>           each streamed log, as streamed (redacted)
  logs/test-output.log  the in-image test output, as streamed (redacted)
```

`proof.json` holds: the launcher's git commit and whether the tree was dirty, the
Python version; start and end time, duration, exit code and state exactly as `--raw`
reports them, the argv with secret values masked, whether `--instance`, `--offer` or
`--keep` were used, how many instances were launched and discarded and the instance
id of every attempt (`run.instance_id`, `run.instance_ids`); the CI context
and run URL when the GitHub Actions variables are set; the template hash and name, the
image, its digest when the instance reports one, and the `--env` overrides
(secret-named values masked); descriptive hardware (GPU model and count, VRAM, arch,
compute capability, CUDA and driver version); the capabilities and their source; every
in-image test result and every external and contract result; and an integrity block
— the sha256 of every other file and of the canonical record.

**What a proof never contains:** host data. No IP address, no machine, host or offer
id, no SSH coordinates or mapped ports, no hostname, datacenter or location — the repo
and its CI artifacts are public. The instance id is not host data: it is ephemeral and
ties the proof to the run, so it is recorded and never scrubbed. The record is built
from an allowlist of fields, never by copying the `--raw` payload. Every file (record,
summary, logs and log file names) goes through the launcher's IP redaction and is
scrubbed, as substrings, of the API key, the instance token, secret-named env values
and the host's identifying values; any run of 8 or more leading characters of a
secret (a logged token prefix) is masked too, and the home directory becomes `~`.
`--proof-dir` is recorded as given, not its path.

`python3 verify_proof.py <dir> [<dir> ...]` recomputes every hash: exit 0 when every
proof verifies, 1 on a mismatch or a missing, extra or unreadable file, 2 on an
unknown schema version.

In CI, `.github/workflows/qa-gate.yml` writes the proofs to `/tmp/qa-proofs` (one per
attempt, so a cell that redrew keeps every attempt's), appends each summary to the job
summary, verifies each proof, and uploads them as the artifact
`qa-proof-<cell>-<inputs hash>-attempt<N>`, kept 90 days. The separate verdict
evidence (`qa-evidence-*`, read by the promote workflows) is uploaded as a copy made by
`public_evidence.py`, without `machine_id`, `ssh` or token-named keys; the job's own
payload stays whole for the redraw logic.
