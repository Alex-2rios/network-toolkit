import ipaddress
import json

from nettool import discovery
from nettool.cli import main
from nettool.discovery import Host


def read_json(capsys):
    return json.loads(capsys.readouterr().out)


def test_subnet_as_json(capsys):
    assert main(["--json", "subnet", "192.168.10.0/24"]) == 0

    payload = read_json(capsys)
    assert payload["netmask"] == "255.255.255.0"
    assert payload["usable hosts"] == "254"


def test_split_as_json(capsys):
    assert main(["--json", "split", "10.0.0.0/22", "-n", "4"]) == 0

    payload = read_json(capsys)
    assert payload["count"] == 4
    assert payload["subnets"][0]["subnet"] == "10.0.0.0/24"
    assert payload["subnets"][3]["usable_hosts"] == 254


def test_vlsm_as_json(capsys):
    assert main(["--json", "vlsm", "192.168.1.0/24", "sales:50", "wan:2"]) == 0

    payload = read_json(capsys)
    assert payload["addresses_used"] == 68
    assert payload["addresses_free"] == 188
    assert payload["allocations"][0]["name"] == "sales"
    assert payload["allocations"][0]["spare"] == 12


def test_sweep_as_json(capsys, monkeypatch):
    def fake_sweep(network, **kwargs):
        assert isinstance(network, ipaddress.IPv4Network)
        return [Host(address="192.168.1.5", alive=True, latency_ms=1.2, open_ports=[22])]

    monkeypatch.setattr(discovery, "sweep", fake_sweep)

    assert main(["--json", "sweep", "192.168.1.0/29"]) == 0

    payload = read_json(capsys)
    assert payload["hosts_up"] == 1
    assert payload["hosts"][0]["address"] == "192.168.1.5"
    assert payload["hosts"][0]["open_ports"][0]["service"] == "ssh"


def test_sweep_as_a_table(capsys, monkeypatch):
    monkeypatch.setattr(
        discovery,
        "sweep",
        lambda network, **kwargs: [
            Host(address="192.168.1.5", alive=True, latency_ms=1.2, hostname="nas", open_ports=[22])
        ],
    )

    assert main(["sweep", "192.168.1.0/29"]) == 0

    out = capsys.readouterr().out
    assert "192.168.1.5" in out
    assert "nas" in out
    assert "22/ssh" in out


def test_ports_as_json(capsys, monkeypatch):
    monkeypatch.setattr(discovery, "scan_ports", lambda host, ports, timeout=0.6: [22, 443])

    assert main(["--json", "ports", "10.0.0.1", "--ports", "22,80,443"]) == 0

    payload = read_json(capsys)
    assert payload["open"] == 2
    assert payload["scanned"] == 3
    assert [p["service"] for p in payload["ports"]] == ["ssh", "https"]


def test_ports_reports_when_nothing_is_open(capsys, monkeypatch):
    monkeypatch.setattr(discovery, "scan_ports", lambda host, ports, timeout=0.6: [])

    assert main(["ports", "10.0.0.1", "--ports", "22"]) == 0
    assert "no open ports found" in capsys.readouterr().out


def test_arp_as_json(capsys, monkeypatch):
    monkeypatch.setattr(
        discovery,
        "arp_table",
        lambda include_broadcast=False: [
            {"address": "192.168.0.1", "mac": "aa:bb:cc:dd:ee:ff", "state": "dynamic"}
        ],
    )

    assert main(["--json", "arp"]) == 0

    payload = read_json(capsys)
    assert payload["count"] == 1
    assert payload["entries"][0]["mac"] == "aa:bb:cc:dd:ee:ff"


def test_arp_says_so_when_the_table_is_empty(capsys, monkeypatch):
    monkeypatch.setattr(discovery, "arp_table", lambda include_broadcast=False: [])

    assert main(["arp"]) == 0
    assert "neighbour table is empty" in capsys.readouterr().out


def test_check_as_json_reports_the_failures(capsys, monkeypatch):
    from nettool import connectivity

    monkeypatch.setattr(discovery, "local_addresses", lambda: ["192.168.1.42"])
    monkeypatch.setattr(
        connectivity,
        "run_suite",
        lambda **kwargs: [
            connectivity.CheckResult("dns", True, "resolved", 12.0),
            connectivity.CheckResult("http", False, "timed out", 3000.0),
        ],
    )

    assert main(["--json", "check"]) == 1

    payload = read_json(capsys)
    assert payload["failed"] == 1
    assert payload["local_addresses"] == ["192.168.1.42"]
    assert payload["checks"][1]["ok"] is False


def test_check_as_a_table_passes_when_everything_passes(capsys, monkeypatch):
    from nettool import connectivity

    monkeypatch.setattr(discovery, "local_addresses", lambda: ["192.168.1.42"])
    monkeypatch.setattr(
        connectivity,
        "run_suite",
        lambda **kwargs: [connectivity.CheckResult("dns", True, "resolved", 12.0)],
    )

    assert main(["check"]) == 0
    assert "all 1 checks passed" in capsys.readouterr().out
