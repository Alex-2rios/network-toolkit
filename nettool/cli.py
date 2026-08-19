from __future__ import annotations

import argparse
import ipaddress
import sys

from nettool import connectivity, discovery, subnetting
from nettool.subnetting import SubnetError

GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"


def supports_colour() -> bool:
    return sys.stdout.isatty()


def paint(text: str, colour: str) -> str:
    return f"{colour}{text}{RESET}" if supports_colour() else text


def cmd_subnet(args: argparse.Namespace) -> int:
    network = subnetting.parse_network(args.network)
    details = subnetting.describe(network)
    width = max(len(key) for key in details)
    for key, value in details.items():
        print(f"{key.rjust(width)}  {value}")
    return 0


def cmd_split(args: argparse.Namespace) -> int:
    network = subnetting.parse_network(args.network)

    if args.prefix:
        if args.prefix <= network.prefixlen:
            raise SubnetError(f"/{args.prefix} is not smaller than /{network.prefixlen}")
        subnets = list(network.subnets(new_prefix=args.prefix))
    else:
        subnets = subnetting.split_into(network, args.count)

    print(f"{network} split into {len(subnets)} subnets")
    print()
    print(f"{'subnet':<20}{'range':<34}{'hosts':>8}")
    for subnet in subnets:
        first, last = subnetting.usable_range(subnet)
        print(f"{str(subnet):<20}{first + ' - ' + last:<34}{subnetting.usable_hosts(subnet):>8}")
    return 0


def cmd_vlsm(args: argparse.Namespace) -> int:
    supernet = subnetting.parse_network(args.network)
    requirements = []

    for raw in args.subnet:
        if ":" not in raw:
            raise SubnetError(f"expected name:hosts, got '{raw}'")
        label, _, hosts = raw.partition(":")
        if not hosts.strip().isdigit():
            raise SubnetError(f"'{hosts}' is not a host count in '{raw}'")
        requirements.append((label.strip(), int(hosts)))

    allocations = subnetting.vlsm(supernet, requirements)

    print(f"{supernet} allocated to {len(allocations)} subnets")
    print()
    print(f"{'name':<16}{'subnet':<20}{'mask':<17}{'needed':>7}{'usable':>8}{'spare':>7}")
    for allocation in allocations:
        first, last = subnetting.usable_range(allocation.network)
        print(
            f"{allocation.label:<16}"
            f"{str(allocation.network):<20}"
            f"{str(allocation.network.netmask):<17}"
            f"{allocation.hosts_required:>7}"
            f"{allocation.hosts_available:>8}"
            f"{allocation.wasted:>7}"
        )
        print(f"{'':<16}{DIM if supports_colour() else ''}{first} - {last}{RESET if supports_colour() else ''}")

    used = sum(allocation.network.num_addresses for allocation in allocations)
    print()
    print(f"{used} of {supernet.num_addresses} addresses used, {supernet.num_addresses - used} left")
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

    ports = None
    if args.ports:
        ports = parse_ports(args.ports)

    print(f"sweeping {network} with {args.workers} workers")
    hosts = discovery.sweep(
        network,
        workers=args.workers,
        timeout=args.timeout,
        resolve_names=args.resolve,
        check_ports=ports,
    )

    print()
    for host in hosts:
        latency = f"{host.latency_ms:.1f} ms" if host.latency_ms is not None else "up"
        line = f"{paint(host.address.ljust(16), GREEN)}{latency:<12}"
        if host.hostname:
            line += f"{host.hostname:<32}"
        if host.open_ports:
            named = ", ".join(
                f"{port}/{discovery.COMMON_PORTS.get(port, 'tcp')}" for port in host.open_ports
            )
            line += named
        print(line)

    print()
    print(f"{len(hosts)} hosts answered out of {network.num_addresses - 2} scanned")
    return 0


def cmd_ports(args: argparse.Namespace) -> int:
    ports = parse_ports(args.ports) if args.ports else sorted(discovery.COMMON_PORTS)
    print(f"scanning {len(ports)} ports on {args.host}")

    open_ports = discovery.scan_ports(args.host, ports, timeout=args.timeout)

    print()
    if not open_ports:
        print("no open ports found")
        return 0

    for port in open_ports:
        service = discovery.COMMON_PORTS.get(port, "unknown")
        print(f"{paint(str(port).rjust(6), GREEN)}  {service}")

    print()
    print(f"{len(open_ports)} open, {len(ports) - len(open_ports)} closed or filtered")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    print(f"local addresses: {', '.join(discovery.local_addresses()) or 'none found'}")
    print()

    results = connectivity.run_suite(
        gateway=args.gateway,
        dns_server=args.dns,
        hostname=args.host,
        url=args.url,
        verify_tls=not args.insecure,
    )

    for result in results:
        mark = paint("ok  ", GREEN) if result.ok else paint("fail", RED)
        print(f"[{mark}] {result.name:<10}{result.detail:<58}{result.elapsed_ms:>7.0f} ms")

    failed = [result for result in results if not result.ok]
    print()
    if failed:
        print(f"{len(failed)} of {len(results)} checks failed")
        return 1

    print(f"all {len(results)} checks passed")
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
