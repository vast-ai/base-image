# Runbook — adding and retiring PyTorch versions

**Audience:** whoever (human or agent) is told "add the new torch release" or
"we're carrying too many old versions".
**Authority:** this file is the procedure. Where it disagrees with a summary
elsewhere, this file and the tests it names win.

Governed by [ADR 0022](../adr/0022-pytorch-version-lifecycle.md) (version
lifecycle) and [ADR 0021](../adr/0021-pytorch-promotion-qa-gate.md) (promotion
QA gate).

---

## 0. The rules that are enforced, so you cannot get them wrong quietly

| rule | enforced by |
|---|---|
| One patch per minor line — a new patch SUPERSEDES the old one | `test_each_minor_line_carries_exactly_one_patch` |
| Mini and config agree on the patch | `test_mini_and_config_agree_on_the_patch` |
| Nothing below the support floor is still built | `test_nothing_below_the_support_floor_is_still_built` |
| **A version a derivative pins can never be retired** | `test_no_pinned_version_is_retired` |
| A retired version does not come back by accident | `test_a_retired_minor_does_not_come_back_by_accident` |
| Every built mini artifact is QA-gated | `test_every_mini_artifact_the_build_produces_is_gated` |
| Every backend an `-auto` tag points at has a QA cell | `test_every_backend_an_auto_tag_points_at_is_gated` |

```bash
python -m pytest -q tools/imagegen tools/template_manager
```

If you change this area and these stay green, **assume you have not actually
changed it.**

---

## 1. The support floor

The floor is **the oldest torch minor any derivative in this repo pins**. It is
derived from the derivative Dockerfiles and `build-*.yml` workflows, never
hand-set, so it rises on its own as pins are bumped.

To see it:

```bash
python - <<'PY'
import re, pathlib
from collections import defaultdict
minor = lambda v: ".".join(v.split(".")[:2])
pins = defaultdict(set)
for pat in ("derivatives/pytorch/derivatives/*/Dockerfile", ".github/workflows/build-*.yml"):
    for f in pathlib.Path(".").glob(pat):
        for v, _ in re.findall(r"vastai/pytorch:([0-9.]+)-([a-z0-9]+)-cuda", f.read_text()):
            pins[minor(v)].add(f.parent.name if f.name == "Dockerfile" else f.name)
for m in sorted(pins, key=lambda s: [int(x) for x in s.split(".")]):
    print(f"  {m}: {', '.join(sorted(pins[m]))}")
PY
```

Retirement is **not** deletion. Every dated tag already published stays pullable
forever; retiring only stops new ones being minted.

---

## 2. Adding a new torch version

**Check upstream first, do not assume.** The wheel index is the ground truth,
not a release note and not a comment in this repo:

```bash
# which CUDA backends exist at all
for b in cu126 cu128 cu129 cu130 cu132; do
  echo -n "$b -> "; curl -s -o /dev/null -w "%{http_code}\n" \
    "https://download.pytorch.org/whl/$b/torch/"
done

# which torch versions, pythons and arches a backend carries
curl -s "https://download.pytorch.org/whl/cu130/torch/" \
 | grep -oE 'torch-[0-9.]+\+cu130-cp3[0-9]+-cp3[0-9]+-manylinux[^"<]*(x86_64|aarch64)\.whl' \
 | sort -u
```

Things that bite:

- **A backend can be frozen.** cu128 stops at torch 2.11.0 — there is no cu128
  wheel for 2.12 or 2.13. Do NOT remap a cu128 entry onto cu126 to force a bump:
  cu126 has no Blackwell kernels, so a Blackwell card on a 12.8/12.9 host would
  get an unusable image.
- **A patch replaces its predecessor**, it does not join it. Adding 2.12.1 means
  removing 2.12.0 in the same change.
- **Add the mini too, or no derivative can adopt it.** All 15 pytorch
  derivatives pin a mini base; a config-only version is unreachable by the
  estate.

Then edit `configs/pytorch.json` — the only source of truth — and run the tests.

## 3. Adding a new CUDA backend

Additionally:

- **Confirm `uv` supports it.** The build uses `uv pip install --torch-backend
  <name>`, and uv only accepts backends it knows. cu132 was blocked on exactly
  this until uv 0.12.0. Check the released version, not `main`:
  ```bash
  curl -s "https://raw.githubusercontent.com/astral-sh/uv/<tag>/crates/uv-torch/src/backend.rs" \
    | grep -oE 'Cu[0-9]{3}' | sort -u
  ```
  The base installs uv unpinned, so a build gets whatever is current.
- **Confirm the CUDA base exists** in `configs/base-image.json`.
- **Decide the `-auto` mapping deliberately** — see §4. Adding a backend does not
  by itself change what customers are served; the `AUTO_TAG_MAP` does.

## 4. Repointing an `-auto` tag — read this before you do it

`AUTO_TAG_MAP` in `promote-pytorch.yml` decides what real customers are served.
It appears **twice** (promote and dry-run); both must move together.

**The direction of CUDA minor-version compatibility is the thing to get right.**
`cuda_max_good` is the NEWEST CUDA a host's driver supports. The safe direction
is an **older-built image on a newer driver** — a cuda-11.8 image is fine on a
13.x box, which is why every floor in the QA templates is a `gte`. The reverse —
a 13.2-built image on a host whose driver tops out at 13.1 — leans on compat in
the direction that can fail, across a whole host tier at once.

This is why `cuda-13.2.1-auto` points at cu132 (native, strictly better) while
`cuda-13.1.2-auto` deliberately stays on cu130. There is no cu131 wheel index
upstream, so cu130 is the newest build every 13.1 driver can definitely run.

If you repoint a tag onto a *newer-built* backend, **the QA floor must move with
it**, or the gate tests the artifact only on hosts newer than the ones it will
be served to.

## 5. Retiring a version

Order matters:

1. **Bump any derivative pins off it first.** Retiring a pinned version breaks
   that derivative's build with no warning. `test_no_pinned_version_is_retired`
   stops you, but the fix is to bump, not to work around the test.
2. Remove its `configs[]` and `mini[]` entries from `configs/pytorch.json`.
3. Add the minor to `RETIRED` in `tools/imagegen/tests/test_pytorch_version_lifecycle.py`.
4. Note it in ADR 0022's "applied" list, with the reason.
5. Run the tests.

Nothing is deleted from the registry. If you actually want to delete published
tags, that is a different decision with a different blast radius and needs its
own ADR.

## 6. After changing the matrix

- The QA matrix is **derived** from the table, so cells follow automatically —
  but check the count is what you expect:
  ```bash
  python -m pytest -q tools/template_manager/tests/test_pytorch_qa_coverage.py
  ```
- Build before promoting: a version that has never been built has no staging tag
  to promote, and `resolve-digests` will fail on the missing source.
- Under ADR 0021 any blocked cell stops the whole promotion, so a newly added
  version that fails QA holds everything. Adding several at once raises that
  risk — consider one new backend per promotion.
