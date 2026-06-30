"""VastTemplate: payload shaping, extra_filters parsing, strict-key rejection."""
import pytest
from pydantic import ValidationError

from models import VastTemplate


def test_to_api_dict_drops_load_only_ports():
    t = VastTemplate(name="t", ports=["1111:1111"], env="-p 1111:1111")
    d = t.to_api_dict()
    assert "ports" not in d          # load-only, already folded into env
    assert d["env"] == "-p 1111:1111"
    assert d["name"] == "t"


def test_to_api_dict_omits_vm_and_autoscaler_when_unset():
    d = VastTemplate(name="t").to_api_dict()
    assert "vm" not in d
    assert "autoscaler" not in d


def test_to_api_dict_includes_vm_when_explicitly_set():
    d = VastTemplate(name="t", vm=True).to_api_dict()
    assert d["vm"] is True


def test_extra_filters_json_string_is_parsed_to_dict():
    t = VastTemplate(name="t", extra_filters='{"gpu_ram": ">=24"}')
    assert t.extra_filters == {"gpu_ram": ">=24"}


def test_extra_filters_dict_passes_through():
    t = VastTemplate(name="t", extra_filters={"gpu_ram": ">=24"})
    assert t.extra_filters == {"gpu_ram": ">=24"}


def test_extra_filters_malformed_json_left_as_string():
    t = VastTemplate(name="t", extra_filters="{not json")
    assert t.extra_filters == "{not json"


def test_unknown_key_is_rejected():
    # extra="forbid": a typo'd or attacker-supplied field must not reach the API.
    with pytest.raises(ValidationError):
        VastTemplate(name="t", definitely_not_a_field=1)
