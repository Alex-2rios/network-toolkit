# nettool

[![ci](https://github.com/Alex-2rios/network-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/Alex-2rios/network-toolkit/actions/workflows/ci.yml)

Command line tools I got tired of not having in one place: a subnet calculator, VLSM planning,
host discovery, a TCP port check and a connectivity test that tells you which layer is broken.

Pure standard library, no dependencies. Python 3.10 or newer, works the same on Linux and
Windows.

## Install

```bash
pip install -e .
```

Or run it straight out of the repo without installing anything:

```bash
python -m nettool subnet 192.168.10.0/24
```

## Subnetting

```
$ nettool subnet 172.16.32.0/20
        network  172.16.32.0/20
        netmask  255.255.240.0
       wildcard  0.0.15.255
      broadcast  172.16.47.255
     first host  172.16.32.1
      last host  172.16.47.254
total addresses  4096
   usable hosts  4094
          class  B
          scope  private
```

The wildcard mask is there because I kept calculating it by hand for ACLs.

## VLSM

Give it a supernet and what each segment needs, and it lays them out largest first so nothing
overlaps and the leftover space stays contiguous:

```
$ nettool vlsm 192.168.1.0/24 sales:50 it:25 servers:12 wan:2
192.168.1.0/24 allocated to 4 subnets

name            subnet              mask              needed  usable  spare
sales           192.168.1.0/26      255.255.255.192       50      62     12
                192.168.1.1 - 192.168.1.62
it              192.168.1.64/27     255.255.255.224       25      30      5
                192.168.1.65 - 192.168.1.94
servers         192.168.1.96/28     255.255.255.240       12      14      2
                192.168.1.97 - 192.168.1.110
wan             192.168.1.112/30    255.255.255.252        2       2      0
                192.168.1.113 - 192.168.1.114

116 of 256 addresses used, 140 left
```

If it does not fit, it says which subnet it choked on and how much space was already committed,
which is more useful than a traceback when you are planning an addressing scheme.

## Splitting a network

```
$ nettool split 10.10.0.0/22 -n 4
10.10.0.0/22 split into 4 subnets

subnet              range                                hosts
10.10.0.0/24        10.10.0.1 - 10.10.0.254                254
10.10.1.0/24        10.10.1.1 - 10.10.1.254                254
10.10.2.0/24        10.10.2.1 - 10.10.2.254                254
10.10.3.0/24        10.10.3.1 - 10.10.3.254                254
```

Ask for 3 subnets and you still get /26s, because subnetting rounds up to a power of two whether
you like it or not.

## Finding hosts

```bash
nettool sweep 192.168.1.0/24 --resolve --ports 22,80,443
```

Threaded ping sweep, optional reverse DNS and an optional port probe on whatever answers. It
refuses anything bigger than a /20 unless you pass `--force`, because I once typed a /8 and
waited a long time for nothing.

## Port check

```bash
nettool ports 192.168.1.10
nettool ports 192.168.1.10 --ports 22,80,8000-8100
```

With no port list it checks a built in set of the usual suspects and prints the service name next
to each open port.

## Who is on this network

```
$ nettool arp
address           mac                 state
172.28.212.177    00:15:5d:9d:db:24   dynamic
192.168.0.1       2c:00:ab:6a:da:a8   dynamic
192.168.0.8       40:9c:a7:4a:eb:d4   dynamic

3 neighbours known to this machine
```

Reads the neighbour table, which on Windows is `arp -a` and on Linux is `ip neigh`. Broadcast and
multicast entries are hidden unless you ask for them with `-a`, because they are noise on every
single machine. States are normalised too, so a Spanish Windows saying `dinamico` and a Linux
saying `REACHABLE` both come out as something you can compare.

## Machine readable output

Every command takes `--json`:

```bash
nettool --json vlsm 10.0.0.0/24 sales:50 wan:2
nettool --json sweep 192.168.1.0/24 | jq '.hosts[].address'
```

Colour is turned off automatically when the output is JSON or not a terminal, so piping it into
anything else does not give you escape codes in your data.

## Connectivity, layer by layer

```
$ nettool check --gateway 192.168.1.1 --dns 1.1.1.1
local addresses: 192.168.1.42

[ok  ] gateway   192.168.1.1 replied in 1.2 ms                                    4 ms
[ok  ] dns       1.1.1.1 resolved one.one.one.one to 1.0.0.1                     59 ms
[ok  ] tcp 443   tcp one.one.one.one:443 accepted the connection                 68 ms
[ok  ] http      https://one.one.one.one returned 200                           273 ms

all 4 checks passed
```

The order is the point. Gateway, then name resolution, then a TCP handshake, then a full HTTPS
request. Whichever line fails first tells you where to look instead of "the internet is down".
It exits non zero when anything fails, so it drops straight into a script or a cron job.

DNS is queried by building the query packet by hand over UDP rather than calling the system
resolver. That way a working `getaddrinfo` cannot hide a DNS server that is actually not
answering.

`--insecure` skips certificate verification, which is how I found out the network I was on
re-signs TLS with its own CA. Verification failing is reported differently from the host being
unreachable, because those are two very different problems.

## Tests

```bash
pytest
```

31 tests, all offline, run against Python 3.10 through 3.13 in CI. They cover the subnet maths,
the CLI argument handling and the neighbour table parsing for both the Windows and the Linux
output formats. The parsing is a pure function taking text, exactly so it can be tested without a
network.

The parts that actually touch the network are deliberately thin wrappers around `ping`, sockets
and `subprocess`, so there is nothing there worth mocking.

## What I learned

- `ipaddress` in the standard library does more than most people use. Half of a subnet calculator
  is already written, the value I added was the VLSM allocator and readable output.
- /31 and /32 are the edge cases everyone forgets. A /31 has two usable addresses on a point to
  point link, not zero, and my first version happily reported negative host counts.
- `ping` has different flags on Windows and Linux (`-n` versus `-c`, `-w` in milliseconds versus
  `-W` in seconds), so the sweep detects the platform. Parsing the latency out of the output needs
  to handle a decimal comma too, depending on the locale.
- A ping sweep is IO bound, so a thread pool of 64 turns a /24 from minutes into a couple of
  seconds. More threads past that point stop helping, the bottleneck moves to the timeout.
- `connect_ex` instead of `connect` for port scanning. It returns an error code instead of
  raising, which keeps the scanning loop readable.
- Parsing command output means parsing whatever locale the machine is in. My first neighbour
  table parser matched the English word for a dynamic entry and returned nothing on a Spanish
  Windows. Normalising to a small set of known states fixed it, and the tests now cover both.
- Separating "run the command" from "parse the output" made the parser testable with a string
  literal. That is most of what the test suite for this feature is.

## Working on this

```bash
make help
```

The usual ones: `make install, make test, make lint, make demo`.

Every push runs the CI workflow described above. A second workflow, `security.yml`, runs weekly
and on every push: it scans the history for committed secrets with gitleaks and audits the
dependencies with pip-audit.

Dependabot opens pull requests for the GitHub Actions and the dependencies once a week.

Line endings are pinned to LF through `.gitattributes`, because half of this was written on
Windows and shell scripts with carriage returns fail on Linux in a way that is genuinely
confusing the first time.
