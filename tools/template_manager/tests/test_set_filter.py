"""--set-filter is RAISE-ONLY (ADR 0019).

CI raises a per-config floor at rent time — each base config wants its own
cuda_max_good — but the direction must be one-way. L050/L054 verify the floors in
the committed template; if a workflow argument could lower or delete one, that
static guarantee would say nothing about what actually gets rented, and the gate
could quietly select a box the template promises it never would.

So: tightening is a CI concern, widening is a reviewed edit to template.yml.
"""

from __future__ import annotations

import pytest

from create import apply_set_filters, parse_set_filter


class T:
    """Minimal stand-in for VastTemplate — only extra_filters is read/written."""
    def __init__(self, extra_filters=None):
        self.extra_filters = extra_filters


# --- parsing ---------------------------------------------------------------

def test_parses_key_op_value():
    assert parse_set_filter("cuda_max_good.gte=13.0") == ("cuda_max_good", "gte", 13.0)


def test_parses_dotted_key():
    assert parse_set_filter("a.b.gte=1") == ("a.b", "gte", 1.0)


@pytest.mark.parametrize("bad", [
    "cuda_max_good=13.0",        # no op
    "cuda_max_good.gte",         # no value
    "cuda_max_good.between=1",   # unknown op
    "cuda_max_good.gte=high",    # non-numeric
])
def test_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_set_filter(bad)


# --- the raise-only rule ---------------------------------------------------

def test_raises_an_existing_floor():
    t = T({"cuda_max_good": {"gte": 11.8}})
    apply_set_filters(t, ["cuda_max_good.gte=13.0"])
    assert t.extra_filters["cuda_max_good"]["gte"] == 13.0


def test_refuses_to_lower_a_floor():
    """THE rule. Lowering would let CI rent below what the linter verified."""
    t = T({"cuda_max_good": {"gte": 13.0}})
    with pytest.raises(ValueError, match="WIDEN"):
        apply_set_filters(t, ["cuda_max_good.gte=11.8"])


def test_refuses_to_raise_an_upper_bound():
    """lte/lt bound selection from above; raising one widens the band."""
    t = T({"gpu_ram": {"lte": 24000}})
    with pytest.raises(ValueError, match="WIDEN"):
        apply_set_filters(t, ["gpu_ram.lte=80000"])


def test_lowering_an_upper_bound_is_tightening():
    t = T({"gpu_ram": {"lte": 24000}})
    apply_set_filters(t, ["gpu_ram.lte=16000"])
    assert t.extra_filters["gpu_ram"]["lte"] == 16000


def test_equal_value_is_allowed():
    """Idempotent re-application must not fail a re-run."""
    t = T({"cuda_max_good": {"gte": 13.0}})
    apply_set_filters(t, ["cuda_max_good.gte=13.0"])
    assert t.extra_filters["cuda_max_good"]["gte"] == 13.0


def test_adds_a_floor_that_was_absent():
    """No prior value means nothing is being widened."""
    t = T({"compute_cap": {"gte": 750}})
    apply_set_filters(t, ["cuda_max_good.gte=12.0"])
    assert t.extra_filters["cuda_max_good"] == {"gte": 12.0}
    assert t.extra_filters["compute_cap"] == {"gte": 750}, "other floors disturbed"


def test_scalar_form_floor_is_understood():
    """A template may write `compute_cap: 750` rather than a dict."""
    t = T({"compute_cap": 750})
    with pytest.raises(ValueError, match="WIDEN"):
        apply_set_filters(t, ["compute_cap.gte=700"])


def test_other_ops_on_the_same_key_are_preserved():
    """Raising the lower bound must not drop an existing upper bound — losing the
    ceiling would let a small claim be tested on a huge box (ADR 0005)."""
    t = T({"gpu_ram": {"gte": 8192, "lte": 24576}})
    apply_set_filters(t, ["gpu_ram.gte=16384"])
    assert t.extra_filters["gpu_ram"] == {"gte": 16384, "lte": 24576}


def test_multiple_specs_apply_in_order():
    t = T({"compute_cap": {"gte": 750}, "cuda_max_good": {"gte": 11.8}})
    apply_set_filters(t, ["cuda_max_good.gte=13.0", "compute_cap.gte=800"])
    assert t.extra_filters["cuda_max_good"]["gte"] == 13.0
    assert t.extra_filters["compute_cap"]["gte"] == 800


def test_none_and_empty_are_noops():
    t = T({"compute_cap": {"gte": 750}})
    apply_set_filters(t, None)
    apply_set_filters(t, [])
    assert t.extra_filters == {"compute_cap": {"gte": 750}}


def test_template_without_extra_filters_gains_them():
    t = T(None)
    apply_set_filters(t, ["cuda_max_good.gte=13.0"])
    assert t.extra_filters == {"cuda_max_good": {"gte": 13.0}}


# --- the real base template ------------------------------------------------

def test_the_per_config_raises_the_base_gate_will_make_are_all_legal():
    """Every value CI will pass for the 12 configs must be a tightening of the
    committed floor — otherwise the gate would be rejected at rent time."""
    from pathlib import Path
    import yaml
    repo = Path(__file__).resolve().parents[3]
    tpl = yaml.safe_load((repo / "templates/base-qa/template.yml").read_text())
    baseline = tpl["extra_filters"]["cuda_max_good"]["gte"]
    for value in (11.8, 12.0, 13.0):        # the major-baseline ladder
        t = T({"cuda_max_good": {"gte": baseline}})
        apply_set_filters(t, [f"cuda_max_good.gte={value}"])
        assert t.extra_filters["cuda_max_good"]["gte"] == value
