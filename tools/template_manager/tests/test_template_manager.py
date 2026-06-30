"""Env-string building and YAML loading (the ports/env normalization)."""
import pytest

from template_manager import TemplateManager

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
