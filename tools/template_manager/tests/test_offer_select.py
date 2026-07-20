"""Offer selection (ADR 0005): VRAM-primary, compute_cap floor, bounded search."""
import json

import pytest

from test_template import (VRAM_CEILING_MULTIPLIER, _coerce_extra_filters,
                           _required_floor, apply_vram_ceiling,
                           make_offer_sort_key)


def _o(cc=800, total_mb=24576, per_gpu_mb=24576, num_gpus=1, dph=0.5):
    return {"compute_cap": cc, "gpu_total_ram": total_mb, "gpu_ram": per_gpu_mb,
            "num_gpus": num_gpus, "dph_total": dph, "inet_down": 1000, "inet_up": 1000}


def _pick(offers, *, cap=None, total=None, per_gpu=None):
    return sorted(offers, key=make_offer_sort_key(total, per_gpu, cap))


def test_required_floor_shapes():
    assert _required_floor({"compute_cap": {"gte": 700}}, "compute_cap") == 700.0
    assert _required_floor({"compute_cap": {"gt": 800}}, "compute_cap") == 800.0
    assert _required_floor({"compute_cap": {"eq": 900}}, "compute_cap") == 900.0
    assert _required_floor({"compute_cap": 750}, "compute_cap") == 750.0
    assert _required_floor({}, "compute_cap") is None


def test_smallest_vram_above_floor_wins():
    offers = [_o(total_mb=80000), _o(total_mb=16000), _o(total_mb=24000)]
    assert _pick(offers, total=8192)[0]["gpu_total_ram"] == 16000


def test_below_vram_floor_sinks():
    too_small = _o(total_mb=4096)   # below an 8GB floor
    ok = _o(total_mb=16000)
    assert _pick([too_small, ok], total=8192)[0] is ok


def test_vram_dominates_compute_cap():
    # VRAM is primary: a small high-cc box beats a large low-cc box.
    small_high = _o(cc=890, total_mb=16000)
    big_low = _o(cc=800, total_mb=80000)
    assert _pick([big_low, small_high], cap=700, total=8192)[0] is small_high


def test_compute_cap_breaks_vram_ties():
    # Within one VRAM size, prefer the lowest compute_cap above the floor.
    a = _o(cc=900, total_mb=24000)
    b = _o(cc=800, total_mb=24000)
    assert _pick([a, b], cap=700, total=8192)[0] is b


def test_below_compute_floor_sinks_to_end():
    below = _o(cc=600, total_mb=16000)   # below a 700 cc floor
    ok = _o(cc=890, total_mb=16000)
    assert _pick([below, ok], cap=700, total=8192)[0] is ok


def test_vram_ceiling_adds_lte_when_only_gte():
    out = apply_vram_ceiling({"gpu_total_ram": {"gte": 12288}}, 2.0)
    assert out["gpu_total_ram"] == {"gte": 12288, "lte": 24576.0}


def test_vram_ceiling_respects_explicit_lte():
    f = {"gpu_total_ram": {"gte": 12288, "lte": 16384}}
    assert apply_vram_ceiling(f, 2.0) == f


def test_vram_ceiling_noop_without_floor():
    assert apply_vram_ceiling({"verified": {"eq": True}}, 2.0) == {"verified": {"eq": True}}


def test_vram_ceiling_does_not_mutate_input():
    f = {"gpu_total_ram": {"gte": 12288}}
    apply_vram_ceiling(f, 2.0)
    assert f == {"gpu_total_ram": {"gte": 12288}}  # original unchanged


def test_production_multiplier_admits_consumer_tier_excludes_datacenter():
    # The cost guardrail (now the price filter is gone): with the shipped
    # multiplier, an 8 GB floor must reach the abundant 24 GB consumer tier
    # (RTX 3090/4090) to keep the market from going thin, but must NOT reach the
    # 32 GB+ datacenter cards (V100-32 / A100 / H100). If someone widens the
    # multiplier into datacenter range, this fails.
    floor_mb = 8192
    ceiling_mb = apply_vram_ceiling(
        {"gpu_total_ram": {"gte": floor_mb}}, VRAM_CEILING_MULTIPLIER
    )["gpu_total_ram"]["lte"]
    assert ceiling_mb >= 24576       # includes a 24 GB 4090
    assert ceiling_mb < 32768        # excludes a 32 GB datacenter card


# --- _coerce_extra_filters: template-controlled input must fail cleanly --------

def test_coerce_extra_filters_dict_passthrough():
    assert _coerce_extra_filters({"compute_cap": {"gte": 800}}) == {"compute_cap": {"gte": 800}}


def test_coerce_extra_filters_json_string():
    assert _coerce_extra_filters('{"gpu_total_ram": {"gte": 8192}}') == {"gpu_total_ram": {"gte": 8192}}


def test_coerce_extra_filters_empty_forms():
    assert _coerce_extra_filters("") == {}
    assert _coerce_extra_filters(None) == {}
    assert _coerce_extra_filters("{}") == {}


def test_coerce_extra_filters_malformed_json_raises():
    with pytest.raises(json.JSONDecodeError):
        _coerce_extra_filters("{not json")


def test_coerce_extra_filters_non_object_raises():
    # valid JSON, but not an object — would crash _required_floor() downstream
    with pytest.raises(ValueError):
        _coerce_extra_filters("[1, 2, 3]")
    with pytest.raises(ValueError):
        _coerce_extra_filters("42")


def test_coerce_extra_filters_falsy_non_dict_raises():
    # A falsy NON-string ([], 0, False) must be rejected like [1,2], NOT swallowed
    # into {} — else a required compute_cap/VRAM floor is silently dropped.
    for bad in ([], 0, False):
        with pytest.raises(ValueError):
            _coerce_extra_filters(bad)


# ── download-aware tie-break for big models ────────────────────────────────
# Among offers that are otherwise equal (VRAM, compute_cap, num_gpus), rank by
# estimated download cost when a model size is known, so a big pull doesn't
# land on a slow-bandwidth box.

from test_template import _download_cost


def _net(offer_id, inet_down, dph, dl_cost):
    o = _o(cc=900, total_mb=1123000, per_gpu_mb=140000, num_gpus=8, dph=dph)
    o.update(id=offer_id, inet_down=inet_down, inet_up=1000, inet_down_cost=dl_cost)
    return o


def test_disk_aware_prefers_fast_host_for_big_model():
    """The 8xH200 case we hit: fast host is metered, slow host is cheap+near-
    unmetered.  Legacy score picks the slow one (penalised on egress); the
    disk-aware tie-break picks the fast one (idle GPU-time dominates)."""
    slow = _net(1, inet_down=826, dph=31.0, dl_cost=0.0026)
    fast = _net(2, inet_down=8441, dph=53.0, dl_cost=0.019)
    disk_aware = sorted([slow, fast],
                        key=make_offer_sort_key(1100000, None, 900, disk_gb=750))
    assert disk_aware[0]["id"] == 2          # fast host for a 750 GB pull
    legacy = sorted([slow, fast], key=make_offer_sort_key(1100000, None, 900))
    assert legacy[0]["id"] == 1              # the bug: legacy picks the slow host


def test_download_cost_weights_idle_gpu_time():
    # 3600 GB/hr link, 3600 GB pull → 1.0 h idle at $10/hr = $10, egress free.
    assert _download_cost(_net(1, inet_down=8000, dph=10.0, dl_cost=0.0),
                          disk_gb=3600) == pytest.approx(10.0)
    # Unknown/zero bandwidth sorts last.
    assert _download_cost(_net(2, inet_down=0, dph=1.0, dl_cost=0.0),
                          disk_gb=100) == float("inf")


def test_disk_aware_is_opt_in():
    """No disk_gb → legacy inet/price tie-break (unchanged behaviour)."""
    a = _net(1, inet_down=1000, dph=1.0, dl_cost=0.0)
    b = _net(2, inet_down=9000, dph=1.0, dl_cost=0.0)
    # legacy: higher inet wins the tie-break
    assert sorted([a, b], key=make_offer_sort_key(1100000, None, 900))[0]["id"] == 2
