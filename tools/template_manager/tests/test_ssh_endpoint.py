"""ssh_endpoint(): SSH coords for a held box so the QA-fix loop (ADR 0009) can log in."""
import test_template as tt


class _Api:
    def __init__(self, record):
        self._record = record

    def get_instance(self, instance_id, safe=False):
        return self._record


def test_ssh_endpoint_prefers_ssh_fields():
    api = _Api({"ssh_host": "ssh5.vast.ai", "ssh_port": 12186, "public_ipaddr": "1.2.3.4"})
    assert tt.ssh_endpoint(api, 1) == {"host": "ssh5.vast.ai", "port": 12186}


def test_ssh_endpoint_falls_back_to_22_tcp_mapping():
    api = _Api({"public_ipaddr": "1.2.3.4",
                "ports": {"22/tcp": [{"HostPort": "40022"}],
                          "10199/tcp": [{"HostPort": "40000"}]}})
    assert tt.ssh_endpoint(api, 1) == {"host": "1.2.3.4", "port": 40022}


def test_ssh_endpoint_empty_when_unresolvable():
    assert tt.ssh_endpoint(_Api({}), 1) == {}
    assert tt.ssh_endpoint(_Api(None), 1) == {}
    # public IP but no SSH port anywhere -> empty, not a half-answer
    assert tt.ssh_endpoint(_Api({"public_ipaddr": "1.2.3.4"}), 1) == {}
