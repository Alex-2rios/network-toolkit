from __future__ import annotations

import ipaddress
from dataclasses import dataclass


class SubnetError(ValueError):
    pass


def parse_network(value: str) -> ipaddress.IPv4Network:
    try:
        if "/" not in value:
            raise SubnetError(f"{value} is missing a prefix, try {value}/24")
        return ipaddress.IPv4Network(value, strict=False)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError) as exc:
        raise SubnetError(str(exc)) from exc


def wildcard(network: ipaddress.IPv4Network) -> ipaddress.IPv4Address:
    return ipaddress.IPv4Address(int(network.hostmask))


def usable_range(network: ipaddress.IPv4Network) -> tuple[str, str]:
    if network.prefixlen >= 31:
        return str(network.network_address), str(network.broadcast_address)
    return str(network.network_address + 1), str(network.broadcast_address - 1)


def usable_hosts(network: ipaddress.IPv4Network) -> int:
    if network.prefixlen == 32:
        return 1
    if network.prefixlen == 31:
        return 2
    return network.num_addresses - 2


def address_class(network: ipaddress.IPv4Network) -> str:
    first = int(str(network.network_address).split(".")[0])
    if first < 128:
        return "A"
    if first < 192:
        return "B"
    if first < 224:
        return "C"
    if first < 240:
        return "D (multicast)"
    return "E (reserved)"


def scope(network: ipaddress.IPv4Network) -> str:
    if network.is_loopback:
        return "loopback"
    if network.is_link_local:
        return "link local"
    if network.is_private:
        return "private"
    if network.is_multicast:
        return "multicast"
    return "public"


def describe(network: ipaddress.IPv4Network) -> dict[str, str]:
    first, last = usable_range(network)
    return {
        "network": f"{network.network_address}/{network.prefixlen}",
        "netmask": str(network.netmask),
        "wildcard": str(wildcard(network)),
        "broadcast": str(network.broadcast_address),
        "first host": first,
        "last host": last,
        "total addresses": str(network.num_addresses),
        "usable hosts": str(usable_hosts(network)),
        "class": address_class(network),
        "scope": scope(network),
    }


def split_into(network: ipaddress.IPv4Network, count: int) -> list[ipaddress.IPv4Network]:
    if count < 1:
        raise SubnetError("count has to be at least 1")

    bits = 0
    while (1 << bits) < count:
        bits += 1

    new_prefix = network.prefixlen + bits
    if new_prefix > 32:
        raise SubnetError(f"cannot split /{network.prefixlen} into {count} subnets, not enough bits")

    return list(network.subnets(new_prefix=new_prefix))[:count]


@dataclass
class Allocation:
    label: str
    hosts_required: int
    network: ipaddress.IPv4Network

    @property
    def hosts_available(self) -> int:
        return usable_hosts(self.network)

    @property
    def wasted(self) -> int:
        return self.hosts_available - self.hosts_required


def prefix_for_hosts(hosts: int) -> int:
    if hosts < 1:
        raise SubnetError("every subnet needs at least one host")

    bits = 1
    while (2**bits) - 2 < hosts:
        bits += 1
        if bits > 30:
            raise SubnetError(f"{hosts} hosts does not fit in an IPv4 subnet")
    return 32 - bits


def vlsm(
    supernet: ipaddress.IPv4Network,
    requirements: list[tuple[str, int]],
) -> list[Allocation]:
    if not requirements:
        raise SubnetError("give me at least one subnet requirement")

    ordered = sorted(requirements, key=lambda item: item[1], reverse=True)
    cursor = int(supernet.network_address)
    limit = int(supernet.broadcast_address)
    allocations: list[Allocation] = []

    for label, hosts in ordered:
        prefix = prefix_for_hosts(hosts)
        size = 2 ** (32 - prefix)

        if cursor % size:
            cursor += size - (cursor % size)

        if cursor + size - 1 > limit:
            allocated = sum(alloc.network.num_addresses for alloc in allocations)
            raise SubnetError(
                f"ran out of space in {supernet} at '{label}', "
                f"{allocated} of {supernet.num_addresses} addresses already assigned"
            )

        network = ipaddress.IPv4Network((cursor, prefix))
        allocations.append(Allocation(label=label, hosts_required=hosts, network=network))
        cursor += size

    return allocations


def summarize(networks: list[ipaddress.IPv4Network]) -> list[ipaddress.IPv4Network]:
    return list(ipaddress.collapse_addresses(networks))
