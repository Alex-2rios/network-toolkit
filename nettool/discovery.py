from __future__ import annotations

import ipaddress
import platform
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

WINDOWS = platform.system().lower() == "windows"

COMMON_PORTS = {
    21: "ftp",
    22: "ssh",
    23: "telnet",
    25: "smtp",
    53: "dns",
    80: "http",
    110: "pop3",
    123: "ntp",
    135: "msrpc",
    139: "netbios",
    143: "imap",
    161: "snmp",
    389: "ldap",
    443: "https",
    445: "smb",
    514: "syslog",
    587: "submission",
    993: "imaps",
    1433: "mssql",
    3306: "mysql",
    3389: "rdp",
    5432: "postgres",
    5900: "vnc",
    6379: "redis",
    8006: "proxmox",
    8080: "http-alt",
    8443: "https-alt",
    9090: "prometheus",
    9100: "node-exporter",
}


@dataclass
class Host:
    address: str
    alive: bool
    latency_ms: float | None = None
    hostname: str | None = None
    open_ports: list[int] = field(default_factory=list)


def ping(address: str, timeout: float = 1.0) -> tuple[bool, float | None]:
    if WINDOWS:
        command = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), address]
    else:
        command = ["ping", "-c", "1", "-W", str(int(max(timeout, 1))), address]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 2,
            text=True,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False, None

    if result.returncode != 0:
        return False, None

    match = re.search(r"[=<]\s*([\d.,]+)\s*ms", result.stdout)
    if match:
        try:
            return True, float(match.group(1).replace(",", "."))
        except ValueError:
            return True, None
    return True, None


def resolve(address: str) -> str | None:
    try:
        return socket.gethostbyaddr(address)[0]
    except (socket.herror, socket.gaierror, OSError):
        return None


def probe_port(address: str, port: int, timeout: float = 0.6) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((address, port)) == 0


def scan_ports(
    address: str,
    ports: list[int],
    timeout: float = 0.6,
    workers: int = 100,
) -> list[int]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(lambda port: (port, probe_port(address, port, timeout)), ports)
    return sorted(port for port, is_open in results if is_open)


def sweep(
    network: ipaddress.IPv4Network,
    workers: int = 64,
    timeout: float = 1.0,
    resolve_names: bool = False,
    check_ports: list[int] | None = None,
) -> list[Host]:
    targets = list(network.hosts()) if network.prefixlen < 31 else list(network)

    def inspect(address: ipaddress.IPv4Address) -> Host:
        text = str(address)
        alive, latency = ping(text, timeout)
        host = Host(address=text, alive=alive, latency_ms=latency)
        if alive and resolve_names:
            host.hostname = resolve(text)
        if alive and check_ports:
            host.open_ports = scan_ports(text, check_ports, timeout=0.4)
        return host

    with ThreadPoolExecutor(max_workers=workers) as pool:
        hosts = list(pool.map(inspect, targets))

    return [host for host in hosts if host.alive]


def normalise_state(raw: str) -> str:
    lowered = raw.lower()
    if lowered in {"reachable", "stale", "delay", "probe", "failed", "incomplete", "permanent"}:
        return lowered
    if lowered.startswith(("din", "dyn")):
        return "dynamic"
    if lowered.startswith(("est", "sta")):
        return "static"
    return lowered


def is_broadcast_or_multicast(entry: dict[str, str]) -> bool:
    if entry["mac"] in {"ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00"}:
        return True
    if entry["mac"].startswith(("01:00:5e", "33:33")):
        return True
    first = int(entry["address"].split(".")[0])
    return first >= 224 or entry["address"].endswith(".255")


def parse_arp_output(text: str, windows: bool) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []

    for line in text.splitlines():
        parts = line.split()
        if windows:
            if len(parts) >= 3 and parts[0].count(".") == 3 and "-" in parts[1]:
                entries.append(
                    {
                        "address": parts[0],
                        "mac": parts[1].replace("-", ":").lower(),
                        "state": normalise_state(parts[2]),
                    }
                )
        else:
            if "lladdr" in parts and parts[0].count(".") == 3:
                entries.append(
                    {
                        "address": parts[0],
                        "mac": parts[parts.index("lladdr") + 1].lower(),
                        "state": normalise_state(parts[-1]),
                    }
                )

    seen = set()
    unique = []
    for entry in entries:
        if entry["address"] in seen:
            continue
        seen.add(entry["address"])
        unique.append(entry)

    return sorted(unique, key=lambda item: tuple(int(part) for part in item["address"].split(".")))


def arp_table(include_broadcast: bool = False) -> list[dict[str, str]]:
    command = ["arp", "-a"] if WINDOWS else ["ip", "neigh", "show"]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            text=True,
            errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError):
        return []

    entries = parse_arp_output(result.stdout, WINDOWS)
    if include_broadcast:
        return entries
    return [entry for entry in entries if not is_broadcast_or_multicast(entry)]


def local_addresses() -> list[str]:
    addresses = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            addresses.add(sock.getsockname()[0])
    except OSError:
        pass

    return sorted(addresses)
