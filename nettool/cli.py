from __future__ import annotations

import argparse
import ipaddress
import json
import sys

from nettool import connectivity, discovery, subnetting
from nettool.subnetting import SubnetError

GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"


def colour_enabled(args: argparse.Namespace) -> bool:
    return sys.stdout.isatty() and not getattr(args, "json", False)


def paint(text: str, colour: str, enabled: bool) -> str:
    return f"{colour}{text}{RESET}" if enabled else text


def emit(args: argparse.Namespace, payload: object) -> bool:
    if getattr(args, "json", False):
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return True
    return False


def cmd_subnet(args: argparse.Namespace) -> int:
    network = subnetting.parse_network(args.network)
    details = subnetting.describe(network)

    if emit(args, details):
        return 0

    width = max(len(key) for key in details)
    for key, value in details.items():
        print(f"{key.rjust(width)}  {value}")
    return 0


def subnet_row(subnet: ipaddress.IPv4Network) -> dict[str, object]:
    first, last = subnetting.usable_range(subnet)
    return {
        "subnet": str(subnet),
        "netmask": str(subnet.netmask),
        "first_host": first,
        "last_host": last,
        "usable_hosts": subnetting.usable_hosts(subnet),
    }


def cmd_split(args: argparse.Namespace) -> int:
    network = subnetting.parse_network(args.network)

    if args.prefix:
        if args.prefix <= network.prefixlen:
            raise SubnetError(f"/{args.prefix} is not smaller than /{network.prefixlen}")
        subnets = list(network.subnets(new_prefix=args.prefix))
    else:
        subnets = subnetting.split_into(network, args.count)

    rows = [subnet_row(subnet) for subnet in subnets]

    if emit(args, {"network": str(network), "count": len(rows), "subnets": rows}):
        return 0

    print(f"{network} split into {len(subnets)} subnets")
    print()
    print(f"{'subnet':<20}{'range':<34}{'hosts':>8}")
    for row in rows:
        span = f"{row['first_host']} - {row['last_host']}"
        print(f"{row['subnet']:<20}{span:<34}{row['usable_hosts']:>8}")
    return 0


def parse_requirements(raw_values: list[str]) -> list[tuple[str, int]]:
    requirements = []
    for raw in raw_values:
        if ":" not in raw:
            raise SubnetError(f"expected name:hosts, got '{raw}'")
        label, _, hosts = raw.partition(":")
        if not hosts.strip().isdigit():
            raise SubnetError(f"'{hosts}' is not a host count in '{raw}'")
        requirements.append((label.strip(), int(hosts)))
    return requirements


def cmd_vlsm(args: argparse.Namespace) -> int:
    supernet = subnetting.parse_network(args.network)
    allocations = subnetting.vlsm(supernet, parse_requirements(args.subnet))
    used = sum(allocation.network.num_addresses for allocation in allocations)

    rows = []
    for allocation in allocations:
        row = subnet_row(allocation.network)
        row["name"] = allocation.label
        row["hosts_required"] = allocation.hosts_required
        row["spare"] = allocation.wasted
        rows.append(row)

    if emit(
        args,
        {
            "supernet": str(supernet),
            "addresses_total": supernet.num_addresses,
            "addresses_used": used,
            "addresses_free": supernet.num_addresses - used,
            "allocations": rows,
        },
    ):
        return 0

    dim = DIM if colour_enabled(args) else ""
    reset = RESET if colour_enabled(args) else ""

    print(f"{supernet} allocated to {len(allocations)} subnets")
    print()
    print(f"{'name':<16}{'subnet':<20}{'mask':<17}{'needed':>7}{'usable':>8}{'spare':>7}")
    for row in rows:
        print(
            f"{row['name']:<16}"
            f"{row['subnet']:<20}"
            f"{row['netmask']:<17}"
            f"{row['hosts_required']:>7}"
            f"{row['usable_hosts']:>8}"
            f"{row['spare']:>7}"
        )
        print(f"{'':<16}{dim}{row['first_host']} - {row['last_host']}{reset}")

    free = supernet.num_addresses - used
    print()
    print(f"{used} of {supernet.num_addresses} addresses used, {free} left")
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    network = subnetting.parse_network(args.network)

    if network.num_addresses > 4096 and not args.force:
        print(
            f"{network} has {network.num_addresses} addresses, that will take a while. "
            f"Add --force if you meant it.",
            file=sys.stderr,
        )
        return 2

    ports = parse_ports(args.ports) if args.ports else None

    if not getattr(args, "json", False):
        print(f"sweeping {network} with {args.workers} workers")

    hosts = discovery.sweep(
        network,
        workers=args.workers,
        timeout=args.timeout,
        resolve_names=args.resolve,
        check_ports=ports,
    )

    rows = [
        {
            "address": host.address,
            "latency_ms": host.latency_ms,
            "hostname": host.hostname,
            "open_ports": [
                {"port": port, "service": discovery.COMMON_PORTS.get(port, "unknown")}
                for port in host.open_ports
            ],
        }
        for host in hosts
    ]

    if emit(args, {"network": str(network), "hosts_up": len(rows), "hosts": rows}):
        return 0

    colour = colour_enabled(args)
    print()
    for host in hosts:
        latency = f"{host.latency_ms:.1f} ms" if host.latency_ms is not None else "up"
        line = f"{paint(host.address.ljust(16), GREEN, colour)}{latency:<12}"
        if host.hostname:
            line += f"{host.hostname:<32}"
        if host.open_ports:
            line += ", ".join(
                f"{port}/{discovery.COMMON_PORTS.get(port, 'tcp')}" for port in host.open_ports
            )
        print(line)

    print()
    print(f"{len(hosts)} hosts answered out of {network.num_addresses - 2} scanned")
    return 0


def cmd_ports(args: argparse.Namespace) -> int:
    ports = parse_ports(args.ports) if args.ports else sorted(discovery.COMMON_PORTS)

    if not getattr(args, "json", False):
        print(f"scanning {len(ports)} ports on {args.host}")

    open_ports = discovery.scan_ports(args.host, ports, timeout=args.timeout)
    rows = [
        {"port": port, "service": discovery.COMMON_PORTS.get(port, "unknown")}
        for port in open_ports
    ]

    if emit(
        args,
        {"host": args.host, "scanned": len(ports), "open": len(rows), "ports": rows},
    ):
        return 0

    colour = colour_enabled(args)
    print()
    if not open_ports:
        print("no open ports found")
        return 0

    for row in rows:
        print(f"{paint(str(row['port']).rjust(6), GREEN, colour)}  {row['service']}")

    print()
    print(f"{len(open_ports)} open, {len(ports) - len(open_ports)} closed or filtered")
    return 0


def cmd_arp(args: argparse.Namespace) -> int:
    entries = discovery.arp_table(include_broadcast=args.all)

    if emit(args, {"entries": entries, "count": len(entries)}):
        return 0

    if not entries:
        print("the neighbour table is empty, try pinging something first")
        return 0

    print(f"{'address':<18}{'mac':<20}state")
    for entry in entries:
        print(f"{entry['address']:<18}{entry['mac']:<20}{entry['state']}")

    print()
    print(f"{len(entries)} neighbours known to this machine")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    results = connectivity.run_suite(
        gateway=args.gateway,
        dns_server=args.dns,
        hostname=args.host,
        url=args.url,
        verify_tls=not args.insecure,
    )

    rows = [
        {
            "name": result.name,
            "ok": result.ok,
            "detail": result.detail,
            "elapsed_ms": round(result.elapsed_ms, 1),
        }
        for result in results
    ]
    failed = [row for row in rows if not row["ok"]]

    if emit(
        args,
        {
            "local_addresses": discovery.local_addresses(),
            "checks": rows,
            "failed": len(failed),
        },
    ):
        return 1 if failed else 0

    colour = colour_enabled(args)
    print(f"local addresses: {', '.join(discovery.local_addresses()) or 'none found'}")
    print()

    for result in results:
        mark = paint("ok  ", GREEN, colour) if result.ok else paint("fail", RED, colour)
        print(f"[{mark}] {result.name:<10}{result.detail:<58}{result.elapsed_ms:>7.0f} ms")

    print()
    if failed:
        print(f"{len(failed)} of {len(rows)} checks failed")
        return 1

    print(f"all {len(rows)} checks passed")
    return 0


def parse_ports(raw: str) -> list[int]:
    ports: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start, _, end = chunk.partition("-")
            ports.update(range(int(start), int(end) + 1))
        else:
            ports.add(int(chunk))

    invalid = [port for port in ports if not 1 <= port <= 65535]
    if invalid:
        raise SubnetError(f"port out of range: {invalid[0]}")
    return sorted(ports)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettool",
        description="subnetting, host discovery and connectivity checks",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print machine readable output instead of a table",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_subnet = sub.add_parser("subnet", help="describe a network")
    p_subnet.add_argument("network", help="for example 192.168.10.0/24")
    p_subnet.set_defaults(func=cmd_subnet)

    p_split = sub.add_parser("split", help="split a network into equal subnets")
    p_split.add_argument("network")
    p_split.add_argument("-n", "--count", type=int, default=2, help="how many subnets")
    p_split.add_argument("-p", "--prefix", type=int, help="split to this prefix instead")
    p_split.set_defaults(func=cmd_split)

    p_vlsm = sub.add_parser("vlsm", help="variable length allocation from a supernet")
    p_vlsm.add_argument("network")
    p_vlsm.add_argument("subnet", nargs="+", metavar="NAME:HOSTS", help="for example sales:50")
    p_vlsm.set_defaults(func=cmd_vlsm)

    p_sweep = sub.add_parser("sweep", help="find live hosts on a network")
    p_sweep.add_argument("network")
    p_sweep.add_argument("-w", "--workers", type=int, default=64)
    p_sweep.add_argument("-t", "--timeout", type=float, default=1.0)
    p_sweep.add_argument("-r", "--resolve", action="store_true", help="reverse dns lookups")
    p_sweep.add_argument("--ports", help="also probe these ports on hosts that answer")
    p_sweep.add_argument("--force", action="store_true", help="allow networks larger than /20")
    p_sweep.set_defaults(func=cmd_sweep)

    p_ports = sub.add_parser("ports", help="tcp port scan of one host")
    p_ports.add_argument("host")
    p_ports.add_argument("--ports", help="22,80,8000-8100, defaults to a common list")
    p_ports.add_argument("-t", "--timeout", type=float, default=0.6)
    p_ports.set_defaults(func=cmd_ports)

    p_arp = sub.add_parser("arp", help="show the neighbour table of this machine")
    p_arp.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="include broadcast and multicast entries, which are hidden by default",
    )
    p_arp.set_defaults(func=cmd_arp)

    p_check = sub.add_parser("check", help="validate connectivity end to end")
    p_check.add_argument("-g", "--gateway", help="gateway address to ping first")
    p_check.add_argument("-d", "--dns", help="dns server to query directly")
    p_check.add_argument("--host", default="one.one.one.one", help="name to resolve and reach")
    p_check.add_argument("--url", default="https://one.one.one.one", help="url to fetch")
    p_check.add_argument(
        "--insecure",
        action="store_true",
        help="skip tls verification, useful behind a proxy that rewrites certificates",
    )
    p_check.set_defaults(func=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except SubnetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ipaddress.AddressValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
