from __future__ import annotations

import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from nettool.discovery import ping


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    elapsed_ms: float


def timed(name: str, func) -> CheckResult:
    start = time.perf_counter()
    try:
        ok, detail = func()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    elapsed = (time.perf_counter() - start) * 1000
    return CheckResult(name=name, ok=ok, detail=detail, elapsed_ms=elapsed)


def check_gateway(gateway: str) -> CheckResult:
    def run():
        alive, latency = ping(gateway, timeout=1.0)
        if not alive:
            return False, f"{gateway} did not answer"
        return True, f"{gateway} replied" + (f" in {latency} ms" if latency else "")

    return timed("gateway", run)


def check_dns(server: str | None, name: str) -> CheckResult:
    def run():
        if server:
            addresses = query_dns(server, name)
            if not addresses:
                return False, f"{server} returned no A record for {name}"
            return True, f"{server} resolved {name} to {addresses[0]}"

        info = socket.getaddrinfo(name, None, socket.AF_INET)
        return True, f"system resolver returned {info[0][4][0]} for {name}"

    return timed("dns", run)


def query_dns(server: str, name: str, timeout: float = 2.0) -> list[str]:
    transaction = b"\xab\xcd"
    header = transaction + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    question = b"".join(bytes([len(part)]) + part.encode() for part in name.split(".")) + b"\x00"
    packet = header + question + b"\x00\x01\x00\x01"

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(2048)

    answer_count = int.from_bytes(data[6:8], "big")
    if answer_count == 0:
        return []

    offset = len(header)
    while data[offset] != 0:
        offset += data[offset] + 1
    offset += 5

    addresses = []
    for _ in range(answer_count):
        offset += 2
        record_type = int.from_bytes(data[offset : offset + 2], "big")
        offset += 8
        length = int.from_bytes(data[offset : offset + 2], "big")
        offset += 2
        if record_type == 1 and length == 4:
            addresses.append(".".join(str(byte) for byte in data[offset : offset + 4]))
        offset += length

    return addresses


def check_tcp(host: str, port: int, timeout: float = 3.0) -> CheckResult:
    def run():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            if sock.connect_ex((host, port)) != 0:
                return False, f"tcp {host}:{port} refused or filtered"
        return True, f"tcp {host}:{port} accepted the connection"

    return timed(f"tcp {port}", run)


def check_http(url: str, timeout: float = 5.0, verify: bool = True) -> CheckResult:
    def run():
        context = ssl.create_default_context()
        if not verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        request = urllib.request.Request(url, headers={"User-Agent": "nettool"})
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                return True, f"{url} returned {response.status}"
        except urllib.error.HTTPError as exc:
            return exc.code < 500, f"{url} returned {exc.code}"
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                return False, f"{url} served a certificate this machine does not trust"
            return False, f"{url} unreachable: {exc.reason}"

    return timed("http", run)


def run_suite(
    gateway: str | None,
    dns_server: str | None,
    hostname: str,
    url: str,
    verify_tls: bool = True,
) -> list[CheckResult]:
    results = []
    if gateway:
        results.append(check_gateway(gateway))
    results.append(check_dns(dns_server, hostname))
    results.append(check_tcp(hostname, 443))
    results.append(check_http(url, verify=verify_tls))
    return results
