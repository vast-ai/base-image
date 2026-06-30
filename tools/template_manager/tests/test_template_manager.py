"""Env-string building and YAML loading (the ports/env normalization)."""
import pytest

import template_manager
from template_manager import TemplateManager, _redact_secrets

build = TemplateManager.build_env_string_from_lists


def test_build_env_ports_and_dict():
    assert build(ports=["1111:1111"], env_vars={"A": "b"}) == '-p 1111:1111 -e A="b"'


def test_build_env_list_form():
    assert build(env_vars=["A=b", "C=d"]) == '-e A="b" -e C="d"'


def test_build_env_ports_only():
    assert build(ports=["8080:8080", "5000:5000"]) == "-p 8080:8080 -p 5000:5000"


def test_build_env_empty():
    assert build() == ""


def test_build_env_escapes_quotes_in_value():
    assert build(env_vars={"A": 'x"y'}) == '-e A="x\\"y"'


def _write(tmp_path, text):
    p = tmp_path / "template.yml"
    p.write_text(text)
    return p


def test_load_folds_ports_and_dict_env(tmp_path):
    p = _write(tmp_path, 'name: t\nports:\n  - "1111:1111"\nenv:\n  A: b\n')
    (t,) = TemplateManager.load_templates_from_yaml(p)
    assert t.env == '-p 1111:1111 -e A="b"'


def test_load_mixed_ports_list_and_env_string(tmp_path):
    p = _write(tmp_path, 'name: t\nports:\n  - "1111:1111"\nenv: "-e A=b"\n')
    (t,) = TemplateManager.load_templates_from_yaml(p)
    assert t.env == "-p 1111:1111 -e A=b"


def test_load_env_string_passthrough(tmp_path):
    p = _write(tmp_path, 'name: t\nenv: "-e A=b -p 9:9"\n')
    (t,) = TemplateManager.load_templates_from_yaml(p)
    assert t.env == "-e A=b -p 9:9"


@pytest.mark.parametrize("body", ["env: []\n", "env: {}\n"])
def test_load_empty_list_or_dict_env_folds_to_empty_string(tmp_path, body):
    # Regression: an empty `env: []`/`env: {}` is falsy, so the fold guard used to
    # skip folding and assign the raw list/dict straight to entry['env'], which
    # VastTemplate (env: Optional[str]) then rejected with a confusing
    # ValidationError. Empty containers must fold to '' like any other shape.
    p = _write(tmp_path, "name: t\n" + body)
    (t,) = TemplateManager.load_templates_from_yaml(p)
    assert t.env == ""


def test_load_list_of_templates(tmp_path):
    p = _write(tmp_path, "- name: a\n- name: b\n")
    templates = TemplateManager.load_templates_from_yaml(p)
    assert [t.name for t in templates] == ["a", "b"]


def test_load_empty_yaml_raises(tmp_path):
    p = _write(tmp_path, "")
    with pytest.raises(ValueError):
        TemplateManager.load_templates_from_yaml(p)


def test_load_non_mapping_entry_raises(tmp_path):
    p = _write(tmp_path, "- just a string\n")
    with pytest.raises(ValueError):
        TemplateManager.load_templates_from_yaml(p)


# --- _redact_secrets: must mask at any depth (matches the docstring claim) -----

def test_redact_secrets_nested():
    payload = {"top_token": "s", "plain": "keep",
               "nested": {"api_key": "s", "ok": 1, "deep": [{"x_pass": "s"}]}}
    out = _redact_secrets(payload)
    assert out["top_token"] == "***redacted***"
    assert out["plain"] == "keep"
    assert out["nested"]["api_key"] == "***redacted***"
    assert out["nested"]["ok"] == 1
    assert out["nested"]["deep"][0]["x_pass"] == "***redacted***"
    # input is not mutated (returns a copy)
    assert payload["nested"]["api_key"] == "s"


# --- _request_with_retry: the first attempt fires immediately, no backoff ------

def test_request_with_retry_no_sleep_on_first_attempt(monkeypatch):
    mgr = TemplateManager("k")
    slept = []
    monkeypatch.setattr(template_manager.time, "sleep", slept.append)
    monkeypatch.setattr(mgr, "_open", lambda method, url, payload: {"ok": True})
    out = mgr._request_with_retry("POST", "http://x", payload={}, label="t")
    assert out == {"ok": True}
    assert slept == []   # no unconditional per-request latency
