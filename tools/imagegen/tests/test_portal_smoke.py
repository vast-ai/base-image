"""Bind-address smoke-gate verdict tests (ADR 0002 binding condition 1).

The verdict logic is the testable brain of the gate; the container orchestration
(smoke/bind-check.sh) is the thin integration layer around it.

Run: cd tools/imagegen && PYTHONPATH=. python -m pytest -q test_portal_smoke.py
"""
from imagegen.portal import parse_portal_config
from imagegen.portal_smoke import (
    ERROR,
    WARN,
    Listener,
    check_binds,
    exposed_ports,
    parse_ss,
)

# A realistic `ss -ltnp` dump: Caddy public on the front ports, apps on loopback.
SS_GOOD = """\
State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
LISTEN 0      4096   0.0.0.0:7860        0.0.0.0:*         users:(("caddy",pid=10,fd=3))
LISTEN 0      4096   127.0.0.1:17860     0.0.0.0:*         users:(("python",pid=20,fd=5))
LISTEN 0      128    [::]:8080           [::]:*            users:(("caddy",pid=10,fd=7))
LISTEN 0      128    127.0.0.1:18080     0.0.0.0:*         users:(("jupyter",pid=30,fd=8))
LISTEN 0      128    *:22                *:*               users:(("sshd",pid=5,fd=3))
"""

CFG = "localhost:7860:17860:/:Model UI|localhost:8080:18080:/:Jupyter|localhost:8080:8080:/t:Term"


# ---- ss parsing -------------------------------------------------------------

def test_parse_ss_addr_port_process_and_public_flag():
    ls = parse_ss(SS_GOOD)
    by_port = {l.port: l for l in ls}
    assert by_port[7860].process == "caddy" and by_port[7860].public
    assert by_port[17860].addr == "127.0.0.1" and not by_port[17860].public
    assert by_port[8080].public  # [::] is public
    assert not by_port[18080].public  # 127.0.0.1
    assert by_port[22].public  # *


def test_parse_ss_skips_header_and_blank():
    assert parse_ss("State Recv-Q ...\n\n") == []


# ---- exposed-port extraction ------------------------------------------------

def test_exposed_ports_from_dockerfile():
    df = "FROM x\nEXPOSE 7860 8000\nEXPOSE 8265/tcp\nRUN env-hash > /.env_hash\n"
    assert exposed_ports(df) == {7860, 8000, 8265}


def test_exposed_ports_ignores_unresolved_vars():
    assert exposed_ports("EXPOSE ${PORTS}\nEXPOSE 9000\n") == {9000}


# ---- the verdict: the good case is clean ------------------------------------

def test_clean_topology_passes():
    entries = parse_portal_config(CFG)
    v = check_binds(parse_ss(SS_GOOD), {7860}, entries)  # 8080 is base-allowlisted, app EXPOSEs 7860
    assert v == []


# ---- FAIL (1): an app binds 0.0.0.0 on its loopback/internal port -----------

def test_app_binding_public_on_internal_port_fails():
    ss = SS_GOOD.replace("127.0.0.1:17860", "0.0.0.0:17860")
    entries = parse_portal_config(CFG)
    v = check_binds(parse_ss(ss), {7860}, entries)
    assert any(f.severity == ERROR and f.port == 17860 for f in v)


# ---- FAIL (2): a non-Caddy process public on an EXPOSE'd port ---------------

def test_non_caddy_public_on_exposed_port_fails():
    ss = """\
LISTEN 0 4096 0.0.0.0:7860 0.0.0.0:* users:(("python",pid=20,fd=5))
"""
    entries = parse_portal_config("localhost:7860:17860:/:Model UI")
    v = check_binds(parse_ss(ss), {7860}, entries)
    assert any(f.severity == ERROR and f.port == 7860 and "not an auth proxy" in f.detail for f in v)


def test_caddy_public_on_exposed_proxied_port_is_ok():
    ss = """\
LISTEN 0 4096 0.0.0.0:7860 0.0.0.0:* users:(("caddy",pid=10,fd=3))
LISTEN 0 4096 127.0.0.1:17860 0.0.0.0:* users:(("python",pid=20,fd=5))
"""
    entries = parse_portal_config("localhost:7860:17860:/:Model UI")
    assert check_binds(parse_ss(ss), {7860}, entries) == []


# ---- FAIL (3): an equal-port-only port is EXPOSE'd and has a public bind -----

def test_equal_port_only_exposed_with_public_listener_fails():
    # Wan2GP-style: 7861:7861 (Caddy skips), app binds 0.0.0.0:7861, and it's EXPOSE'd.
    ss = """\
LISTEN 0 4096 0.0.0.0:7861 0.0.0.0:* users:(("python",pid=40,fd=9))
"""
    entries = parse_portal_config("localhost:7861:7861:/:Wan2GP")
    v = check_binds(parse_ss(ss), {7861}, entries)
    # both the forbidden-port rule and the equal-port-only rule should bite
    assert any(f.severity == ERROR and f.port == 7861 for f in v)


# ---- multi-URL convention: proxied + equal sibling, app on loopback = clean --

def test_multi_url_jupyter_pattern_clean():
    ss = """\
LISTEN 0 4096 0.0.0.0:8080 0.0.0.0:* users:(("caddy",pid=10,fd=3))
LISTEN 0 4096 127.0.0.1:18080 0.0.0.0:* users:(("jupyter",pid=30,fd=8))
"""
    entries = parse_portal_config("localhost:8080:18080:/:Jupyter|localhost:8080:8080:/t:Term")
    # 8080 is allowlisted out of `exposed` for per-image images; pass it anyway to
    # prove caddy-on-8080 is accepted and the loopback jupyter is fine.
    assert check_binds(parse_ss(ss), {8080}, entries) == []


# ---- WARN: proxied EXPOSE'd port with no Caddy listener (functional) --------

def test_proxied_exposed_port_without_caddy_warns_not_errors():
    ss = """\
LISTEN 0 4096 127.0.0.1:17860 0.0.0.0:* users:(("python",pid=20,fd=5))
"""
    entries = parse_portal_config("localhost:7860:17860:/:Model UI")
    v = check_binds(parse_ss(ss), {7860}, entries)
    assert [f.severity for f in v] == [WARN]
    assert v[0].port == 7860
