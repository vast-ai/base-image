"""The torch version lifecycle policy, enforced (ADR 0022).

Two rules decide what the pytorch build matrix carries:

  1. ONE PATCH PER MINOR — the newest we have adopted. A new patch supersedes
     the old one in place rather than sitting alongside it. This was an implicit
     convention long before it was written down (the table has always carried
     2.7.1 and not 2.7.0, 2.9.1 and not 2.9.0); it was noticed only when adding
     2.12.1 alongside 2.12.0 broke it.

  2. SUPPORT FLOOR — the oldest torch minor that any derivative in this repo
     pins. Anything below it is retired: we stop minting new dated tags for it.

Rule 2 is deliberately derived rather than a hand-set number. A "keep the last
N minors" rule would have retired 2.7, which five derivative images pin, while
keeping 2.8, which nothing uses. Consumption is the thing that matters, so
consumption sets the floor — and the floor rises on its own as derivatives move
forward, with no one having to remember to raise it.

RETIREMENT IS NOT DELETION. Every dated tag already published stays pullable
forever. Retiring a version only stops new ones being minted, so nothing a user
already runs can break.

THE DIRECTION THAT ACTUALLY BITES is not "we kept something too long" — that is
merely wasteful. It is retiring a version something still depends on, which
breaks a derivative build with no warning. test_no_pinned_version_is_retired is
the one that must never be weakened.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TABLE = json.loads((REPO / "configs/pytorch.json").read_text())

vkey = lambda v: [int(x) for x in v.split(".")]
minor = lambda v: ".".join(v.split(".")[:2])
mkey = lambda m: [int(x) for x in m.split(".")]


def pinned_minors() -> dict[str, list[str]]:
    """{torch minor: [who pins it]} across derivative Dockerfiles and workflows.

    Read from the files rather than a list, so the floor tracks reality. A pin
    added or bumped anywhere changes what this test permits, in the same commit.
    """
    out: dict[str, list[str]] = defaultdict(list)
    globs = ("derivatives/pytorch/derivatives/*/Dockerfile",
             ".github/workflows/build-*.yml")
    for pat in globs:
        for f in sorted(REPO.glob(pat)):
            text = f.read_text(encoding="utf-8", errors="replace")
            for ver, _be in re.findall(r"vastai/pytorch:([0-9.]+)-([a-z0-9]+)-cuda", text):
                out[minor(ver)].append(f.name if f.name != "Dockerfile" else f.parent.name)
    # the multi image is built ON a mini of its primary torch
    out[minor(TABLE["multi"][0]["primary_torch"])].append("multi-torch base")
    return out


def support_floor() -> str:
    pins = pinned_minors()
    assert pins, "no derivative pins found — the floor would be undefined"
    return min(pins, key=mkey)


def table_minors() -> list[str]:
    return sorted({minor(c["torch"]) for c in TABLE["configs"]}, key=mkey)


# --- rule 1: one patch per minor -------------------------------------------

def test_each_minor_line_carries_exactly_one_patch():
    """Two patches of one minor doubles the artifacts and the QA cells for a
    version pair that differs by a bugfix. It also makes "which 2.12 do I get"
    ambiguous for anyone reading the tag list."""
    by_minor: dict[str, set[str]] = defaultdict(set)
    for c in TABLE["configs"]:
        by_minor[minor(c["torch"])].add(c["torch"])
    for m, patches in sorted(by_minor.items()):
        assert len(patches) == 1, (
            f"minor {m} carries {sorted(patches, key=vkey)}. A new patch must "
            f"SUPERSEDE the old one, not sit beside it.")


def test_mini_and_config_agree_on_the_patch():
    """A mini built from a different patch than its config sibling would ship a
    torch version no config image corresponds to — invisible until someone
    compared two tag lists by hand."""
    cfg = {minor(c["torch"]): c["torch"] for c in TABLE["configs"]}
    for m in TABLE["mini"]:
        want = cfg.get(minor(m["torch"]))
        assert want is not None, f"mini {m['torch']}/{m['key']} has no config sibling"
        assert m["torch"] == want, (
            f"mini carries {m['torch']} but the config for that minor is {want}")


# --- rule 2: the support floor ---------------------------------------------

def test_nothing_below_the_support_floor_is_still_built():
    floor = support_floor()
    stale = [m for m in table_minors() if mkey(m) < mkey(floor)]
    assert not stale, (
        f"minor(s) {stale} are below the support floor {floor} (the oldest "
        f"version any derivative pins) and should be retired. Existing dated "
        f"tags stay pullable; only new ones stop.")


def test_no_pinned_version_is_retired():
    """THE safety property. Retiring a version a derivative still pins breaks
    that derivative's build at its next run, with no warning and no obvious
    cause. This is the direction that must never be weakened."""
    have = set(table_minors())
    missing = {m: who for m, who in pinned_minors().items() if m not in have}
    assert not missing, (
        "these torch minors are pinned but no longer built: "
        + "; ".join(f"{m} (pinned by {', '.join(sorted(set(who)))})"
                    for m, who in sorted(missing.items()))
        + ". Bump the pins first, THEN retire.")


def test_the_floor_is_derived_from_real_pins_not_a_constant():
    """Guard the guard. If the pin extraction silently stopped matching, the
    floor would collapse and every rule above would pass vacuously."""
    pins = pinned_minors()
    assert len(pins) >= 2, f"only found pins for {sorted(pins)} — extraction has rotted"
    assert all(who for who in pins.values()), "a pinned minor with no named source"


def test_the_floor_is_actually_load_bearing():
    """The floor should be doing work: if it sat below everything in the table
    it would permit anything, and its passing would mean nothing."""
    floor, ms = support_floor(), table_minors()
    assert floor in ms, f"support floor {floor} is not itself built — pins point at nothing"


# --- the retired set stays retired -----------------------------------------

RETIRED = {"2.6"}   # ADR 0022; extend deliberately, never silently


@pytest.mark.parametrize("m", sorted(RETIRED))
def test_a_retired_minor_does_not_come_back_by_accident(m):
    """Re-adding a retired version should be a decision with a reason, not the
    side effect of copying a nearby table entry."""
    assert m not in table_minors(), (
        f"torch {m} was retired (ADR 0022) but is in the table again. If that "
        f"is intended, remove it from RETIRED here and say why in the ADR.")


def test_retired_minors_are_genuinely_unused():
    """A retired version that something still pins is the bug this whole file
    exists to prevent; assert it from the retired side too."""
    pins = pinned_minors()
    clash = {m: pins[m] for m in RETIRED if m in pins}
    assert not clash, f"retired minors are still pinned: {clash}"


# --- the SECOND source of truth: torch-companions.json ---------------------
#
# Found the hard way. Adding a version to configs/pytorch.json is NOT enough:
# the Dockerfile also reads derivatives/pytorch/torch-companions.json to pin
# torchvision/torchcodec/etc, via
#
#     jq '... if .[$v] == null then error("version not found") ...'
#
# so a missing entry fails the BUILD with jq exit code 5 and the message
# "jq: error: version not found" — after pulling the base image, and with
# nothing pointing at the file that is actually wrong. It cost a real CI run to
# find, on a version added the same day.
#
# Two files that must agree and no check that they do is exactly the drift the
# config-table extraction was done to remove, so it is checked here.

COMPANIONS = json.loads((REPO / "derivatives/pytorch/torch-companions.json").read_text())


def test_every_built_torch_version_has_a_companions_entry():
    """THE assertion. Missing => the build dies late with an opaque jq error."""
    want = {c["torch"] for c in TABLE["configs"]} | {m["torch"] for m in TABLE["mini"]}
    want |= {u["primary_torch"] for u in TABLE["multi"]}
    missing = sorted(want - set(COMPANIONS), key=vkey)
    assert not missing, (
        f"torch {missing} are in configs/pytorch.json but not in "
        f"torch-companions.json. The build will fail with `jq: error: version "
        f"not found` (exit 5) after pulling the base image.")


def test_companions_has_no_entries_for_versions_we_no_longer_build():
    """The other direction. A stale entry is harmless to the build but it is a
    lie about what is supported, and it is how a retired version quietly looks
    live to whoever reads this file next."""
    built = {c["torch"] for c in TABLE["configs"]} | {m["torch"] for m in TABLE["mini"]}
    stale = sorted(set(COMPANIONS) - built, key=vkey)
    assert not stale, (
        f"torch-companions.json still carries {stale}, which nothing builds. "
        f"Remove them when retiring a version (ADR 0022).")


def test_every_companions_entry_is_well_formed():
    """A typo'd key produces a confusing runtime failure rather than a clear one:
    `.packages` missing makes the jq emit nothing, so PACKAGES is just `torch==X`
    and the companions are silently NOT installed — the image ships without
    torchvision and every test that imports it fails much later."""
    for ver, entry in sorted(COMPANIONS.items(), key=lambda kv: vkey(kv[0])):
        assert isinstance(entry.get("packages"), dict) and entry["packages"], \
            f"{ver}: no non-empty .packages — companions would silently not install"
        assert isinstance(entry.get("amd64_only", []), list), f"{ver}: amd64_only not a list"
        for pkg, pin in entry["packages"].items():
            # X.Y is tolerated because 2.7.1 pins torchcodec "0.5" and that
            # version is consumed by five derivatives — tightening it is a
            # separate, riskier change. Noting the consequence rather than
            # silently accepting it: the Dockerfile verifies the install with a
            # PREFIX match (`[[ "${actual}" == "${ver}"* ]]`), so "0.5" would
            # also accept a hypothetical 0.50.0. Prefer X.Y.Z in new entries.
            assert re.fullmatch(r"\d+\.\d+(\.\d+)?", pin), \
                f"{ver}/{pkg}: pin {pin!r} is not X.Y or X.Y.Z"
