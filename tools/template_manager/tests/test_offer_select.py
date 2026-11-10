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


# ── run-cost tie-break for big models ──────────────────────────────────────
# Among offers otherwise equal (VRAM, compute_cap, num_gpus), rank by estimated
# total run cost when a model size is known, so a big pull avoids slow-bandwidth
# boxes without overpaying for a much-pricier fast one.

from test_template import _offer_run_cost


def _net(offer_id, inet_down, dph, dl_cost):
    o = _o(cc=900, total_mb=1123000, per_gpu_mb=140000, num_gpus=8, dph=dph)
    o.update(id=offer_id, inet_down=inet_down, inet_up=1000, inet_down_cost=dl_cost)
    return o


def test_run_cost_prefers_fast_cheap_not_fast_pricey():
    """8xH200, 750 GB model. A fast *unmetered* box wins; a slow-cheap box loses
    (GPU-hours burned pulling); and a much-pricier fast box ALSO loses — the rate
    backstop (it's billed its rate for the whole run, not just the fast pull)."""
    slow_cheap = _net(1, inet_down=826, dph=31.0, dl_cost=0.0026)   # ~$127 (2h pull)
    fast_cheap = _net(2, inet_down=6470, dph=33.7, dl_cost=0.0)     # ~$76 unmetered
    fast_pricey = _net(3, inet_down=8441, dph=53.0, dl_cost=0.019)  # ~$131
    key = make_offer_sort_key(1100000, None, 900, disk_gb=750)
    assert sorted([slow_cheap, fast_cheap, fast_pricey], key=key)[0]["id"] == 2
    # the fastest box (pricey) must NOT win over the cheaper fast one — the risk
    # the red-team flagged when --max-price is unset.
    assert sorted([fast_cheap, fast_pricey], key=key)[0]["id"] == 2


def test_run_cost_fixes_the_slow_host_bug():
    """The exact session bug: legacy over-penalises a fast host's egress cost and
    picks the slow one; the run-cost tie-break picks the fast one (its shorter
    download outweighs its rate over the backstop window)."""
    slow = _net(1, inet_down=826, dph=31.0, dl_cost=0.0026)     # legacy favourite
    fast = _net(2, inet_down=8441, dph=53.0, dl_cost=0.019)     # metered+pricey
    assert sorted([slow, fast],
                  key=make_offer_sort_key(1100000, None, 900, disk_gb=750))[0]["id"] == 2
    assert sorted([slow, fast],
                  key=make_offer_sort_key(1100000, None, 900))[0]["id"] == 1  # legacy bug


def test_offer_run_cost_counts_download_plus_backstop():
    # 3600 GB/hr link, 3600 GB pull → 1.0 h download; + 2.0 h backstop = 3.0 h at
    # $10/hr = $30, egress free.
    assert _offer_run_cost(_net(1, inet_down=8000, dph=10.0, dl_cost=0.0),
                           disk_gb=3600, test_hours=2.0) == pytest.approx(30.0)
    # Unknown/zero bandwidth sorts last.
    assert _offer_run_cost(_net(2, inet_down=0, dph=1.0, dl_cost=0.0),
                           disk_gb=100) == float("inf")


def test_disk_aware_is_opt_in():
    """No disk_gb → legacy inet/price tie-break (unchanged behaviour)."""
    a = _net(1, inet_down=1000, dph=1.0, dl_cost=0.0)
    b = _net(2, inet_down=9000, dph=1.0, dl_cost=0.0)
    assert sorted([a, b], key=make_offer_sort_key(1100000, None, 900))[0]["id"] == 2


def test_tie_break_deterministic_when_all_inf():
    """No offer reports inet_down → every _offer_run_cost is inf; the offer-id
    final key keeps ordering deterministic instead of raw API order."""
    a = _net(2, inet_down=0, dph=1.0, dl_cost=0.0)
    b = _net(1, inet_down=0, dph=1.0, dl_cost=0.0)
    key = make_offer_sort_key(1100000, None, 900, disk_gb=750)
    assert [o["id"] for o in sorted([a, b], key=key)] == [1, 2]
    assert [o["id"] for o in sorted([b, a], key=key)] == [1, 2]


# ── robustness: bad template/env values must not crash the sort ─────────────

from test_template import _env_float


def test_invalid_disk_gb_falls_back_to_legacy():
    """A missing/non-numeric/<=0 disk_gb (bad recommended_disk_space) must not
    crash the sort — it disables the run-cost tie-break and uses legacy."""
    a = _net(1, inet_down=1000, dph=1.0, dl_cost=0.0)
    b = _net(2, inet_down=9000, dph=1.0, dl_cost=0.0)
    legacy = [o["id"] for o in sorted([a, b], key=make_offer_sort_key(1100000, None, 900))]
    for bad in ("not-a-number", 0, -5, None,
                float("nan"), float("inf"), float("-inf")):
        got = [o["id"] for o in
               sorted([a, b], key=make_offer_sort_key(1100000, None, 900, disk_gb=bad))]
        assert got == legacy


def test_env_float_defensive(monkeypatch):
    monkeypatch.delenv("QA_TEST_X", raising=False)
    assert _env_float("QA_TEST_X", 1.0) == 1.0     # unset → default
    monkeypatch.setenv("QA_TEST_X", "abc")
    assert _env_float("QA_TEST_X", 1.0) == 1.0     # non-numeric → default (no crash)
    monkeypatch.setenv("QA_TEST_X", "-3")
    assert _env_float("QA_TEST_X", 1.0) == 0.0     # clamped to >= 0
    monkeypatch.setenv("QA_TEST_X", "2.5")
    assert _env_float("QA_TEST_X", 1.0) == 2.5


# ---------------------------------------------------------------- --min-inet / --min-reliability

import sys

import test_template as tt


def test_offer_floors_only_tighten():
    base = {"inet_down": {"gte": 500}, "reliability2": {"gte": 0.95, "lte": 1.0}}
    out = tt.apply_offer_floors(base, min_inet=800, min_reliability=0.9)
    assert out["inet_down"] == {"gte": 800}                         # raised
    assert out["reliability2"] == {"gte": 0.95, "lte": 1.0}         # template's higher floor kept
    assert base == {"inet_down": {"gte": 500}, "reliability2": {"gte": 0.95, "lte": 1.0}}


def test_offer_floors_are_added_when_the_template_has_none():
    assert tt.apply_offer_floors({}, min_inet=300, min_reliability=0.97) == {
        "inet_down": {"gte": 300}, "reliability2": {"gte": 0.97}}


def test_no_floor_options_leave_the_filters_exactly_as_they_were():
    base = {"verified": {"eq": True}, "inet_down": {"gte": 500}}
    assert tt.apply_offer_floors(base) == base


@pytest.mark.parametrize("flag,value", [("--min-inet", "0"), ("--min-inet", "nan"),
                                        ("--min-reliability", "1.5"),
                                        ("--min-reliability", "-0.1")])
def test_a_bad_floor_is_a_usage_error(monkeypatch, flag, value):
    monkeypatch.setattr(sys, "argv", ["test_template.py", "abc123", flag, value])
    with pytest.raises(SystemExit) as e:
        tt.parse_args()
    assert e.value.code == tt.EXIT_CONFIG_ERROR


class _SearchAPI:
    """Just enough of VastAPI for main() to reach the offer search on --dry-run."""
    searched = []

    def __init__(self, *a, **k):
        pass

    def get_template(self, h):
        return {"image": "vastai/base-image:v1", "env": "",
                "extra_filters": json.dumps({"inet_down": {"gte": 500},
                                             "reliability2": {"gte": 0.95}}),
                "recommended_disk_space": 40}

    def search_offers(self, filters):
        _SearchAPI.searched.append(filters)
        return {"offers": [{"id": 1, "gpu_name": "RTX A4000", "num_gpus": 1,
                            "gpu_total_ram": 16384, "gpu_ram": 16384, "compute_cap": 860,
                            "dph_total": 0.2, "inet_down": 900, "inet_up": 900}]}

    def __getattr__(self, name):                    # anything else main() asks for
        return lambda *a, **k: {}


def _dry_run(monkeypatch, *extra):
    _SearchAPI.searched = []
    monkeypatch.setattr(sys, "stderr", sys.stderr)             # main() wraps it; restore after
    monkeypatch.setattr(tt, "VastAPI", _SearchAPI)
    monkeypatch.setattr(sys, "argv", ["test_template.py", "abc123", "--api-key", "k",
                                      "--dry-run", *extra])
    tt.main()
    return _SearchAPI.searched


def test_without_the_options_the_search_is_unchanged(monkeypatch):
    (filters,) = _dry_run(monkeypatch)
    assert filters["inet_down"] == {"gte": 500} and filters["reliability2"] == {"gte": 0.95}


def test_the_options_reach_the_launchers_own_search(monkeypatch):
    (filters,) = _dry_run(monkeypatch, "--min-inet", "1000", "--min-reliability", "0.99")
    assert filters["inet_down"] == {"gte": 1000} and filters["reliability2"] == {"gte": 0.99}


def test_offer_warns_that_the_templates_filters_are_bypassed(monkeypatch, capsys):
    searched = _dry_run(monkeypatch, "--offer", "4242")
    assert searched == []
    err = capsys.readouterr().err
    assert "WARNING: --offer 4242 skips the offer search" in err and "not applied" in err


def test_main_leaves_no_atexit_handler_in_the_pytest_process(monkeypatch, _no_atexit_handlers_leak):
    """The guard for conftest's atexit fixture: a dry run registers its handlers, and
    none reaches the real atexit (whose count would otherwise grow)."""
    import atexit
    before = atexit._ncallbacks()
    _dry_run(monkeypatch)
    assert atexit._ncallbacks() == before
    assert any(getattr(fn, "__name__", "") == "_finish_proof" for fn in _no_atexit_handlers_leak)
