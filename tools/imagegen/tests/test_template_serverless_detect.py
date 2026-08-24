"""Tests for test_template.py's serverless detection (ADR 0031 decision 3).

The QA gate runs the standard and the serverless cell from ONE template, so the
serverless flag reaches the client as an `--env` override and nowhere else. Before
ADR 0031 the client looked only at the template's own env/onstart, so that cell
launched with `is_serverless` false — and the one thing that gates on it,
`OPEN_BUTTON_TOKEN=1`, happened to be in the vLLM QA template already. It worked
by coincidence. A coincidence is not a passing test, so this pins the rule.

Loaded by path: `test_template.py` is a script under tools/template_manager, not
an installed module, and this needs no network, no API key and no instance.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

CLIENT = Path(__file__).resolve().parents[3] / "tools/template_manager/test_template.py"


def _load():
    spec = importlib.util.spec_from_file_location("tt_client", CLIENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tt = _load()


def test_override_alone_is_enough():
    """THE case that was missing, and the only one the serverless QA cell has."""
    assert tt.detect_serverless({"env": "-e FOO=1"}, ["SERVERLESS=true"])


def test_template_env_still_counts():
    assert tt.detect_serverless({"env": "-e SERVERLESS=true -e FOO=1"}, [])


def test_onstart_still_counts():
    assert tt.detect_serverless({"onstart": "export SERVERLESS=true\nentrypoint.sh"}, [])


def test_plain_template_is_not_serverless():
    assert not tt.detect_serverless({"env": "-e OPEN_BUTTON_TOKEN=1"}, ["FOO=bar"])


def test_missing_fields_do_not_raise():
    """The platform returns these as absent or null, not as empty strings."""
    assert not tt.detect_serverless({})
    assert not tt.detect_serverless({"env": None, "onstart": None})


def test_serverless_false_is_not_serverless():
    """Substring matching must not turn the OFF switch into the ON switch."""
    assert not tt.detect_serverless({"env": "-e SERVERLESS=false"}, [])
