# ADR 0056 — Every CI job refreshes the runner's CA store first

- **Status:** Accepted
- **Date:** 2026-10-21
- **Decision owner:** Rob Ballantyne

## Context
On 20 October 2026 Ubuntu shipped `ca-certificates 20260816ubuntu0.24.04.1`, which
distrusts `emSign ECC Root CA - G3` after the private keys of eight eMudhra intermediates
under it were found to be recoverable. Our builds run on GitHub-hosted runners
(`ubuntu-latest`, `ubuntu-24.04-arm`). On 21 October those jobs still ran on runner image
`ubuntu-24.04 20261018.339.1`, built before the fix. A hosted image is built days before it
serves jobs, so any CA distrust reaches our runners late.

In that window every job checks out source, logs in to Docker Hub, pulls base images, pushes
images and (for QA) calls the Vast API, all through the runner's own trust store. `RUN`
steps inside our Dockerfiles are not affected: they use the image's store, which the
Dockerfile upgrades. Go module fetches are covered by sumdb. The runner itself is the gap.

## Options considered
- **Wait for GitHub to refresh the runner image.** Rejected: the lag is days, it recurs
  with every distrust, and nothing tells us when the patched image is actually serving our
  jobs.
- **A local composite action (`uses: ./.github/actions/...`).** Rejected: a local action
  needs the repository checked out first, and checkout is itself a TLS fetch through the
  stale store. The refresh has to be the first step.
- **A composite action referenced remotely by commit SHA.** Not chosen now: it removes the
  duplication but adds an indirection whose SHA only exists after merge, and the runner
  downloads it through the same store anyway. Revisit if the step text starts changing often.
- **Only the jobs that hold secrets.** Rejected: every job checks out the source that gets
  built and pushed, so build integrity depends on all of them, and "jobs that hold secrets"
  is not a property a linter can check reliably.
- **Fail the job when apt fails.** Rejected: an Ubuntu mirror outage would stop all CI. The
  apt calls are allowed to fail; the check after them is not.

## Decision
The first step of every workflow job with steps is `Refresh runner CA trust store`. It
upgrades `ca-certificates`, prints the installed version, and fails the job if a distrusted
root is still linked in `/etc/ssl/certs`. The step text has one canonical copy,
`linter.CA_REFRESH_STEP_RUN`. L109 requires the step first in every job and requires each
copy to match that text exactly. The generator's workflow template carries the same step, so
a scaffolded image passes L109 from the start.

## Binding conditions
- The step stays first: anything before it (checkout included) runs on the stale store.
- A new distrusted root is added to the canonical text and re-applied to every workflow in
  the same change; one hand-edited copy fails L109.

## Consequences
- Every job gains an `apt-get update` (roughly 10–20 s).
- An unpatched runner whose apt refresh fails now fails closed instead of running.
- The step is duplicated across ~146 jobs; L109 keeps the copies identical.
- The root list must be maintained. When Ubuntu distrusts a root, this list is part of the
  response.

## What would reverse this
GitHub guaranteeing that runner images pick up Ubuntu CA updates within hours, or a move to
runner images we build and refresh ourselves.
