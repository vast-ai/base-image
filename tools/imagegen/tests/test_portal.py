"""PORTAL_CONFIG parser + EXPOSE port-set logic tests (ADR 0002).

Run: cd tools/imagegen && PYTHONPATH=. python -m pytest -q test_portal.py
"""
import re
from pathlib import Path

import pytest

from imagegen.discover import find_repo_root
from imagegen.portal import (
    BASE_ALLOWLIST,
    FIELD_ORDER,
    PortalEntry,
    equal_port_externals,
    extract_baked_default,
    forbidden_expose_ports,
    internal_ports,
    orphan_equal_ports,
    parse_portal_config,
    proxied_externals,
    required_expose_ports,
)

# The real external/vllm baked default — a representative multi-service string
# with a proxied set, the Instance Portal, and the Jupyter proxied+terminal pair.
VLLM = (
    "localhost:1111:11111:/:Instance Portal"
    "|localhost:7860:17860:/:Model UI"
    "|localhost:8000:18000:/docs:vLLM API"
    "|localhost:8265:28265:/:Ray Dashboard"
    "|localhost:8080:18080:/:Jupyter"
    "|localhost:8080:8080:/terminals/1:Jupyter Terminal"
)


# ---- parsing ----------------------------------------------------------------

def test_parse_field_order_external_then_internal():
    e = parse_portal_config("localhost:7860:17860:/:Model UI")[0]
    assert (e.hostname, e.external, e.internal, e.path, e.label) == (
        "localhost", 7860, 17860, "/", "Model UI")
    assert e.proxied  # 7860 != 17860


def test_parse_path_may_contain_colon_is_not_split():
    # split(':', 4) caps fields at 5 — a colon in the label/path stays intact.
    e = parse_portal_config("localhost:9000:19000:/a:Tool: Beta")[0]
    assert e.path == "/a"
    assert e.label == "Tool: Beta"


def test_parse_skips_blank_and_trailing_pipe():
    assert len(parse_portal_config("localhost:1:2:/:A|")) == 1
    assert parse_portal_config("") == []


def test_parse_rejects_short_entry():
    with pytest.raises(ValueError):
        parse_portal_config("localhost:7860:/:NoInternal")


def test_parse_rejects_non_integer_ports():
    with pytest.raises(ValueError):
        parse_portal_config("localhost:abc:17860:/:Bad")


def test_equal_port_entry_is_not_proxied():
    e = parse_portal_config("localhost:8080:8080:/terminals/1:Jupyter Terminal")[0]
    assert not e.proxied


# ---- set logic over the real vllm config ------------------------------------

def test_vllm_proxied_and_required():
    es = parse_portal_config(VLLM)
    assert proxied_externals(es) == {1111, 7860, 8000, 8265, 8080}
    # required strips the base-owned 1111/8080; the image EXPOSEs only its own apps.
    assert required_expose_ports(es) == {7860, 8000, 8265}


def test_vllm_forbidden_never_includes_a_proxied_front():
    es = parse_portal_config(VLLM)
    forbidden = forbidden_expose_ports(es)
    # internals must be forbidden ...
    assert {11111, 17860, 18000, 28265, 18080} <= forbidden
    # ... but 8080 is proxied (8080:18080) so it is NOT forbidden, even though the
    # terminal entry 8080:8080 makes it an equal-port external too. (The multi-URL
    # convention: proxied by one entry ⇒ EXPOSE-safe.)
    assert 8080 not in forbidden
    assert required_expose_ports(es).isdisjoint(forbidden)


# ---- the multi-URL / equal-port convention (the heart of ADR 0002) ----------

def test_proxied_plus_equal_port_sibling_is_expose_safe():
    # One proxied entry forces Caddy; the equal-port sibling is a tab only.
    es = parse_portal_config("localhost:8080:18080:/:Jupyter|localhost:8080:8080:/terminals/1:Terminal")
    assert proxied_externals(es) == {8080}
    assert 8080 not in forbidden_expose_ports(es)   # proxied wins
    assert orphan_equal_ports(es) == set()          # 8080 has a proxied sibling


def test_orphan_equal_port_is_forbidden_and_flagged():
    # aio-studio's Wan2GP `7861:7861` with no proxied sibling: raw/unauth smell.
    es = parse_portal_config("localhost:7861:7861:/:Wan2GP")
    assert proxied_externals(es) == set()
    assert 7861 in forbidden_expose_ports(es)
    assert orphan_equal_ports(es) == {7861}


def test_swapped_internal_low_port_is_forbidden():
    # aio-studio's `13000:3000`: EXPOSE-ing the internal 3000 would be a raw backend.
    es = parse_portal_config("localhost:13000:3000:/:App")
    assert required_expose_ports(es) == {13000}
    assert 3000 in forbidden_expose_ports(es)


def test_required_and_forbidden_are_always_disjoint():
    es = parse_portal_config(VLLM + "|localhost:7861:7861:/:Wan2GP|localhost:13000:3000:/:App")
    assert required_expose_ports(es).isdisjoint(forbidden_expose_ports(es))


def test_internal_and_equal_helpers():
    es = parse_portal_config("localhost:9000:19000:/:A|localhost:5000:5000:/:B")
    assert internal_ports(es) == {19000, 5000}
    assert equal_port_externals(es) == {5000}


# ---- baked-default extraction from a boot env script ------------------------

def test_extract_baked_default_inside_guard():
    sh = (
        "#!/bin/bash\n"
        "if [[ -z $PORTAL_CONFIG ]]; then\n"
        '    export PORTAL_CONFIG="localhost:8000:18000:/:API"\n'
        "fi\n"
        'export OTHER="x"\n'
    )
    assert extract_baked_default(sh) == "localhost:8000:18000:/:API"


def test_extract_ignores_later_runtime_mutation():
    # linux-desktop shape: bake a default, then mutate PORTAL_CONFIG below the guard.
    # We must return the baked literal, NOT the mutated form (caveat: the mutation
    # itself is out of scope — ADR 0002 condition 2 / smoke test).
    sh = (
        "if [[ -z $PORTAL_CONFIG ]]; then\n"
        '    export PORTAL_CONFIG="localhost:6100:16100:/:Desktop|localhost:1:2:/:X"\n'
        "fi\n"
        'PORTAL_CONFIG=$(echo "$PORTAL_CONFIG" | grep -v \':16100:\')\n'
    )
    assert extract_baked_default(sh) == "localhost:6100:16100:/:Desktop|localhost:1:2:/:X"


def test_extract_returns_none_when_no_default():
    assert extract_baked_default("#!/bin/bash\nexport MODEL_NAME=foo\n") is None


# ---- pins against ground truth (must not silently drift) --------------------

def test_field_order_pinned_to_runtime_parser():
    """FIELD_ORDER must match caddy_config_manager.py's `split(':', 4)` unpacking —
    external is field 2, internal is field 3. If the runtime parser changes, this
    fails so portal.py is updated in lockstep (ADR 0002)."""
    repo = find_repo_root(Path(__file__).resolve().parent)
    src = (repo / "portal-aio" / "caddy_manager" / "caddy_config_manager.py").read_text()
    m = re.search(r"(\w+),\s*(\w+),\s*(\w+),\s*(\w+),\s*(\w+)\s*=\s*\w+\.split\(\s*['\"]:['\"]\s*,\s*4\s*\)", src)
    assert m, "could not find the PORTAL_CONFIG split in caddy_config_manager.py"
    names = [g.lower() for g in m.groups()]
    assert names[1].startswith("ext") and names[2].startswith("int"), \
        f"runtime parser order changed: {names} — update FIELD_ORDER + this test"
    assert FIELD_ORDER == ("hostname", "external", "internal", "path", "label")


def test_contributing_documents_correct_field_order():
    """ADR 0002 P0: CONTRIBUTING.md must document external-before-internal and must
    not resurrect the false `internal + 10000` convention."""
    repo = find_repo_root(Path(__file__).resolve().parent)
    txt = (repo / "CONTRIBUTING.md").read_text()
    assert "localhost:external:internal:path:Label" in txt
    assert "localhost:internal:external:path:Label" not in txt
    # the false-claim phrasing must be gone (the doc may still *mention* the rule
    # only to say it does not exist).
    assert "convention: internal + 10000" not in txt
    # the External-port row must appear before the Internal-port row in the table
    assert txt.index("| External port |") < txt.index("| Internal port |")
