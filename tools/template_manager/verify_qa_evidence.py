#!/usr/bin/env python3
"""Decide which auto tags may flip, from QA evidence (ADR 0019 cond 2).

THE safety property of the base promotion gate:

    An auto tag may only move to a digest that PASSED live-GPU QA in this run,
    on that exact digest.

Everything else in the pipeline serves this. It is expressed over the *state
transition* rather than over configs, which is what makes the awkward cases fall
out for free:

  * A config whose digest is unchanged is not flipping, so it needs no evidence —
    the dance re-pushes identical content (push ORDER matters to Vast's
    automatic-tag backend, so every auto tag is re-pushed every run, changed or
    not; that is not a content change and must not demand a fresh test).
  * A FILTERed run only flips the configs it promotes; the rest are re-pushed at
    their pre-captured digest and likewise need nothing.
  * A config QA'd at digest D but about to be promoted at D' is caught, because
    the check is digest-to-digest and not "did some cell for this config pass".

Verdict per config:
  flip  — the auto tag moves to the new digest (evidence present and passing)
  hold  — the auto tag stays at its current digest (no/failed evidence, or the
          digest did not change anyway)

`hold` is deliberately not `abort`. One flaky cell out of twelve costing a whole
fleet promotion is how a gate gets routed around; holding one tag while the other
eleven proceed keeps the gate honest AND keeps it usable. Held tags are surfaced
loudly to the approver — that is the mitigation, not silence.
"""

from __future__ import annotations

import argparse
import json
import sys


def classify_configs(manifest: dict, evidence: dict, skip_qa: bool = False) -> list[dict]:
    """Return a per-config decision list.

    manifest: {"configs": [{"key","tag_template","auto_version","target_digest",
                            "current_digest"}]}
    evidence: {config_key: {"verdict": "pass"|..., "digest": "sha256:..."}}
    """
    out = []
    for c in manifest.get("configs", []):
        key = c["key"]
        target = c.get("target_digest")
        current = c.get("current_digest")
        auto = c.get("auto_version")

        if not auto:
            # stock-*: no CUDA version, so no auto tag exists to flip.
            out.append({**c, "decision": "n/a", "reason": "no auto tag for this config"})
            continue

        if not target:
            out.append({**c, "decision": "hold",
                        "reason": "no target digest resolved — staging source missing"})
            continue

        if target == current:
            # Not a flip: same content. Re-pushed for ordering, nothing to certify.
            out.append({**c, "decision": "flip", "reason": "digest unchanged (re-push only)"})
            continue

        if skip_qa:
            out.append({**c, "decision": "flip",
                        "reason": "SKIP_QA — flipped WITHOUT automated evidence"})
            continue

        ev = evidence.get(key)
        if not ev:
            out.append({**c, "decision": "hold",
                        "reason": "digest changed but no QA evidence for this config"})
            continue
        if ev.get("verdict") != "pass":
            out.append({**c, "decision": "hold",
                        "reason": f"QA verdict was {ev.get('verdict')!r}, not a pass"})
            continue
        if ev.get("digest") != target:
            # The tested bits are not the bits about to ship. Staging tags are
            # mutable, so this is a real possibility, not a theoretical one.
            out.append({**c, "decision": "hold",
                        "reason": (f"QA tested {str(ev.get('digest'))[:19]}… but the tag would "
                                   f"move to {str(target)[:19]}… — evidence does not cover "
                                   f"these bits")})
            continue

        out.append({**c, "decision": "flip", "reason": "QA passed on this exact digest"})
    return out


def render_summary(decisions: list[dict]) -> str:
    """Markdown for the approval summary — this is what a reviewer actually reads."""
    holds = [d for d in decisions if d["decision"] == "hold"]
    lines = ["## Base image promotion — auto-tag decisions", ""]
    if holds:
        lines += [f"> **{len(holds)} auto tag(s) will NOT be updated.** They keep serving their "
                  f"current image. Read the HOLD rows before approving.", ""]
    lines += ["| config | auto tag | decision | why |", "|---|---|---|---|"]
    for d in decisions:
        if d["decision"] == "n/a":
            continue
        mark = {"flip": "flip", "hold": "**HOLD**"}[d["decision"]]
        lines.append(f"| `{d['key']}` | `cuda-{d['auto_version']}-auto` | {mark} | {d['reason']} |")
    if any(d["reason"].startswith("SKIP_QA") for d in decisions):
        lines += ["", "> **SKIP_QA was used — these tags flip with NO automated evidence.**"]
    # ADR 0019 cond 5: what a pass does NOT cover, stated where the approver reads it
    # rather than only in the ADR. A green table means less than it looks like.
    lines += [
        "",
        "### What a `flip` here does and does not certify",
        "",
        "- **amd64 only.** The promoted artifact is a multi-arch index; its arm64",
        "  manifest was never booted.",
        "- **Default-python only.** The other four python variants per config, and the",
        "  mini variants, are promoted untested — they carry no auto tag and are",
        "  reached only by explicit dated reference.",
        "- **Single GPU, Turing or newer**, on one box in the selected VRAM band.",
        "- **A point-in-time boot**, not a runtime guarantee: a service that breaks",
        "  later, or under load, is out of scope.",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--evidence", required=True,
                    help="JSON map of config_key -> {verdict, digest}")
    ap.add_argument("--skip-qa", action="store_true")
    ap.add_argument("--out", help="write the decision list here")
    ap.add_argument("--summary", help="write the markdown summary here")
    args = ap.parse_args(argv)

    manifest = json.load(open(args.manifest, encoding="utf-8"))
    try:
        evidence = json.load(open(args.evidence, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        evidence = {}

    decisions = classify_configs(manifest, evidence, skip_qa=args.skip_qa)

    if args.out:
        json.dump(decisions, open(args.out, "w", encoding="utf-8"), indent=2)
    if args.summary:
        open(args.summary, "w", encoding="utf-8").write(render_summary(decisions))

    for d in decisions:
        if d["decision"] != "n/a":
            print(f"{d['decision']:5} cuda-{d['auto_version']}-auto  ({d['key']}): {d['reason']}")

    holds = sum(1 for d in decisions if d["decision"] == "hold")
    flips = sum(1 for d in decisions if d["decision"] == "flip")
    print(f"\n{flips} flip, {holds} hold")
    # Always exit 0: holding is a normal outcome, not a tool failure. The workflow
    # decides what to do with the decision list.
    return 0


if __name__ == "__main__":
    sys.exit(main())
