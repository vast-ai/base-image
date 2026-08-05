# ADR 0008 — Template publish + live-test tooling in base-image

- **Status:** Accepted (conditional — see Binding conditions)
- **Date:** 2026-06-26
- **Decision owner:** Rob Ballantyne
- **Process:** copied from `vast_landing` (director-authorised) → trimmed to two tools → multi-dimension review (correctness / security / fit) → findings fixed

## Context

This line of work builds the new-image pipeline. The last mile of that pipeline is
*publishing* a built image as a Vast.ai template and *testing* it on real
hardware. Both already existed in the private `vast_landing` repo's
`scripts/template_manager`, which is a larger suite (model-library generator,
recommended/autoscaler pipelines, template CRUD). With director approval the
relevant pieces were copied into this repo (the source remains in
`vast_landing`) and trimmed to just the two tools the pipeline needs:

- **`create.py` (+ `template_manager.py`, `models.py`)** — create a Vast.ai
  template from a directory of `<name>/{template.yml, README.md}`. Talks to the
  Vast.ai **console** API (`POST /template/`).
- **`test_template.py`** — launch a live GPU instance from a template hash and
  stream the in-instance test results. It is the client half of the test runner
  this repo already owns at `ROOT/opt/instance-tools/tests/` (SSE on port
  **10199**).

This couples an image-build repo to the Vast.ai console API (account/template
management), which is a different concern from building images. The decision is
whether that coupling belongs here, and on what terms.

## Options considered

- **Leave it in `vast_landing`.** Rejected: `test_template.py` drives a contract
  (`ROOT/opt/instance-tools/tests/`, port 10199/SSE) that is *defined in this
  repo*. A client living in another repo silently drifts when the contract here
  changes. The publish step also consumes `template.yml`+`README.md` describing
  images built here — it is the natural tail of this pipeline.
- **A new dedicated ops repo.** Rejected for now: premature for two small tools;
  adds a third place to keep the shared Vast-API core in sync. Revisit if the
  console-API surface here grows beyond publish+test.
- **Host the two tools in `tools/template_manager/` (chosen).** Keeps the test
  client next to the contract it depends on, and the publish tool next to the
  artifacts it publishes — fenced off from the image-build path.

## Decision

Host the two tools in `tools/template_manager/`, as the QA/publish layer of
the new-image pipeline, under these terms:

1. **Scope boundary.** `tools/template_manager/` is publish/QA tooling. It is
   **not** part of the image build or any CI correctness path, and must never be
   imported by a build script, Dockerfile, or workflow. It is run by a human (or
   a future `imagegen qa` step, ADR 0005) against a dedicated QA account.
2. **Source-of-truth split, not a silent fork.** Ownership is split *by tool*:
   base-image is canonical for the create-from-directory + live-test tools;
   `vast_landing` keeps the model-library/recommended/autoscaler half. The shared
   core (`VastTemplate`, the async API client) is duplicated and has now
   **diverged** (this copy is trimmed, hardened, and `extra="forbid"`); do not
   back-port between the copies. If the console API changes, both copies update
   independently (accepted cost) until/unless a shared package is extracted.
3. **The 10199 / SSE contract is named.** `test_template.py` ↔
   `ROOT/opt/instance-tools/tests/` communicate over port 10199 with an SSE event
   shape owned in this repo. A change to that port or event shape on either side
   is a breaking change to the other; keep them in step.
4. **Tool-local dependency policy.** Third-party deps for a tool live in
   `tools/<x>/requirements.txt` and are **not** part of any image. `pydantic` +
   `pyyaml` (typed validation of `template.yml`) earn their place; `httpx`/async
   was rejected in favor of stdlib `urllib` — heavier than this serial,
   self-paced workload needs (`test_template.py` does gnarlier networking in
   stdlib too), and not to be cargo-culted by the next tool.

## Binding conditions

The review's findings are fixed as a condition of acceptance:

- **Secret hygiene (security).** The Vast API key is sent via
  `Authorization: Bearer`, never as a URL query param; `*_pass`/`*_token`/`*_key`
  are redacted in dry-run/log output; `tools/template_manager/output/` is
  gitignored; `.env` is never committed (only `.env.example`). The QA account key
  must be a **dedicated QA-only key** (shared with ADR 0005/0006).
- **Strict input model.** `VastTemplate` is `extra="forbid"` — an unknown
  `template.yml` key is rejected, not forwarded to the API verbatim.
- **Correctness.** The 429/retry path surfaces terminal status errors instead of
  looping; retries are limited to transport faults.
- **Tested logic.** The pure logic (env/ports folding, `to_api_dict`,
  `inject_readme`, `extra_filters` parsing) carries unit tests, run the same way
  as imagegen's: `cd tools/template_manager && PYTHONPATH=. python -m pytest -q`.

If any condition is refused, this decision is void.

## Consequences

- The publish + live-test last mile lives next to the pipeline and the test
  contract it depends on; the new-image flow can reach a real template + a real
  GPU smoke without leaving the repo.
- A duplicated, now-diverged Vast-API core exists in two repos. Mitigated by the
  ownership split and a fenced scope; the residual risk is parallel maintenance
  if the console API changes.
- New tool-local deps (`pydantic`, `pyyaml`, `python-dotenv`) enter the
  repo's dev surface, but not any image.

## What would reverse this

- `create.py` re-growing toward model-library/autoscaler/account-management — the
  "it's just the publish tail of our pipeline" justification collapses; move it
  back to `vast_landing` or a dedicated ops repo.
- A shared Vast-API client being extracted as an installable package — then both
  repos depend on it and the duplication (condition 2) goes away.

## Note on ADR numbering

ADRs 0002–0007 are committed on sibling feature branches (oobabooga,
exposure-gate, agent-guides) not yet merged here. This took the next free number
(0008); reconcile the sequence when the branches land together.
