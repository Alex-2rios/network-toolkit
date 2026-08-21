import ipaddress
import socket
import subprocess

from nettool import connectivity, discovery

WINDOWS_PING = """
Haciendo ping a 192.168.0.1 con 32 bytes de datos:
Respuesta desde 192.168.0.1: bytes=32 tiempo=1,4ms TTL=64
"""

LINUX_PING = """
PING 192.168.0.1 (192.168.0.1) 56(84) bytes of data.
64 bytes from 192.168.0.1: icmp_seq=1 ttl=64 time=0.842 ms
"""

SUB_MILLISECOND_PING = """
64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time<1 ms
"""


class FakeCompleted:
    def __init__(self, returncode, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_the_ping_command_differs_per_platform():
    assert discovery.ping_command("10.0.0.1", 1.0, windows=True)[1:4] == ["-n", "1", "-w"]
    assert discovery.ping_command("10.0.0.1", 1.0, windows=False)[1:4] == ["-c", "1", "-W"]


def test_windows_reports_milliseconds_with_a_decimal_comma():
    assert discovery.parse_ping_latency(WINDOWS_PING) == 1.4


def test_linux_reports_milliseconds_with_a_decimal_point():
    assert discovery.parse_ping_latency(LINUX_PING) == 0.842


def test_a_sub_millisecond_reply_still_parses():
    assert discovery.parse_ping_latency(SUB_MILLISECOND_PING) == 1.0


def test_output_without_a_time_gives_no_latency():
    assert discovery.parse_ping_latency("Request timed out.") is None


def test_a_successful_ping(monkeypatch):
    monkeypatch.setattr(
        discovery.subprocess, "run", lambda *a, **k: FakeCompleted(0, LINUX_PING)
    )

    alive, latency = discovery.ping("192.168.0.1")

    assert alive is True
    assert latency == 0.842


def test_a_failed_ping(monkeypatch):
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **k: FakeCompleted(1, ""))

    assert discovery.ping("192.168.0.1") == (False, None)


def test_a_ping_that_times_out_is_not_an_exception(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ping", timeout=1)

    monkeypatch.setattr(discovery.subprocess, "run", timeout)

    assert discovery.ping("192.168.0.1") == (False, None)


def test_a_missing_ping_binary_is_not_an_exception(monkeypatch):
    def missing(*args, **kwargs):
        raise OSError("ping not found")

    monkeypatch.setattr(discovery.subprocess, "run", missing)

    assert discovery.ping("192.168.0.1") == (False, None)


def test_reverse_lookup_returns_none_when_there_is_no_record(monkeypatch):
    def fail(address):
        raise socket.herror(1, "Unknown host")

    monkeypatch.setattr(discovery.socket, "gethostbyaddr", fail)

    assert discovery.resolve("10.0.0.1") is None


def test_reverse_lookup_returns_the_name(monkeypatch):
    monkeypatch.setattr(discovery.socket, "gethostbyaddr", lambda a: ("nas.lan", [], [a]))

    assert discovery.resolve("10.0.0.1") == "nas.lan"


def test_a_sweep_only_returns_the_hosts_that_answered(monkeypatch):
    def fake_ping(address, timeout):
        return (address.endswith(".3"), 1.0 if address.endswith(".3") else None)

    monkeypatch.setattr(discovery, "ping", fake_ping)

    hosts = discovery.sweep(ipaddress.IPv4Network("192.168.0.0/29"), workers=4)

    assert [h.address for h in hosts] == ["192.168.0.3"]
    assert hosts[0].latency_ms == 1.0
    assert hosts[0].hostname is None


def test_a_sweep_can_resolve_names_and_probe_ports(monkeypatch):
    monkeypatch.setattr(discovery, "ping", lambda address, timeout: (True, 1.0))
    monkeypatch.setattr(discovery, "resolve", lambda address: "host.lan")
    monkeypatch.setattr(discovery, "scan_ports", lambda address, ports, timeout=0.4: [22])

    hosts = discovery.sweep(
        ipaddress.IPv4Network("192.168.0.0/30"),
        workers=2,
        resolve_names=True,
        check_ports=[22, 80],
    )

    assert hosts[0].hostname == "host.lan"
    assert hosts[0].open_ports == [22]


def test_a_point_to_point_range_scans_both_addresses(monkeypatch):
    seen = []

    def fake_ping(address, timeout):
        seen.append(address)
        return False, None

    monkeypatch.setattr(discovery, "ping", fake_ping)
    discovery.sweep(ipaddress.IPv4Network("10.0.0.0/31"), workers=2)

    assert sorted(seen) == ["10.0.0.0", "10.0.0.1"]


def build_dns_response(addresses, name="one.one.one.one"):
    header = b"\xab\xcd" + b"\x81\x80" + b"\x00\x01"
    header += len(addresses).to_bytes(2, "big") + b"\x00\x00" + b"\x00\x00"

    question = b"".join(bytes([len(part)]) + part.encode() for part in name.split(".")) + b"\x00"
    question += b"\x00\x01\x00\x01"

    answers = b""
    for address in addresses:
        answers += b"\xc0\x0c"
        answers += b"\x00\x01"
        answers += b"\x00\x01"
        answers += b"\x00\x00\x01\x2c"
        answers += b"\x00\x04"
        answers += bytes(int(octet) for octet in address.split("."))

    return header + question + answers


class FakeUdpSocket:
    def __init__(self, payload):
        self.payload = payload
        self.sent_to = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def settimeout(self, value):
        pass

    def sendto(self, data, address):
        self.sent_to = address

    def recvfrom(self, size):
        return self.payload, self.sent_to


def test_a_dns_answer_is_parsed_into_addresses(monkeypatch):
    fake = FakeUdpSocket(build_dns_response(["1.0.0.1", "1.1.1.1"]))
    monkeypatch.setattr(connectivity.socket, "socket", lambda *a, **k: fake)

    assert connectivity.query_dns("1.1.1.1", "one.one.one.one") == ["1.0.0.1", "1.1.1.1"]


def test_the_query_goes_to_port_53(monkeypatch):
    fake = FakeUdpSocket(build_dns_response(["1.0.0.1"]))
    monkeypatch.setattr(connectivity.socket, "socket", lambda *a, **k: fake)

    connectivity.query_dns("9.9.9.9", "example.com")

    assert fake.sent_to == ("9.9.9.9", 53)


def test_an_answer_with_no_records_gives_an_empty_list(monkeypatch):
    fake = FakeUdpSocket(build_dns_response([]))
    monkeypatch.setattr(connectivity.socket, "socket", lambda *a, **k: fake)

    assert connectivity.query_dns("1.1.1.1", "nothing.example") == []


def test_local_addresses_are_deduplicated_and_sorted(monkeypatch):
    monkeypatch.setattr(discovery.socket, "gethostname", lambda: "workstation")
    monkeypatch.setattr(
        discovery.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("192.168.1.42", 0)), (2, 1, 6, "", ("192.168.1.42", 0))],
    )

    class FakeUdp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def connect(self, address):
            pass

        def getsockname(self):
            return ("10.0.0.5", 0)

    monkeypatch.setattr(discovery.socket, "socket", lambda *a, **k: FakeUdp())

    assert discovery.local_addresses() == ["10.0.0.5", "192.168.1.42"]
