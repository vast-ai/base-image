#!/usr/bin/env python3
"""Reap orphaned QA instances (ADR 0005 condition 1 — the out-of-band reaper).

The live-GPU QA gate destroys its instance via ``test_template.py --destroy`` on
the normal path, but a GitHub runner that is hard-killed mid-test (cancellation,
timeout, eviction) can skip that and leak a paid instance. This is the backstop:
a scheduled sweep that destroys orphaned QA instances older than a threshold.

Scope is by the Vast **label** ``test_template.py --label`` stamps on every
instance it launches (``--label`` here, prefix-matched): a good non-test instance
carries no QA label and is therefore never reaped — so this does not rely on the
account being single-tenant. An image-prefix and a ``--max-reap`` ceiling provide
defence in depth, and ``--destroy`` refuses to run with no scope at all.

Reads ``VAST_API_KEY`` from the environment (the dedicated QA-account key).
Dry-run by default; pass ``--destroy`` to actually reap. Stdlib only.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "https://console.vast.ai/api/v0"

# The QA account is single-key and shared with concurrent QA runs, so the list
# call can 429 (observed live on the gate). A */30 sweep that crashed on the
# first 429 would mean the backstop never runs — so retry rate-limit/gateway
# errors with backoff. 404 is NOT here: destroy_instance treats it as "gone".
_RETRYABLE_STATUS = frozenset((429, 502, 503, 504))
_MAX_API_RETRIES = 5

# Worst-case lifetime of a HEALTHY QA instance, mirrored from test_template.py's
# auto-lifted client --timeout (prov + derivative + headroom; see
# test_template.py:1100). The instance bills for this whole window on a good run,
# so the reaper MUST stay above it or it deletes a live instance mid-test — a
# false BLOCK plus the wasted spend of the whole run. The coupling is enforced by
# tests/test_qa_reaper.py::test_default_threshold_stays_above_test_template_budget,
# which fails if test_template.py raises its budget past our margin.
_HEALTHY_TEST_PHASE_SEC = 3600 + 3600 + 600   # 7800s = 130 min
# A live instance also bills during poll-to-running + connectivity probing before
# the test phase and briefly during teardown, none of it counted above. Reaping
# latency is cheap (an orphan over-running on a sub-$0.50/hr QA GPU costs cents),
# but clipping a live run is expensive — so the default sits at 2x the test-phase
# ceiling. Override per-run with --max-age-min when a gated image needs more.
DEFAULT_MAX_AGE_MIN = (_HEALTHY_TEST_PHASE_SEC * 2) / 60   # 260 min


def in_scope(inst, label, image_prefix):
    """True if an instance falls within the QA reap scope.

    Label and image-prefix are each optional; when set they are ANDed (an
    instance must match every supplied filter). A non-QA instance carries no QA
    label, so a label scope excludes it. Returns True only for the intended
    targets — keep this the single definition of "in scope" so the dry-run report
    and the destroy path can never diverge.
    """
    ilabel = inst.get("label") or ""
    image = inst.get("image_uuid") or inst.get("image") or ""
    return ((label is None or ilabel.startswith(label))
            and (image_prefix is None or image.startswith(image_prefix)))


def is_reapable(inst, max_age_min, label, image_prefix):
    """An instance is reapable iff it is older than the threshold AND in scope."""
    return _age_min(inst) > max_age_min and in_scope(inst, label, image_prefix)


def select_candidates(instances, max_age_min, label, image_prefix):
    """The instances the reaper would destroy under this scope and threshold."""
    return [i for i in instances
            if is_reapable(i, max_age_min, label, image_prefix)]


def old_out_of_scope(instances, max_age_min, label, image_prefix):
    """Over-age instances EXCLUDED only by scope.

    On the single-tenant QA account these are the smoking gun for silent
    under-reaping: an orphan whose ``--label`` failed to round-trip, or an image
    string the ``--image-prefix`` AND no longer matches, ages forever while the
    reaper reports a clean ``reaped: 0``. Surfacing them turns that invisible
    no-op into a warning so a broken scope can't masquerade as a quiet account.
    """
    return [i for i in instances
            if _age_min(i) > max_age_min and not in_scope(i, label, image_prefix)]


def destroy_instance(iid, key):
    """Destroy one instance. Returns a (status, detail) pair:

      "reaped" — deleted by us;
      "gone"   — already destroyed (HTTP 404). A concurrent test_template
                 --destroy, a prior reaper pass, or a spot reclaim beat us to it;
                 that is the goal state, not a leak, so it must NOT count as a
                 failure (mirrors test_template.py's _request_safe teardown). A
                 404 that alarmed would train operators to ignore the one alert
                 that signals a real leak;
      "failed" — a genuine error (5xx, 429, transport) worth surfacing; detail
                 carries the message.
    """
    try:
        _request("DELETE", f"/instances/{iid}/", key)
        return "reaped", None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "gone", None
        return "failed", str(e)
    except urllib.error.URLError as e:
        return "failed", str(e)


def _request(method, path, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for attempt in range(_MAX_API_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode()
            return json.loads(text) if text else {}
        except urllib.error.HTTPError as e:
            if e.code not in _RETRYABLE_STATUS or attempt == _MAX_API_RETRIES:
                raise
            ra = e.headers.get("Retry-After") if e.headers else None
            delay = float(ra) if (ra and str(ra).strip().isdigit()) else min(2 ** attempt, 30)
            print(f"API {e.code} on {path} — retry {attempt + 1}/{_MAX_API_RETRIES} "
                  f"in {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)


def _age_min(inst):
    """Age in minutes. Prefer ``uptime_mins``; fall back to ``start_date`` (both
    in the show-instances response) because ``uptime_mins`` is documented as
    nullable — a leaked orphan reporting ``uptime_mins: null`` must still be aged
    (via start_date) and reaped, not coerced to 0 and left billing forever. When
    NEITHER is usable, return 0.0 so an un-ageable instance is not auto-deleted
    (fail-safe for deletion); ``_age_known`` lets the caller warn on that instead."""
    um = inst.get("uptime_mins")
    if um is not None:
        try:
            return float(um)
        except (TypeError, ValueError):
            pass
    sd = inst.get("start_date")
    if sd is not None:
        try:
            return max(0.0, (time.time() - float(sd)) / 60.0)
        except (TypeError, ValueError):
            pass
    return 0.0


def _age_known(inst):
    """False when neither ``uptime_mins`` nor ``start_date`` yields a usable age.
    Such an instance can't be reaped by age, so it must be surfaced rather than
    hidden behind a clean ``reaped: 0`` (the null-uptime silent-leak failure)."""
    for k in ("uptime_mins", "start_date"):
        v = inst.get(k)
        if v is not None:
            try:
                float(v)
                return True
            except (TypeError, ValueError):
                pass
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-age-min", type=float, default=DEFAULT_MAX_AGE_MIN,
                    help=f"reap instances whose uptime exceeds this many minutes "
                         f"(default {DEFAULT_MAX_AGE_MIN:.0f} — 2x the max healthy QA "
                         f"test lifetime, derived from test_template.py's budget)")
    ap.add_argument("--label", default=None,
                    help="ONLY reap instances whose Vast label starts with this — the "
                         "primary QA scope (test_template.py --label stamps it at launch). "
                         "A non-test instance has no QA label, so it is never reaped.")
    ap.add_argument("--image-prefix", default=None,
                    help="additional scope: only reap instances whose image starts with "
                         "this prefix (e.g. the staging namespace). ANDed with --label.")
    ap.add_argument("--allow-no-scope", action="store_true",
                    help="permit --destroy with NO --label and NO --image-prefix (age-only, "
                         "ALL images) — only safe on a verified single-tenant QA account.")
    ap.add_argument("--max-reap", type=int, default=30,
                    help="refuse to destroy if more than this many instances match — a "
                         "runaway backstop, NOT the primary guard (the --label scope is). "
                         "Sized above full-rollout QA concurrency (images x matrix cells x "
                         "in-flight); raise it as more images are gated. Abort + alert.")
    ap.add_argument("--destroy", action="store_true",
                    help="actually destroy matching instances (default: dry-run report)")
    args = ap.parse_args()

    # Treat an empty/whitespace filter as "no scope". An unset env/var that expands
    # to --label "" must NOT become startswith("") == match-everything and slip past
    # the fail-closed guard below (which would then reap EVERY over-age instance).
    args.label = args.label.strip() if args.label and args.label.strip() else None
    args.image_prefix = (args.image_prefix.strip()
                         if args.image_prefix and args.image_prefix.strip() else None)

    key = os.environ.get("VAST_API_KEY")
    if not key:
        print("VAST_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    # Fail-closed: never destroy unscoped by accident. Require a positive QA scope
    # (label or image prefix) so a mis-set key can't sweep good instances. An empty
    # secret that drops both filters is caught here rather than reaping ALL images.
    if args.destroy and args.label is None and args.image_prefix is None and not args.allow_no_scope:
        print("::error::--destroy requires --label (preferred) or --image-prefix. Pass "
              "--allow-no-scope only on a verified single-tenant QA account.",
              file=sys.stderr)
        sys.exit(1)

    instances = _request("GET", "/instances/?owner=me", key).get("instances", [])
    parts = []
    if args.label:
        parts.append(f"label prefix '{args.label}'")
    if args.image_prefix:
        parts.append(f"image prefix '{args.image_prefix}'")
    scope = " AND ".join(parts) if parts else "ALL images (age only)"
    print(f"{len(instances)} instance(s) on the account; threshold "
          f"{args.max_age_min:.0f} min; scope = {scope}")

    candidates = []
    for inst in instances:
        age = _age_min(inst)
        image = inst.get("image_uuid") or inst.get("image") or ""
        ilabel = inst.get("label") or ""
        desc = (f"id={inst.get('id')} {inst.get('gpu_name', '?')} {image} "
                f"label={ilabel or '-'} uptime={age:.0f}min status={inst.get('actual_status', '?')}")
        scoped = in_scope(inst, args.label, args.image_prefix)
        if age > args.max_age_min and scoped:
            candidates.append(inst)
            print(f"  CANDIDATE  {desc}")
        else:
            print(f"  keep       {desc}"
                  + ("" if scoped else "  (out of QA scope)"))

    # Canary for silent under-reaping: an over-age instance the scope EXCLUDED is,
    # on the single-tenant QA account, most likely an orphan whose label/prefix
    # stopped matching — exactly the failure a bare 'reaped: 0' would hide. Warn
    # (not error) so it surfaces without false-failing a legitimately quiet sweep.
    stale_unscoped = old_out_of_scope(instances, args.max_age_min, args.label, args.image_prefix)
    if stale_unscoped:
        ids = ", ".join(str(i.get("id")) for i in stale_unscoped)
        print(f"::warning::{len(stale_unscoped)} over-age instance(s) are OUT of QA scope "
              f"(id={ids}) — if these are leaked QA orphans the label/prefix is not matching; "
              f"verify before trusting 'reaped: 0'.", file=sys.stderr)

    # Second silent-leak guard, independent of the age coercion: an IN-scope
    # instance whose age can't be determined (both uptime_mins and start_date
    # unusable) can't be reaped by age — flag it so a null-age orphan doesn't hide
    # behind 'reaped: 0'.
    unknown_age = [i for i in instances
                   if in_scope(i, args.label, args.image_prefix) and not _age_known(i)]
    if unknown_age:
        ids = ", ".join(str(i.get("id")) for i in unknown_age)
        print(f"::warning::{len(unknown_age)} in-scope instance(s) have no usable age "
              f"(id={ids}) — cannot be reaped by age; check them manually for a leak.",
              file=sys.stderr)

    # Fail-closed: a count far above what QA ever produces means the key is
    # pointed at the wrong (e.g. production) account — abort rather than wipe it.
    if args.destroy and len(candidates) > args.max_reap:
        print(f"::error::refusing to reap {len(candidates)} instances "
              f"(> --max-reap {args.max_reap}) — likely a mis-scoped key/account. Aborting.",
              file=sys.stderr)
        sys.exit(1)

    reaped = 0
    failures = 0
    for inst in candidates:
        iid = inst.get("id")
        if not args.destroy:
            print(f"  WOULD REAP id={iid}")
            reaped += 1
            continue
        status, detail = destroy_instance(iid, key)
        if status == "reaped":
            print(f"  REAPED     id={iid}")
            reaped += 1
        elif status == "gone":
            print(f"  GONE       id={iid} (already destroyed)")
            reaped += 1
        else:
            print(f"  FAILED     id={iid}: {detail}")
            failures += 1

    print(f"{'reaped' if args.destroy else 'would reap'}: {reaped}/{len(instances)}")
    # A swallowed reap failure means a leaked paid instance persists; surface it so
    # a stuck reaper (e.g. repeated 429/5xx) alerts instead of silently exiting 0.
    if failures:
        print(f"::error::{failures} instance(s) failed to reap — still leaking; check the account.",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
