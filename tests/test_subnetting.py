import ipaddress

import pytest

from nettool import subnetting
from nettool.subnetting import SubnetError


def net(value):
    return ipaddress.IPv4Network(value)


def test_describe_a_typical_lan():
    details = subnetting.describe(net("192.168.10.0/24"))

    assert details["netmask"] == "255.255.255.0"
    assert details["wildcard"] == "0.0.0.255"
    assert details["broadcast"] == "192.168.10.255"
    assert details["first host"] == "192.168.10.1"
    assert details["last host"] == "192.168.10.254"
    assert details["usable hosts"] == "254"
    assert details["scope"] == "private"


def test_point_to_point_link_has_two_usable_addresses():
    link = net("10.0.0.0/31")

    assert subnetting.usable_hosts(link) == 2
    assert subnetting.usable_range(link) == ("10.0.0.0", "10.0.0.1")


def test_single_host_route():
    assert subnetting.usable_hosts(net("10.0.0.5/32")) == 1


def test_missing_prefix_is_a_friendly_error():
    with pytest.raises(SubnetError, match="missing a prefix"):
        subnetting.parse_network("192.168.1.0")


def test_host_bits_are_normalised():
    assert str(subnetting.parse_network("192.168.1.77/24")) == "192.168.1.0/24"


def test_scope_detection():
    assert subnetting.scope(net("8.8.8.0/24")) == "public"
    assert subnetting.scope(net("127.0.0.0/8")) == "loopback"
    assert subnetting.scope(net("169.254.0.0/16")) == "link local"
    assert subnetting.scope(net("172.16.0.0/12")) == "private"


def test_split_rounds_up_to_a_power_of_two():
    subnets = subnetting.split_into(net("10.0.0.0/24"), 3)

    assert len(subnets) == 3
    assert subnets[0] == net("10.0.0.0/26")
    assert subnets[2] == net("10.0.0.128/26")


def test_split_refuses_when_there_are_not_enough_bits():
    with pytest.raises(SubnetError, match="not enough bits"):
        subnetting.split_into(net("10.0.0.0/30"), 8)


def test_prefix_for_hosts_accounts_for_network_and_broadcast():
    assert subnetting.prefix_for_hosts(2) == 30
    assert subnetting.prefix_for_hosts(50) == 26
    assert subnetting.prefix_for_hosts(62) == 26
    assert subnetting.prefix_for_hosts(63) == 25


def test_vlsm_allocates_largest_first_without_overlap():
    allocations = subnetting.vlsm(
        net("192.168.1.0/24"),
        [("sales", 50), ("it", 25), ("wan", 2)],
    )

    assert [a.label for a in allocations] == ["sales", "it", "wan"]
    assert str(allocations[0].network) == "192.168.1.0/26"
    assert str(allocations[1].network) == "192.168.1.64/27"
    assert str(allocations[2].network) == "192.168.1.96/30"

    for first, second in zip(allocations, allocations[1:], strict=False):
        assert not first.network.overlaps(second.network)


def test_vlsm_orders_by_size_not_by_input_order():
    allocations = subnetting.vlsm(net("10.0.0.0/24"), [("small", 2), ("big", 100)])

    assert allocations[0].label == "big"
    assert allocations[0].network.prefixlen == 25


def test_vlsm_reports_where_it_ran_out():
    with pytest.raises(SubnetError, match="ran out of space"):
        subnetting.vlsm(net("192.168.1.0/26"), [("a", 60), ("b", 60)])


def test_vlsm_tracks_wasted_addresses():
    allocation = subnetting.vlsm(net("10.0.0.0/24"), [("team", 40)])[0]

    assert allocation.hosts_available == 62
    assert allocation.wasted == 22


def test_summarize_collapses_contiguous_networks():
    result = subnetting.summarize([net("10.0.0.0/25"), net("10.0.0.128/25")])

    assert result == [net("10.0.0.0/24")]
