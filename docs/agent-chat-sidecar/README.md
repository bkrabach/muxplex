# Agent-sidecar network fence

Deployment artifacts for the fence described in
[`../AGENT_CHAT_SIDECAR.md`](../AGENT_CHAT_SIDECAR.md) §6.

**The directory layout mirrors the install layout.** `etc/...` installs to
`/etc/...`, `usr/local/sbin/...` to `/usr/local/sbin/...`. There is no mapping
table to keep in sync, and no step where a file's destination has to be
remembered.

These files are host wiring, not importable code. Nothing under `muxplex/`
loads them; `muxplex/tests/test_agent_fence.py` is the only in-repo thing that
observes their effect, and it does so through the live system rather than by
reading these files.

## What was wrong with the fence this replaces

`AGENT_CHAT_SIDECAR.md` §6 previously flagged one gap — the iptables rules were
not persistent, so they vanished on reboot and the fence disappeared silently.

That was true. It was also not the whole gap. **Measured on the live host,
before any reboot, the fence was already porous.** It named two destinations:

```
-d 127.0.0.1/32       --dport 8088 -j REJECT
-d 10.119.176.180/32  --dport 8088 -j REJECT
```

muxplex binds `0.0.0.0:8088`, so it answers on every address in `127.0.0.0/8`,
of which that rule covered exactly one. Probed as `aa-svc`:

| target | before |
|---|---|
| `127.0.0.1:8088` | blocked |
| `127.0.0.2:8088` | **HTTP 200, unauthenticated** |
| `127.0.0.9:8088` | **HTTP 200, unauthenticated** |
| `10.119.176.180:8088` | blocked |
| `127.0.0.1:8188` (2nd muxplex) | **HTTP 200, unauthenticated** |
| `127.0.0.1:7681` (ttyd) | **HTTP 404 — reachable** |

The 200s are *unauthenticated* because `ip route get 127.0.0.2` returns
`src 127.0.0.1`, so the connection arrives wearing the address
`muxplex/auth.py`'s `_LOCALHOST_ADDRS` waves straight through.

So the sidecar had the full muxplex API with no credential, on a box where the
hand-verification had passed. The verification was not wrong about what it
checked — it checked one address out of sixteen million.

## What replaces it

An address denylist cannot express *"you may not talk to this machine."* The
fence inverts the default: `aa-svc` may not initiate a connection to anything
local, with one narrow allowance for the DNS stub resolver it needs to reach
its upstream model API.

```
-A OUTPUT -m owner --uid-owner <AA_UID> -j MUXPLEX_AGENT_FENCE

MUXPLEX_AGENT_FENCE:
  -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN   # it is a server on 9099
  -d 127.0.0.0/8 -p udp --dport 53 -j RETURN             # stub resolver
  -d 127.0.0.0/8 -p tcp --dport 53 -j RETURN
  -d 127.0.0.0/8 -p tcp -j REJECT --reject-with tcp-reset
  -d 127.0.0.0/8       -j REJECT --reject-with icmp-port-unreachable
  -p tcp -m multiport --dports 8088,8188 -j REJECT --reject-with tcp-reset
```

The last rule is destination-independent on purpose: it stops the sidecar
looping back via the host's own LAN address, survives a DHCP change that an
address-pinned rule would not, and also blocks a *federated* muxplex on another
host. Mirrored in `ip6tables` against `::1/128`.

### Side effect: the sidecar can no longer reach ttyd on `:7681`

Rule 3 rejects **all** sidecar-initiated loopback TCP, not just muxplex's
ports. ttyd (`127.0.0.1:7681`) is inside that blast radius and is now
unreachable from `aa-svc`, where previously it answered.

This is intended, not collateral. ttyd is a terminal server; a process that can
open a socket to it is a process that can be handed a terminal. The sidecar has
no legitimate need for it — the browser, not the agent, drives every muxplex
interaction. Anything later given to `aa-svc` that *does* need a local service
must be added to the fence explicitly, which is the point: the allowance is a
decision someone makes in this file, not a gap nobody noticed.

## Persistence, without `iptables-persistent`

There is no `iptables-persistent` on the box and none was installed. The fence
unit re-derives the rules on every boot and then **proves** them before
declaring success. That is strictly stronger than restoring a saved ruleset: a
restore that no longer blocks anything still restores, silently. This fails.

## Why absence is loud

Three independent locks, because the failure being closed is specifically the
one that degrades quietly:

1. `Requires=` — no fence unit, no sidecar. Covers boot.
2. `BindsTo=` — fence unit stops or fails, systemd stops the sidecar. Covers a
   rule deleted at 3am on an already-running box.
3. `ExecStartPre=+…verify` — covers the path the other two do not: an operator
   running `systemctl start amplifier-agent-http` while the unit is nominally
   "active" but the chain has been flushed underneath it. The `+` runs it as
   root rather than inheriting `User=aa-svc`.

Plus a 30s watchdog that re-proves the property and, on breach, logs
`auth.alert` and stops both the sidecar and the fence unit. Recovery requires
starting the fence unit, which re-applies *and* re-proves — there is no path
back to serving that skips the proof. No warn-and-continue, no
timeout-and-proceed, no env var that disables it.

## Why `verify` is the load-bearing subcommand

`muxplex-agent-fence verify` does not read the ruleset and conclude "looks
right." It attempts the connections the fence exists to stop, from the real
UID, over real sockets, and fails if any succeed.

- It **discovers muxplex's listening ports from the running system** (`ss`) and
  fails if a live instance is on a port not named in
  `etc/muxplex-agent-fence.conf`. A new instance widens the hole loudly instead
  of silently.
- It runs a **positive control first** — root must be able to reach muxplex.
  Without it, "muxplex is down" and "the fence works" produce identical green
  output.
- **Ambiguous probe results (timeout, error) fail.** They are not evidence.

`etc/muxplex-agent-fence.conf` is therefore trusted by the applier (so the
applied rules are deterministic and auditable) and *not* trusted by the
verifier.

## The test

`muxplex/tests/test_agent_fence.py`, 6 tests. Run on the deployment host:

```bash
cd /opt/muxplex
MUXPLEX_TEST_ALLOW_LIVE_HOST=1 .venv/bin/python -m pytest muxplex/tests/test_agent_fence.py -v
```

The override is required because `conftest.pytest_sessionstart` refuses to run
against a live server, and this test is only evidence *because* it runs against
one. It is safe for this file specifically, mechanically: the file never
imports `serve()`, never touches `settings.json`, never signals a process. It
opens client sockets and reads `systemctl`/`ss`.

The test never calls `muxplex-agent-fence verify`. A test that delegates to the
implementation's own self-check passes whenever both share a blind spot.

It skips in exactly two cases — no `aa-svc` user (no sidecar to fence), and not
root (cannot probe). **A missing fence is a failure, never a skip.** A skip
that fired when the fence went missing would reproduce the original bug inside
the test meant to catch it.
