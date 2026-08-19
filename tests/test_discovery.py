from nettool.discovery import is_broadcast_or_multicast, normalise_state, parse_arp_output

WINDOWS_OUTPUT = """
Interfaz: 192.168.0.14 --- 0xe
  Direccion de Internet      Direccion fisica      Tipo
  192.168.0.1           2c-00-ab-6a-da-a8     dinamico
  192.168.0.8           40-9c-a7-4a-eb-d4     dinamico
  192.168.0.255         ff-ff-ff-ff-ff-ff     estatico
  224.0.0.22            01-00-5e-00-00-16     estatico
"""

LINUX_OUTPUT = """
192.168.0.1 dev eth0 lladdr 2c:00:ab:6a:da:a8 REACHABLE
192.168.0.8 dev eth0 lladdr 40:9C:A7:4A:EB:D4 STALE
192.168.0.44 dev eth0  FAILED
fe80::1 dev eth0 lladdr aa:bb:cc:dd:ee:ff router STALE
"""


def test_windows_output_is_parsed():
    entries = parse_arp_output(WINDOWS_OUTPUT, windows=True)
    addresses = [entry["address"] for entry in entries]

    assert "192.168.0.1" in addresses
    assert entries[0]["mac"] == "2c:00:ab:6a:da:a8"
    assert entries[0]["state"] == "dynamic"


def test_linux_output_is_parsed_and_macs_lowercased():
    entries = parse_arp_output(LINUX_OUTPUT, windows=False)
    by_address = {entry["address"]: entry for entry in entries}

    assert by_address["192.168.0.8"]["mac"] == "40:9c:a7:4a:eb:d4"
    assert by_address["192.168.0.1"]["state"] == "reachable"


def test_entries_without_a_mac_are_skipped():
    entries = parse_arp_output(LINUX_OUTPUT, windows=False)

    assert "192.168.0.44" not in [entry["address"] for entry in entries]


def test_ipv6_neighbours_are_skipped():
    entries = parse_arp_output(LINUX_OUTPUT, windows=False)

    assert all(entry["address"].count(".") == 3 for entry in entries)


def test_results_are_sorted_numerically_not_alphabetically():
    text = """
192.168.0.100 dev eth0 lladdr aa:aa:aa:aa:aa:01 REACHABLE
192.168.0.9 dev eth0 lladdr aa:aa:aa:aa:aa:02 REACHABLE
"""
    entries = parse_arp_output(text, windows=False)

    assert [entry["address"] for entry in entries] == ["192.168.0.9", "192.168.0.100"]


def test_localised_states_are_normalised():
    assert normalise_state("dinamico") == "dynamic"
    assert normalise_state("estatico") == "static"
    assert normalise_state("STALE") == "stale"


def test_broadcast_and_multicast_are_recognised():
    assert is_broadcast_or_multicast({"address": "192.168.0.255", "mac": "ff:ff:ff:ff:ff:ff"})
    assert is_broadcast_or_multicast({"address": "224.0.0.22", "mac": "01:00:5e:00:00:16"})
    assert not is_broadcast_or_multicast({"address": "192.168.0.8", "mac": "40:9c:a7:4a:eb:d4"})


def test_duplicate_addresses_are_collapsed():
    text = """
192.168.0.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE
192.168.0.1 dev wlan0 lladdr aa:bb:cc:dd:ee:ff STALE
"""
    assert len(parse_arp_output(text, windows=False)) == 1
