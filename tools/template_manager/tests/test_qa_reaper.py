"""Reaper scope/threshold logic — the cost-safety backstop (ADR 0005 cond 1).

The reaper is the one thing between a hard-killed runner and an indefinitely
billing GPU, so its selection predicate carries the same test burden as every
other tested module here: prove it (a) reaps a labeled, over-age orphan and
(b) refuses anything outside scope or under age. The threshold tests pin it above
test_template.py's healthy-run budget so a sweep can never clip a live instance.
"""
import urllib.error

import reap_orphans
import test_template


def inst(id=1, label="base-image-qa-comfyui", image="stagingns/comfyui:qa",
         uptime=300.0, status="running"):
    """A Vast instance record with the fields the reaper reads."""
    return {"id": id, "label": label, "image_uuid": image,
            "uptime_mins": uptime, "actual_status": status, "gpu_name": "RTX 4090"}


# --- scope predicate -------------------------------------------------------

def test_in_scope_label_prefix_matches():
    assert reap_orphans.in_scope(inst(label="base-image-qa-x"), "base-image-qa", None)


def test_in_scope_label_mismatch_excluded():
    # A non-QA instance carries no QA label — it must never be in scope.
    assert not reap_orphans.in_scope(inst(label="prod-inference"), "base-image-qa", None)


def test_in_scope_missing_label_excluded():
    assert not reap_orphans.in_scope(inst(label=None), "base-image-qa", None)


def test_in_scope_image_prefix_is_anded():
    # label matches but image prefix does not -> excluded (AND, not OR).
    i = inst(label="base-image-qa-x", image="other/comfyui:qa")
    assert not reap_orphans.in_scope(i, "base-image-qa", "stagingns/")


def test_in_scope_falls_back_to_image_field():
    # Vast may return the image under 'image' rather than 'image_uuid'.
    i = {"label": "base-image-qa", "image": "stagingns/comfyui:qa"}
    assert reap_orphans.in_scope(i, "base-image-qa", "stagingns/")


def test_in_scope_no_filters_matches_everything():
    # Both filters None = the --allow-no-scope age-only mode.
    assert reap_orphans.in_scope(inst(label=None, image=""), None, None)


# --- reapability: age AND scope -------------------------------------------

def test_labeled_over_age_orphan_is_reaped():
    # (a) the thing the reaper exists to catch.
    i = inst(label="base-image-qa-comfyui", uptime=400.0)
    assert reap_orphans.is_reapable(i, 260.0, "base-image-qa", None)


def test_unlabeled_over_age_instance_is_refused():
    # (b) an old non-QA instance must survive even though it is over age.
    i = inst(label="prod-training", uptime=9999.0)
    assert not reap_orphans.is_reapable(i, 260.0, "base-image-qa", None)


def test_out_of_prefix_over_age_instance_is_refused():
    i = inst(label="base-image-qa-x", image="other/x:qa", uptime=9999.0)
    assert not reap_orphans.is_reapable(i, 260.0, "base-image-qa", "stagingns/")


def test_in_scope_under_age_instance_is_refused():
    # A live QA run that is younger than the threshold must not be reaped.
    i = inst(label="base-image-qa-comfyui", uptime=120.0)
    assert not reap_orphans.is_reapable(i, 260.0, "base-image-qa", None)


def test_age_threshold_is_strict():
    # Exactly at the threshold is kept; strictly over is reaped.
    at = inst(uptime=260.0)
    over = inst(uptime=260.1)
    assert not reap_orphans.is_reapable(at, 260.0, "base-image-qa", None)
    assert reap_orphans.is_reapable(over, 260.0, "base-image-qa", None)


def test_malformed_uptime_keeps_instance():
    # _age_min coerces None/missing/garbage to 0 -> never reapable. Fail safe:
    # a malformed record must not be destroyed on a guessed age.
    for bad in (None, "abc", {}):
        i = inst(uptime=bad)
        assert not reap_orphans.is_reapable(i, 260.0, "base-image-qa", None)


def test_select_candidates_filters_mixed_set():
    instances = [
        inst(id=1, label="base-image-qa-comfyui", uptime=400.0),   # reap
        inst(id=2, label="prod", uptime=400.0),                    # keep (scope)
        inst(id=3, label="base-image-qa-vllm", uptime=10.0),       # keep (age)
        inst(id=4, label="base-image-qa-vllm", uptime=999.0),      # reap
    ]
    got = {i["id"] for i in reap_orphans.select_candidates(
        instances, 260.0, "base-image-qa", None)}
    assert got == {1, 4}


# --- silent under-reaping canary ------------------------------------------

def test_old_out_of_scope_flags_unmatched_orphan():
    # An over-age instance excluded only by scope (e.g. label failed to round-trip)
    # is the signature of silent under-reaping — it must be surfaced.
    instances = [
        inst(id=1, label="base-image-qa", uptime=400.0),   # in scope, reaped normally
        inst(id=2, label=None, uptime=400.0),              # over-age but unlabeled
    ]
    flagged = reap_orphans.old_out_of_scope(instances, 260.0, "base-image-qa", None)
    assert [i["id"] for i in flagged] == [2]


def test_old_out_of_scope_empty_when_all_young_or_in_scope():
    instances = [
        inst(id=1, label="base-image-qa", uptime=400.0),   # in scope
        inst(id=2, label="prod", uptime=10.0),             # out of scope but young
    ]
    assert reap_orphans.old_out_of_scope(instances, 260.0, "base-image-qa", None) == []


# --- destroy classification (404 = already gone, not a leak) --------------

def _http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, None)


def test_destroy_success_is_reaped(monkeypatch):
    monkeypatch.setattr(reap_orphans, "_request", lambda *a, **k: {})
    assert reap_orphans.destroy_instance(1, "key") == ("reaped", None)


def test_destroy_404_is_gone_not_failure(monkeypatch):
    # Already destroyed (concurrent --destroy / prior pass / spot reclaim) is the
    # goal state — it must not be counted as a leak or it cries wolf.
    def boom(*a, **k):
        raise _http_error(404)
    monkeypatch.setattr(reap_orphans, "_request", boom)
    status, detail = reap_orphans.destroy_instance(1, "key")
    assert status == "gone"


def test_destroy_5xx_is_failure(monkeypatch):
    def boom(*a, **k):
        raise _http_error(500)
    monkeypatch.setattr(reap_orphans, "_request", boom)
    status, detail = reap_orphans.destroy_instance(1, "key")
    assert status == "failed"
    assert detail


def test_destroy_transport_error_is_failure(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("connection reset")
    monkeypatch.setattr(reap_orphans, "_request", boom)
    assert reap_orphans.destroy_instance(1, "key")[0] == "failed"


# --- threshold drift guard ------------------------------------------------

def test_healthy_phase_constant_mirrors_test_template_budget():
    # reap_orphans mirrors test_template's auto-lifted --timeout. If that budget
    # changes (someone bumps PROV_TIMEOUT etc.), this fails so the mirror is kept
    # in step rather than silently drifting below the real run lifetime.
    envs = test_template.INSTANCE_TIMEOUT_ENVS
    prov = envs["PROV_TIMEOUT"]
    derivative = max(envs["VLLM_HEALTH_TIMEOUT"], envs["INSTANCE_TEST_DEFAULT_TIMEOUT"])
    budget = prov + derivative + test_template.TIMEOUT_HEADROOM
    assert reap_orphans._HEALTHY_TEST_PHASE_SEC == budget


def test_default_threshold_stays_above_test_template_budget():
    # The reaper must never undercut a healthy run + a safety margin, or it deletes
    # a live instance mid-test (false BLOCK + wasted spend).
    envs = test_template.INSTANCE_TIMEOUT_ENVS
    prov = envs["PROV_TIMEOUT"]
    derivative = max(envs["VLLM_HEALTH_TIMEOUT"], envs["INSTANCE_TEST_DEFAULT_TIMEOUT"])
    budget_sec = prov + derivative + test_template.TIMEOUT_HEADROOM
    safety_margin_sec = 3600   # 60 min over the healthy ceiling, minimum
    assert reap_orphans.DEFAULT_MAX_AGE_MIN * 60 >= budget_sec + safety_margin_sec
