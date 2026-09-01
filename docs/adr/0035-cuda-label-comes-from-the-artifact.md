# ADR 0035 — A published image's CUDA label comes from the artifact, not the tag name

- **Status:** Accepted
- **Date:** 2026-09-01
- **Decision owner:** Rob Ballantyne

## Context

`build-vllm-omni.yml` derived the CUDA version of each build from the shape of the
upstream tag: the bare tag was hardcoded to CUDA 12.9, `-cu130` to 13.0, `-cu128` to
12.8. That was true when it was written. Upstream later changed what the bare tag
*means* — 12.9 before `v0.20.0`, 13.0 from `v0.20.0` onward — without changing how it
is spelled, and `vllm/vllm-omni` publishes no `-cu130` tag that would reveal the shift.

The rule kept reporting 12.9. Five published tags carry a CUDA version they do not
contain:

| tag | label says | actually contains |
|---|---|---|
| `vastai/vllm-omni:v0.14.0-cuda-12.9` | 12.9 | 12.9.1 |
| `vastai/vllm-omni:v0.16.0-cuda-12.9` | 12.9 | 12.9.1 |
| `vastai/vllm-omni:v0.18.0-cuda-12.9` | 12.9 | 12.9.1 |
| `vastai/vllm-omni:v0.20.0-cuda-12.9` | 12.9 | **13.0.2** |
| `vastai/vllm-omni:v0.22.0-cuda-12.9` | 12.9 | **13.0.2** |
| `vastai/vllm-omni:v0.24.0-cuda-12.9` | 12.9 | **13.0.2** |
| `vastai/vllm-omni:v0.26.0-cuda-12.9` | 12.9 | **13.0.2** |
| `vastai/vllm-omni:v0.28.0-cuda-12.9` | 12.9 | **13.0.2** |

The CUDA minor is customer-facing: it is how a renter's host driver is matched to an
image. A tag that understates its CUDA requirement can be scheduled onto a host whose
driver cannot run it. Separately, the genuine CUDA 12.9 build (`minimax-h3-cu129`,
12.9.1, a distinct upstream image) was never built at all, because the mapper had no
`-cu129` branch.

`build-vllm.yml` had already met the same upstream change and answered it with a
heuristic: treat the bare tag as 13.0 *only when* no explicit `-cu130` variant exists
(`build-vllm.yml:122-131`). That works for `vllm/vllm-openai`, which publishes
`-cu130`. It cannot work for `vllm-omni`, which never has — applying it there labels
the genuinely-12.9 `v0.14.0`-through-`v0.18.0` images as 13.0. Copying it would have
swapped one mislabel for its mirror image.

The root problem is not which suffix table is right. It is that the label was being
inferred from a name whose meaning is controlled by someone else and is not stable
over time.

## Options considered

**A. Port `build-vllm.yml`'s `$has_cu130` heuristic to omni.** The smallest diff, and
it fixes every currently-shipping tag. Rejected: verified against live tags, it
relabels `v0.14.0`/`v0.16.0`/`v0.18.0` — which really are 12.9.1 — as 13.0. It trades
a wrong answer for the new era against a wrong answer for the old one, and it leaves
the next silent upstream re-meaning to be discovered the same way this one was.

**B. Pin a cutover version — bare means 13.0 at or above `v0.20.0`, 12.9 below.**
Correct for every tag today. Rejected: it encodes a fact about upstream's history in
our CI, where nothing will ever re-check it, and it needs a hand edit the next time
upstream moves. It is the same class of defect as the rule it replaces, with a longer
fuse.

**C. Extend the suffix table (add `-cu129`, keep bare hardcoded).** Rejected: builds
the missing genuine 12.9 image but leaves the bare tag — the one that is actually
wrong on five published tags — untouched.

**D. Read `CUDA_VERSION` from the upstream image config (chosen).** One
`docker buildx imagetools inspect` per candidate tag in preflight. Costs a handful of
authenticated registry reads and makes preflight depend on the registry being
reachable. It has no vocabulary to maintain and cannot drift, because it observes the
artifact instead of predicting it.

## Decision

Take D. The CUDA version of a build is read from the upstream image's own
`CUDA_VERSION` environment variable and truncated to `major.minor`. If a candidate
image reports no `CUDA_VERSION`, the variant is skipped with a warning — the build
never guesses a label it cannot substantiate.

Variant *selection* is separately anchored to `^<version>(-cu[0-9]+)?$`, matching
`build-sglang.yml:118`. This is what makes reading the artifact safe: without it,
`startswith` admits neighbouring families (`v0.28.0rc1`, `v0.26.0post1.*`) whose configs
would resolve to a real CUDA version and enter the matrix as duplicates.

Two variants resolving to the same CUDA version now fail the build rather than
publishing to one output tag and silently overwriting each other.

This ADR states the general invariant, not the omni-specific fix: **a published
image's CUDA label is derived from the artifact it was built from, never inferred
from an upstream tag's spelling.** `build-vllm.yml` still infers, and is a known
exception pending the same treatment.

## Binding conditions

- Preflight authenticates to Docker Hub before inspecting. Anonymous manifest reads
  share a per-IP quota across the runner fleet, and a rate-limit response is
  indistinguishable from "no `CUDA_VERSION`" — it would silently drop variants rather
  than fail.
- A variant whose CUDA cannot be read is skipped, never defaulted. If that empties the
  matrix, the existing hard error stands.

## Consequences

- The five mislabelled tags above are corrected on the next build of each version.
  Already-published tags are not rewritten by this change; relabelling or withdrawing
  them is a separate decision.
- `minimax-h3` now yields two images — `-cu129` at 12.9 and the bare tag at 13.0 —
  where it previously yielded one, mislabelled.
- Preflight gains a registry dependency. A registry outage now fails preflight rather
  than producing a wrong matrix, which is the intended direction.
- Arch-specific tags stop being excluded by a denylist of suffixes and are excluded by
  the anchor instead, so upstream's typo spellings (`-x86-64`, `-aarc64`, `-aarch`,
  observed live) can no longer leak in.

## What would reverse this

Upstream ceasing to set `CUDA_VERSION` in the image config, which would make the
artifact no longer self-describing and force a different observation point (for
example, the CUDA runtime package version in the image's package database).
