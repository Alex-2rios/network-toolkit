import pytest

from nettool.cli import main, parse_ports
from nettool.subnetting import SubnetError


def test_subnet_command_prints_the_basics(capsys):
    assert main(["subnet", "192.168.10.0/24"]) == 0

    output = capsys.readouterr().out
    assert "255.255.255.0" in output
    assert "192.168.10.254" in output
    assert "254" in output


def test_split_command_lists_every_subnet(capsys):
    assert main(["split", "10.0.0.0/24", "-n", "4"]) == 0

    output = capsys.readouterr().out
    assert "10.0.0.0/26" in output
    assert "10.0.0.192/26" in output


def test_split_by_prefix(capsys):
    assert main(["split", "10.0.0.0/24", "-p", "26"]) == 0
    assert capsys.readouterr().out.count("10.0.0.") >= 4


def test_vlsm_command(capsys):
    assert main(["vlsm", "192.168.1.0/24", "sales:50", "it:25", "wan:2"]) == 0

    output = capsys.readouterr().out
    assert "192.168.1.0/26" in output
    assert "192.168.1.96/30" in output


def test_vlsm_rejects_bad_syntax(capsys):
    assert main(["vlsm", "192.168.1.0/24", "sales-50"]) == 2
    assert "expected name:hosts" in capsys.readouterr().err


def test_bad_network_exits_with_two(capsys):
    assert main(["subnet", "not-an-address/24"]) == 2
    assert "error:" in capsys.readouterr().err


def test_sweep_refuses_huge_ranges_without_force(capsys):
    assert main(["sweep", "10.0.0.0/16"]) == 2
    assert "--force" in capsys.readouterr().err


def test_parse_ports_handles_ranges_and_lists():
    assert parse_ports("22,80,443") == [22, 80, 443]
    assert parse_ports("8000-8003") == [8000, 8001, 8002, 8003]
    assert parse_ports("80, 80, 22") == [22, 80]


def test_parse_ports_rejects_out_of_range():
    with pytest.raises(SubnetError, match="out of range"):
        parse_ports("70000")
