# Runbook — add or update a CUDA version in base-image

**Audience:** whoever (human or agent) is told "prepare for the new CUDA release".
**Authority:** this file is the procedure. Where it disagrees with a summary
elsewhere, this file and the tests it names win — but see §0, because most of what
matters here is *enforced*, not merely documented.

Governed by [ADR 0019](../adr/0019-base-image-promotion-qa-gate.md) (promotion QA
gate) and [ADR 0005](../adr/0005-live-gpu-qa-gate.md) (live-GPU QA gate).

---

## 0. The rules that are enforced, so you cannot get them wrong quietly

Do not treat these as advice. Each is a test that fails, and each exists because
the failure mode was silent before it was caught.

| rule | enforced by |
|---|---|
| The config table is the ONLY place configs are defined; build/promote/extend all read it | `test_python_versions_are_read_from_the_table_not_hardcoded`, `test_every_job_that_reads_the_table_checks_out` |
| Every CUDA config floors QA at **its own minor**, never below | `test_every_cuda_config_floors_qa_at_its_own_minor` |
| An unmapped major **fails the run**; it never defaults | `test_the_floor_case_fails_closed_on_an_unmapped_major` |
| `cuda_max_good` floors are LOWER bounds — never an `lte` | `test_set_filter.py`, template comments |
| An auto tag only moves to a digest that passed QA **this run, at that digest** | `test_promote_behaviour.py` (executed against a fake registry) |
| There is no QA bypass | `test_no_qa_bypass_input_exists`, `test_there_is_no_way_to_proceed_without_the_qa_key` |
| Every promoted artifact is copied **by digest**, never by a mutable staging tag | `test_no_prod_tag_is_ever_copied_from_a_mutable_staging_tag` |

Run them all with:

```bash
python -m pytest -q tools/imagegen tools/template_manager
```

If you change anything in this area and these stay green, **assume you have not
actually changed it** — that mistake has been made three times in this repo, and
each time the tests were passing.

---

## 1. Decide what kind of change this is

This determines everything downstream. Get it wrong and you ship a product change
believing it is a patch bump.

**A patch bump** (`12.9.1` → `12.9.2`): edit the existing config in place. The
`-auto` tag it feeds already exists and keeps its meaning. Low risk.

**A new minor or major** (`13.3` → `13.4`, or `14.0`): this ADDS a new
`cuda-X.Y.Z-auto` tag. Vast's `@vastai-automatic-tag` backend resolves customer
templates against these, so **a new minor changes what customers can be served.
That is a product decision, not a maintenance task.** Confirm it is wanted before
building. It also requires §3.

**Retiring a version:** removing a config stops the dated tags being rebuilt, but
the `-auto` tag keeps serving its last digest forever. Removal is not deletion —
decide explicitly what should happen to the tag.

---

## 2. Edit the config table — the only source of truth

`configs/base-image.json`, `configs[]`:

```json
{
  "key": "cuda-13.4-24",
  "base_image": "nvidia/cuda:13.4.0-cudnn-devel-ubuntu24.04",
  "tag_template": "cuda-13.4.0-cudnn-devel-ubuntu24.04",
  "arches": ["linux/amd64", "linux/arm64"],
  "default_python": "3.12"
}
```

Notes that bite:

- **`tag_template` determines the auto tag name.** It is parsed with
  `sed 's/^cuda-\([0-9.]*\)-.*/\1/p'`, so `cuda-13.4.0-...` yields
  `cuda-13.4.0-auto` — **patch-versioned**. The auto version is derived, never
  stored. A typo here silently creates a tag nobody is watching.
- **Verify the upstream tag exists first.** `crane digest nvidia/cuda:<tag>`. A
  missing base surfaces much later as a build failure.
- **`default_python`** is the artifact the auto tag points at and the only python
  QA tests. It must be in the table's `python_versions`.
- **`arches`**: arm64 manifests are built but **not QA'd** — deliberate, parked on
  market size (ADR 0019 cond 5). Including arm64 is fine; just know it ships
  untested.
- Mini variants are separate entries under `mini[]` and are **not QA'd** at all,
  also deliberately (they carry no auto tag).

## 3. The driver floor — nothing to do

**This step no longer exists.** The `cuda_max_good` floor is DERIVED from the tag
template in `.github/workflows/promote-base-image.yml` (`13.3.1` -> `13.3`), so a
new major or minor floors itself. There is no `case` branch to add and no step to
forget.

It used to be a hand-maintained major baseline (`13.*) floor=13.0`). That was
changed on 2026-08-14 (ADR 0019 amendment (b)) because it floored the newest
images below their own version: at a 13.0 floor 79% of the market qualifies and is
dominated by 580/590/595 drivers, so a cuda-13.3 image was validated through
forward compat and the native driver path went untested — the shape of gap the
driver-610 rename got through.

The floor remains a **lower** bound: an image is happy on a newer driver, so
`gte` selects the oldest driver that can run it natively. Never add an `lte` —
that selects for the oldest hosts, the opposite of the intent.

Still fail-closed: a tag template whose version parses but has no minor
(`cuda-14-...`) aborts the run rather than emitting a floor QA would rent
against. A template with no `cuda-X.Y` prefix reads as "no auto tag" — which is
what the `stock-*` pair is — and is excluded from QA.

## 4. Build

```bash
gh workflow run build-base-image.yml --ref <branch>
```

Builds every config × 5 pythons × arch into the staging namespace as
`<tpl>-py<ver>-<YYYY-MM-DD>`. **Staging date tags are mutable** — a second build
the same day rewrites them.

## 5. Rehearse

```bash
gh workflow run promote-base-image.yml --ref <branch> \
  -f STAGING_DATE=<YYYY-MM-DD> -f DRY_RUN=true
```

Writes nothing. Check the new config appears, and that the auto-tag dance resolves
a Phase A anchor for it.

## 6. Promote — QA runs automatically, then a human approves

```bash
gh workflow run promote-base-image.yml --ref <branch> -f STAGING_DATE=<YYYY-MM-DD>
```

The new config is QA'd automatically: the QA set is derived as "has an auto version
AND target digest ≠ current digest", and a brand-new config has no current digest,
so it always qualifies. No separate step to remember.

Sequence: `preflight` → `resolve-digests` (pins ~70 digests, publishes run-scoped
`qa-<run_id>-<key>` aliases) → `qa` matrix (max-parallel 4) → `qa-summary` (the
flip/hold table) → **approval** → `promote`.

**Approval comes after QA, by construction** — `promote` is the only job with
`environment: production` and it needs `qa-summary`. Read the flip/hold table
before approving; a `hold` means that tag keeps its current image.

**There is no bypass.** If QA cannot run, nothing reaches an `-auto` tag. To move a
tag by hand: promote first (dated tags land regardless of any hold), then use
`move-base-auto-tag.yml`, which is separately approved and logged.

Expect ~25 min for the QA phase. A cell landing on a pathologically slow host is
abandoned after 15 min and retried on another offer.

## 7. After promotion

- **Derivatives pin dated base tags** (`base-image:cuda-12.9-mini-py312-2026-06-15`
  and similar, e.g. `derivatives/llama-cpp/Dockerfile` and
  `.github/workflows/build-llama-cpp.yml`). They do **not**
  follow automatically. Bump them deliberately when they should move.
- For a **new minor**, confirm the Vast template side is expecting the new
  `-auto` tag before announcing it.

## 8. If it goes wrong

| symptom | meaning | action |
|---|---|---|
| `has no driver floor` | tag template version has no minor (e.g. `cuda-14-`) | fix the template in `configs/base-image.json`; nothing was written |
| `STAGING_DATE must be YYYY-MM-DD` | typo | re-dispatch |
| Some tags `HOLD` | QA did not clear those digests | read the reason column; those tags keep their current image, everything else promoted |
| `staging moved after the plan was approved` | someone rebuilt mid-promotion | re-dispatch against the current staging date |
| `no pinned digest for ... refusing to copy by mutable tag` | manifest incomplete | do **not** work around it; this is the guard that stops mutable-tag copies |
| Bad image already on an `-auto` tag | — | `move-base-auto-tag.yml` — production-approved, refuses non-production sources and CUDA-minor mismatches, and reports the digest it moved away from |

## 9. What a promotion does and does not certify

Say this plainly rather than implying broader coverage:

- **amd64 only** — the promoted index's arm64 manifest is never booted.
- **default-python only** — the other four pythons and the mini variants are
  promoted untested; they carry no auto tag.
- **single GPU, Turing or newer**, one box per config.
- **a point-in-time boot** — not a guarantee under load or over time.
