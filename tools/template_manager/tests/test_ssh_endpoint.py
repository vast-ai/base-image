"""ssh_endpoint(): SSH coords for a held box so the QA-fix loop (ADR 0009) can log in.
Prefers DIRECT ssh (public IP + 22/tcp mapping) — Vast injects the operator's key into the
instance, so direct works where the account-keyed proxy does not on an API-launched box."""
import test_template as tt


class _Api:
    def __init__(self, record):
        self._record = record

    def get_instance(self, instance_id, safe=False):
        return self._record


def test_ssh_endpoint_prefers_direct_over_proxy():
    # both present -> the DIRECT public-IP + 22/tcp mapping wins over the ssh_host/ssh_port proxy
    api = _Api({"ssh_host": "ssh3.vast.ai", "ssh_port": 13976,
                "public_ipaddr": "38.255.16.21",
                "ports": {"22/tcp": [{"HostPort": "58673"}], "10199/tcp": [{"HostPort": "40000"}]}})
    assert tt.ssh_endpoint(api, 1) == {"host": "38.255.16.21", "port": 58673}


def test_ssh_endpoint_falls_back_to_proxy():
    # no public IP / no 22 mapping -> fall back to the ssh_host/ssh_port proxy
    api = _Api({"ssh_host": "ssh3.vast.ai", "ssh_port": 13976})
    assert tt.ssh_endpoint(api, 1) == {"host": "ssh3.vast.ai", "port": 13976}


def test_ssh_endpoint_empty_when_unresolvable():
    assert tt.ssh_endpoint(_Api({}), 1) == {}
    assert tt.ssh_endpoint(_Api(None), 1) == {}
    # public IP but no 22 mapping and no proxy -> empty, not a half-answer
    assert tt.ssh_endpoint(_Api({"public_ipaddr": "1.2.3.4"}), 1) == {}
