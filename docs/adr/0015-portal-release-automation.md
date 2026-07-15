# ADR 0015 — Automate the Instance Portal release cut on VERSION change

- **Status:** Proposed
- **Date:** 2026-07-15
- **Decision owner:** Rob Ballantyne

## Context

`portal-aio/` (the Instance Portal + tunnel manager) ships to the fleet through a
GitHub **release**, not the base-image build. On first boot,
`ROOT/etc/vast_boot.d/first_boot/10-update-instance-portal.sh` reads the installed
version from `/opt/portal-aio/VERSION`, fetches the repo's **latest release**
`tag_name`, and — on any mismatch, upgrade **or downgrade** — calls
`ROOT/opt/instance-tools/bin/update-portal`, which downloads that release's
`instance-portal.tar.gz`, extracts it to `/opt`, and reinstalls
`requirements.txt`. `portal-aio/VERSION` is the source of truth for the installed
version; the release tag is the source of truth for the target.

Until now the release was cut **by hand**: bump `VERSION`, `git tag`, build a
tarball of `portal-aio/`, upload it as the release asset. There was no packaging
workflow. This surfaced during the #216/#217 CVE remediation: the fix reached
`main` but could not reach the fleet without a manual, error-prone cut.

The manual path also has a **correctness trap**. Because `main` bakes
`portal-aio/VERSION` into freshly-built images, and first boot downgrades on *any*
mismatch, if `main`'s VERSION is ahead of the latest **published** release, a new
image boots with `installed > latest` and **downgrades itself**, silently undoing
whatever the VERSION bump shipped. So `main`'s VERSION and the latest release must
never diverge for long — a constraint a human-timed cut cannot guarantee.

## Options considered

- **A — Publish on `VERSION` change on `main` (chosen).** A workflow triggers on a
  push to `main` that touches `portal-aio/VERSION`, reads the version, builds the
  tarball via `git archive`, and creates + publishes the release as `latest`.
  *Trade-off:* no separate publish button — the fleet push fires on merge. Accepted
  because the VERSION-bump PR review **is** the decision to ship, and this makes
  `main`↔release divergence structurally impossible (they move in one CI run),
  closing the downgrade window.

- **B — Publish on tag push (`v*`).** Rejected: a human pushing a tag is a
  deliberate act, but it leaves a downgrade window open between merging the VERSION
  bump and pushing the tag, and lets the tag drift from the `VERSION` file.

- **C — Manual `workflow_dispatch` only.** Rejected as the primary trigger: keeps a
  human finger on publish but leaves the downgrade window open until someone
  remembers to run it — the exact failure mode this ADR exists to remove. Retained
  as a **secondary** trigger for back-fill / re-runs.

## Decision

Add `.github/workflows/release-portal.yml`. Triggers: push to `main` on path
`portal-aio/VERSION`, plus `workflow_dispatch` (ref input) for back-fill. It reads
`portal-aio/VERSION`, validates it, and — unless a **published** release for that
version already exists — builds `instance-portal.tar.gz` = `git archive` of the
`portal-aio/` tree (tracked files only — matches the historical asset layout,
excludes `.claude`/`.pytest_cache`/`venv`), asserts the tarball's internal `VERSION`
matches, then `gh release create <version> --latest` at the resolved commit.

## Binding conditions

These guards are non-negotiable — each closes a fleet-downgrade or wrong-ship failure
mode found in adversarial review; removing one requires a fresh review.

- The asset is a `git archive` of `portal-aio/` with `--prefix=portal-aio/`, so it
  extracts to `/opt/portal-aio/…` exactly as `update-portal` expects.
- **Strict version format** — `portal-aio/VERSION` must match `^v?\d+\.\d+\.\d+$` or
  the run fails; an empty/malformed VERSION must never publish a tag.
- **Monotonic gate** — the workflow refuses to publish a version that is not strictly
  greater than the current *published* latest release. This is what makes `--latest`
  safe: a revert-to-lower VERSION or a `workflow_dispatch` back-fill of an older
  version cannot mark an older release latest and downgrade the fleet.
- **Idempotency keys on _published_ state, not existence** — a matching **draft**
  release (invisible to `releases/latest`) is treated as *not shipped*: it is deleted
  and re-published. Only an already-**published** release for the version is a no-op.
  (Checking mere existence would let a leftover draft strand the release and downgrade
  freshly-built images — the exact trap this ADR closes.)
- **Single resolved commit** — archive, tag, and `--target` all use one SHA
  (`git rev-parse HEAD` after checkout); if a tag for the version already exists at a
  different commit, the run aborts rather than mis-tag.
- Only `portal-aio/VERSION` triggers it; editing portal code without bumping
  VERSION does **not** cut a release (matches the source-of-truth model).
- `contents: write` is the only elevated permission; auth via the default
  `GITHUB_TOKEN`.

## Consequences

- Bumping `portal-aio/VERSION` on `main` ships the portal to the fleet automatically
  and atomically — the downgrade window is eliminated.
- The release cut is reproducible (no hand-built tarballs) and auditable (one CI run
  per version).
- **Accepted negative:** a merge that bumps VERSION is a fleet-wide push with no
  post-merge pause. Mitigation: the gate is the VERSION-bump PR; rollback remains
  "cut a higher VERSION" (unchanged from the manual path), and `--no-update-portal`
  / pinned `PORTAL_VERSION` remain the per-instance escape hatches.

## What would reverse this

- If the portal delivery mechanism stops keying off the GitHub "latest" release
  (e.g. moves to a pinned manifest or is baked into the image build), this workflow
  is obsolete.
- If auto-publish-on-merge proves too eager in practice (accidental fleet pushes),
  fall back to Option C (dispatch-gated) — the job body is unchanged, only the
  trigger flips.
