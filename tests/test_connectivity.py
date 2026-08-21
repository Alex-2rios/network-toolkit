import socket
import ssl
import urllib.error

import pytest

from nettool import connectivity, discovery


class Listener:
    def __init__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(1)
        self.port = self.socket.getsockname()[1]

    def close(self):
        self.socket.close()


@pytest.fixture
def listener():
    server = Listener()
    yield server
    server.close()


def test_a_port_that_is_listening_is_reported_open(listener):
    assert discovery.probe_port("127.0.0.1", listener.port) is True


def test_a_port_nobody_listens_on_is_reported_closed(listener):
    closed = listener.port
    listener.close()

    assert discovery.probe_port("127.0.0.1", closed, timeout=0.3) is False


def test_scan_ports_returns_only_the_open_ones(listener):
    ports = [listener.port, listener.port + 1]

    assert discovery.scan_ports("127.0.0.1", ports, timeout=0.3) == [listener.port]


def test_timed_records_how_long_a_check_took():
    result = connectivity.timed("demo", lambda: (True, "fine"))

    assert result.name == "demo"
    assert result.ok is True
    assert result.detail == "fine"
    assert result.elapsed_ms >= 0


def test_timed_turns_an_exception_into_a_failed_check():
    def explode():
        raise RuntimeError("the socket gave up")

    result = connectivity.timed("demo", explode)

    assert result.ok is False
    assert "RuntimeError" in result.detail
    assert "the socket gave up" in result.detail


def test_the_gateway_check_reports_the_latency(monkeypatch):
    monkeypatch.setattr(connectivity, "ping", lambda address, timeout=1.0: (True, 1.5))

    result = connectivity.check_gateway("192.168.1.1")

    assert result.ok is True
    assert "1.5 ms" in result.detail


def test_the_gateway_check_fails_when_nothing_answers(monkeypatch):
    monkeypatch.setattr(connectivity, "ping", lambda address, timeout=1.0: (False, None))

    result = connectivity.check_gateway("192.168.1.1")

    assert result.ok is False
    assert "did not answer" in result.detail


def test_querying_a_dns_server_directly(monkeypatch):
    monkeypatch.setattr(connectivity, "query_dns", lambda server, name: ["1.0.0.1"])

    result = connectivity.check_dns("1.1.1.1", "one.one.one.one")

    assert result.ok is True
    assert "1.0.0.1" in result.detail


def test_a_dns_server_that_answers_with_nothing_is_a_failure(monkeypatch):
    monkeypatch.setattr(connectivity, "query_dns", lambda server, name: [])

    result = connectivity.check_dns("1.1.1.1", "one.one.one.one")

    assert result.ok is False
    assert "no A record" in result.detail


def test_falling_back_to_the_system_resolver(monkeypatch):
    monkeypatch.setattr(
        connectivity.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )

    result = connectivity.check_dns(None, "example.com")

    assert result.ok is True
    assert "93.184.216.34" in result.detail


def test_the_tcp_check_connects_to_something_listening(listener):
    result = connectivity.check_tcp("127.0.0.1", listener.port)

    assert result.ok is True
    assert "accepted the connection" in result.detail


def test_the_tcp_check_fails_on_a_closed_port(listener):
    closed = listener.port
    listener.close()

    result = connectivity.check_tcp("127.0.0.1", closed, timeout=0.3)

    assert result.ok is False
    assert "refused or filtered" in result.detail


class FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_the_http_check_passes_on_a_200(monkeypatch):
    monkeypatch.setattr(connectivity.urllib.request, "urlopen", lambda *a, **k: FakeResponse(200))

    result = connectivity.check_http("https://example.com")

    assert result.ok is True
    assert "returned 200" in result.detail


def test_a_client_error_is_reported_but_not_treated_as_the_server_being_down(monkeypatch):
    def raise_404(*args, **kwargs):
        raise urllib.error.HTTPError("https://example.com", 404, "Not Found", {}, None)

    monkeypatch.setattr(connectivity.urllib.request, "urlopen", raise_404)

    result = connectivity.check_http("https://example.com")

    assert result.ok is True
    assert "404" in result.detail


def test_a_server_error_is_a_failure(monkeypatch):
    def raise_500(*args, **kwargs):
        raise urllib.error.HTTPError("https://example.com", 503, "Unavailable", {}, None)

    monkeypatch.setattr(connectivity.urllib.request, "urlopen", raise_500)

    result = connectivity.check_http("https://example.com")

    assert result.ok is False


def test_an_untrusted_certificate_is_reported_as_such(monkeypatch):
    def raise_tls(*args, **kwargs):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("self signed"))

    monkeypatch.setattr(connectivity.urllib.request, "urlopen", raise_tls)

    result = connectivity.check_http("https://example.com")

    assert result.ok is False
    assert "does not trust" in result.detail


def test_an_unreachable_host_is_reported_differently_from_a_bad_certificate(monkeypatch):
    def raise_unreachable(*args, **kwargs):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(connectivity.urllib.request, "urlopen", raise_unreachable)

    result = connectivity.check_http("https://example.com")

    assert result.ok is False
    assert "unreachable" in result.detail


def test_the_suite_runs_the_layers_in_order(monkeypatch):
    monkeypatch.setattr(connectivity, "ping", lambda address, timeout=1.0: (True, 1.0))
    monkeypatch.setattr(connectivity, "query_dns", lambda server, name: ["1.0.0.1"])
    monkeypatch.setattr(connectivity.urllib.request, "urlopen", lambda *a, **k: FakeResponse(200))
    monkeypatch.setattr(connectivity.socket, "socket", _socket_that_connects())

    results = connectivity.run_suite("192.168.1.1", "1.1.1.1", "one.one.one.one", "https://x")

    assert [r.name for r in results] == ["gateway", "dns", "tcp 443", "http"]
    assert all(r.ok for r in results)


def test_the_suite_skips_the_gateway_when_none_is_given(monkeypatch):
    monkeypatch.setattr(connectivity, "query_dns", lambda server, name: ["1.0.0.1"])
    monkeypatch.setattr(connectivity.urllib.request, "urlopen", lambda *a, **k: FakeResponse(200))
    monkeypatch.setattr(connectivity.socket, "socket", _socket_that_connects())

    results = connectivity.run_suite(None, "1.1.1.1", "one.one.one.one", "https://x")

    assert [r.name for r in results] == ["dns", "tcp 443", "http"]


def _socket_that_connects():
    class FakeSocket:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def settimeout(self, value):
            pass

        def connect_ex(self, address):
            return 0

    return FakeSocket
