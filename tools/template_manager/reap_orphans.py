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
import urllib.error
import urllib.request

BASE_URL = "https://console.vast.ai/api/v0"


def _request(method, path, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode()
    return json.loads(text) if text else {}


def _age_min(inst):
    """Uptime in minutes, defensively coerced. None/missing/malformed -> 0 (keep)."""
    try:
        return float(inst.get("uptime_mins") or 0)
    except (TypeError, ValueError):
        return 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-age-min", type=float, default=180.0,
                    help="reap instances whose uptime exceeds this many minutes "
                         "(default 180 — comfortably above the max QA test duration)")
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
        in_scope = ((args.label is None or ilabel.startswith(args.label))
                    and (args.image_prefix is None or image.startswith(args.image_prefix)))
        if age > args.max_age_min and in_scope:
            candidates.append(inst)
            print(f"  CANDIDATE  {desc}")
        else:
            print(f"  keep       {desc}"
                  + ("" if in_scope else "  (out of QA scope)"))

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
        if args.destroy:
            try:
                _request("DELETE", f"/instances/{iid}/", key)
                print(f"  REAPED     id={iid}")
                reaped += 1
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                print(f"  FAILED     id={iid}: {e}")
                failures += 1
        else:
            print(f"  WOULD REAP id={iid}")
            reaped += 1

    print(f"{'reaped' if args.destroy else 'would reap'}: {reaped}/{len(instances)}")
    # A swallowed reap failure means a leaked paid instance persists; surface it so
    # a stuck reaper (e.g. repeated 429/5xx) alerts instead of silently exiting 0.
    if failures:
        print(f"::error::{failures} instance(s) failed to reap — still leaking; check the account.",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
