"""Regression tests for caddy_manager.load_config — the empty/malformed
/etc/portal.yaml crash.

Bug: `caddy.sh` `touch`es /etc/portal.yaml when PORTAL_CONFIG is unset, leaving a
zero-byte file. On the next boot load_config saw the file exists and did
`yaml.safe_load('')['applications']` → `None['applications']` → TypeError → no
Caddyfile written → the whole Caddy/portal front failed to start. The invariant:
**a present-but-empty/malformed cache is treated as ABSENT** (fall through to
PORTAL_CONFIG), never a crash.

Run from the portal-aio directory (or repo root) with pytest.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from caddy_manager import caddy_config_manager as ccm


_CFG = "localhost:1111:11111:/:Instance Portal|localhost:7860:17860:/:App UI"


def test_empty_cache_file_falls_through_to_env(tmp_path, monkeypatch):
    """The reported bug: an empty portal.yaml must NOT crash; with PORTAL_CONFIG set
    it regenerates from env. (Pre-fix this raised TypeError.)"""
    p = tmp_path / "portal.yaml"
    p.write_text("")  # the zero-byte file `caddy.sh` leaves behind
    monkeypatch.setenv("PORTAL_CONFIG", _CFG)
    apps = ccm.load_config(str(p))
    assert set(apps) == {"Instance Portal", "App UI"}
    assert apps["App UI"]["external_port"] == 7860


def test_missing_applications_key_falls_through(tmp_path, monkeypatch):
    """A valid-YAML cache lacking 'applications' is treated as absent, not a KeyError."""
    p = tmp_path / "portal.yaml"
    p.write_text("something_else: true\n")
    monkeypatch.setenv("PORTAL_CONFIG", _CFG)
    assert set(ccm.load_config(str(p))) == {"Instance Portal", "App UI"}


def test_empty_cache_and_no_env_raises_valueerror_not_typeerror(tmp_path, monkeypatch):
    """No config anywhere is a graceful ValueError (the pre-existing 'no config' path),
    never a TypeError crash on the empty file."""
    p = tmp_path / "portal.yaml"
    p.write_text("")
    monkeypatch.delenv("PORTAL_CONFIG", raising=False)
    with pytest.raises(ValueError):
        ccm.load_config(str(p))


def test_valid_cache_wins_over_env(tmp_path, monkeypatch):
    """A populated cache is authoritative; the env is ignored (cache-preferred)."""
    p = tmp_path / "portal.yaml"
    p.write_text(
        "applications:\n"
        "  Cached App:\n"
        "    hostname: localhost\n"
        "    external_port: 9000\n"
        "    internal_port: 19000\n"
        "    open_path: /\n"
        "    name: Cached App\n"
    )
    monkeypatch.setenv("PORTAL_CONFIG", _CFG)  # must be ignored
    apps = ccm.load_config(str(p))
    assert set(apps) == {"Cached App"}


def test_regenerated_cache_is_well_formed(tmp_path, monkeypatch):
    """When regenerating from env, the written file is a valid {'applications': {...}}
    doc that a subsequent load reads back without falling through."""
    import yaml
    p = tmp_path / "portal.yaml"
    p.write_text("")
    monkeypatch.setenv("PORTAL_CONFIG", _CFG)
    ccm.load_config(str(p))  # regenerates + writes
    doc = yaml.safe_load(p.read_text())
    assert isinstance(doc, dict) and set(doc["applications"]) == {"Instance Portal", "App UI"}
