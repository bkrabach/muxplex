"""The agent sidecar must not be able to reach muxplex. Proven, not asserted.

WHY THIS FILE EXISTS -- do not weaken it into a rule-shape check:

The chat panel's entire security story is one sentence, from
``docs/AGENT_CHAT_SIDECAR.md``:

    "The agent process holds no muxplex credential of any kind ... there is
     no path by which the sidecar initiates a call into muxplex."

The first clause is a fact about config files. The second is a fact about the
*network*, and it is the load-bearing one -- because muxplex grants an
**unauthenticated** bypass to any peer whose socket address is ``127.0.0.1``
(``muxplex/auth.py``, ``_LOCALHOST_ADDRS``). A sidecar that can open a socket
to muxplex is therefore not merely "on the same box"; it is *inside the trust
boundary with no credential required*. The claim and the fence are the same
claim. If the fence is down, the feature's security model is not degraded --
it is absent.

WHAT THIS TEST CAUGHT THE DAY IT WAS WRITTEN
--------------------------------------------
The original fence was two iptables rules::

    -d 127.0.0.1/32 --dport 8088 -j REJECT
    -d <LAN IP>/32  --dport 8088 -j REJECT

It had been verified by hand, once, against ``127.0.0.1:8088``, and it did
block that. Measured against the actual property, on a box where everything
"appeared to work", the sidecar's UID could still reach:

    127.0.0.2:8088   -> HTTP 200, UNAUTHENTICATED
    127.0.0.9:8088   -> HTTP 200, UNAUTHENTICATED
    127.0.0.1:8188   -> HTTP 200  (a second muxplex instance, unfenced)
    127.0.0.1:7681   -> ttyd, a terminal server

All of ``127.0.0.0/8`` is loopback, muxplex binds ``0.0.0.0`` so it answers
on every one of those addresses, and ``ip route get 127.0.0.2`` selects
``src 127.0.0.1`` -- so the connection arrives wearing precisely the address
the auth middleware waves through. The hand-verification was not wrong about
what it checked. It checked one address out of sixteen million.

That is the specific failure mode this file exists to prevent: a check that
tests a *proxy for* the property (is this one rule present? does this one
address refuse?) instead of the property itself (can this identity reach
muxplex, by any route, at all?).

HOW IT AVOIDS BEING THAT SAME MISTAKE
-------------------------------------
1. It probes as the **real sidecar UID**, over a **real socket**, at every
   address muxplex actually answers on -- including ``127.0.0.2``, the one
   that exposed the original hole.
2. It discovers muxplex's listening ports **from the running system**, not
   from a constant here or in the fence's own config. A new instance on a
   new port fails this test instead of quietly widening the hole.
3. It runs a **positive control** first. Without one, "muxplex is down"
   scores identically to "the fence works", and the suite would go green on
   a box with no protection whatsoever.
4. It never calls ``muxplex-agent-fence verify``. A test that delegates to
   the implementation's own self-check passes whenever both share a blind
   spot. The probing here is independent by construction.
5. An ambiguous result (timeout, probe error) is a **failure**, never a
   pass. The fence rejects with ``tcp-reset`` specifically so that a genuine
   block is instant and unmistakable; anything slower is unproven.

SKIP POLICY -- read before adding one
-------------------------------------
This file skips in exactly two situations, and neither of them is "the fence
seems to be missing":

* the sidecar user does not exist -- there is no sidecar here to fence, so
  the property is vacuous;
* we are not root, and therefore cannot assume the sidecar's identity to
  probe -- we cannot run the test at all, which is reported as a skip rather
  than disguised as a pass.

If the sidecar user **does** exist and we **can** probe, an absent or porous
fence is a FAILURE. That asymmetry is the point. A skip that fires when the
fence goes missing would reproduce the original bug in the test that was
supposed to catch it.

RUNNING IT
----------
This test must run on the deployment box, against the live services -- that
is what makes it evidence. ``conftest.pytest_sessionstart`` refuses to run
when something is serving the default port, so select this file explicitly
and set the documented override::

    MUXPLEX_TEST_ALLOW_LIVE_HOST=1 pytest muxplex/tests/test_agent_fence.py

That override is safe **for this file specifically**, and the reason is
mechanical, not a promise: everything here is read-only. It never imports
``serve()``, never touches ``settings.json``, never signals a process. It
opens client sockets and reads ``systemctl``/``ss`` output. The rail exists
to stop the suite from destroying a live server; this file cannot.
"""

from __future__ import annotations

import errno
import json
import os
import pwd
import re
import shutil
import subprocess

import pytest

# Must match AA_USER in /etc/muxplex-agent-fence.conf and User= in
# amplifier-agent-http.service.
SIDECAR_USER = "aa-svc"

SIDECAR_UNIT = "amplifier-agent-http.service"
FENCE_UNIT = "muxplex-agent-fence.service"

# Addresses every muxplex bound to 0.0.0.0 answers on. 127.0.0.2 is not
# padding -- it is the address that proved the original fence porous, and it
# is the one that carries the unauthenticated loopback bypass with it.
PROBE_LOOPBACK = ("127.0.0.1", "127.0.0.2")

_CONNECT_TIMEOUT = 4.0

# errnos that mean "the kernel actively stopped this", i.e. the fence worked.
_BLOCKED_ERRNOS = frozenset(
    {
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
        errno.EACCES,
        errno.EPERM,
    }
)


# --------------------------------------------------------------------------
# applicability
# --------------------------------------------------------------------------


def _sidecar_user_exists() -> bool:
    try:
        pwd.getpwnam(SIDECAR_USER)
    except KeyError:
        return False
    return True


needs_sidecar_deployment = pytest.mark.skipif(
    not _sidecar_user_exists(),
    reason=(
        f"no {SIDECAR_USER!r} user on this host -- the agent sidecar is not "
        f"deployed here, so there is no identity to fence. (This skip is "
        f"about the sidecar's ABSENCE. If the sidecar exists and the fence "
        f"does not, these tests fail; they must never skip their way past a "
        f"missing fence.)"
    ),
)

needs_root = pytest.mark.skipif(
    os.geteuid() != 0,
    reason=(
        "not root: cannot assume the sidecar's identity to probe. Reported as "
        "a skip because the test could not RUN -- never because it passed."
    ),
)


# --------------------------------------------------------------------------
# probing -- deliberately independent of the fence's own tooling
# --------------------------------------------------------------------------


def _probe_as(user: str, addr: str, port: int) -> str:
    """Attempt one TCP connect to *addr*:*port* as OS user *user*.

    Returns ``"OPEN"``, ``"BLOCKED"``, ``"TIMEOUT"``, or ``"ERROR:<name>"``.

    Runs the connect in a child process under ``setuid(user)`` rather than
    shelling out to ``curl``, so the result reflects the kernel's answer to
    the socket rather than any HTTP-layer behaviour on top of it. The fence
    is a network control and is measured at the network layer.
    """
    src = r"""
import errno, os, pwd, socket, sys
name, addr, port, tmo = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
rec = pwd.getpwnam(name)
os.setgroups([])
os.setgid(rec.pw_gid)
os.setuid(rec.pw_uid)          # drop privilege irreversibly before connecting
assert os.geteuid() == rec.pw_uid, "failed to drop to target uid"
fam = socket.AF_INET6 if ":" in addr else socket.AF_INET
s = socket.socket(fam, socket.SOCK_STREAM)
s.settimeout(tmo)
try:
    s.connect((addr, port))
    print("OPEN")
except socket.timeout:
    print("TIMEOUT")
except OSError as e:
    print("ERRNO:{}".format(errno.errorcode.get(e.errno, e.errno)))
finally:
    s.close()
"""

    proc = subprocess.run(
        ["python3", "-c", src, user, addr, str(port), str(_CONNECT_TIMEOUT)],
        capture_output=True,
        text=True,
        timeout=_CONNECT_TIMEOUT + 6,
        check=False,
    )
    out = proc.stdout.strip()
    if out == "OPEN":
        return "OPEN"
    if out == "TIMEOUT":
        return "TIMEOUT"
    if out.startswith("ERRNO:"):
        name = out.split(":", 1)[1]
        code = getattr(errno, name, None)
        return "BLOCKED" if code in _BLOCKED_ERRNOS else f"ERROR:{name}"
    return f"ERROR:{out or proc.stderr.strip()[:120] or 'no-output'}"


def _muxplex_listen_ports() -> set[int]:
    """Ports a live muxplex process is listening on, discovered from the OS.

    Read from the running system on purpose. Taking this list from a
    constant -- here or in the fence's config -- would mean a muxplex started
    on a new port is invisible to this test, which is the exact class of
    silent gap the file exists to close.
    """
    out = subprocess.run(
        ["ss", "-lntpH"], capture_output=True, text=True, timeout=10, check=False
    ).stdout
    ports: set[int] = set()
    for line in out.splitlines():
        if "muxplex" not in line:
            continue
        m = re.search(r"[\d.:*\[\]]+:(\d+)\s", line)
        if m:
            ports.add(int(m.group(1)))
    return ports


def _local_ipv4_addresses() -> set[str]:
    """Non-loopback IPv4 addresses this host answers on."""
    out = subprocess.run(
        ["ip", "-4", "-json", "addr", "show", "scope", "global"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    ).stdout
    addrs: set[str] = set()
    for iface in json.loads(out or "[]"):
        for info in iface.get("addr_info", []):
            if info.get("family") == "inet" and info.get("local"):
                addrs.add(info["local"])
    return addrs


def _systemctl_show(unit: str, prop: str) -> str:
    return subprocess.run(
        ["systemctl", "show", unit, "-p", prop, "--value"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    ).stdout.strip()


@pytest.fixture(scope="module")
def muxplex_ports() -> set[int]:
    ports = _muxplex_listen_ports()
    if not ports:
        pytest.fail(
            "No listening muxplex process found. Every negative result below "
            "would be vacuous -- a connection refused because nothing is "
            "there proves nothing about the fence. Refusing to report a pass. "
            "Start muxplex and re-run on the deployment host."
        )
    return ports


# --------------------------------------------------------------------------
# 1. positive control -- without this, everything below is meaningless
# --------------------------------------------------------------------------


@needs_sidecar_deployment
@needs_root
def test_control_muxplex_is_reachable_by_an_unfenced_identity(muxplex_ports):
    """A non-sidecar identity CAN reach muxplex.

    This is the test that stops the whole file from passing for the wrong
    reason. Every assertion below is of the form "this connection failed" --
    and a connection to a dead server also fails. Without this control, an
    outage and a working fence produce identical, green output.

    ``root`` is used simply as an identity the fence does not name.
    """
    for port in sorted(muxplex_ports):
        verdict = _probe_as("root", "127.0.0.1", port)
        assert verdict == "OPEN", (
            f"CONTROL FAILED: root could not reach muxplex at 127.0.0.1:{port} "
            f"({verdict}). This does not mean the fence is working -- it means "
            f"this test cannot tell the difference between a fence and an "
            f"outage. Fix muxplex, then re-run."
        )


# --------------------------------------------------------------------------
# 2. the actual property
# --------------------------------------------------------------------------


@needs_sidecar_deployment
@needs_root
def test_sidecar_cannot_reach_muxplex_on_any_loopback_address(muxplex_ports):
    """The sidecar's UID cannot reach muxplex over loopback.

    Not "over 127.0.0.1" -- over loopback. The original fence pinned
    ``127.0.0.1/32`` and was bypassed by ``127.0.0.2``, which reaches the
    same ``0.0.0.0``-bound server AND arrives with ``src 127.0.0.1``, so
    muxplex's unauthenticated loopback bypass fires on the way in. Both
    addresses are probed; a fence that covers only the first fails here.
    """
    reached: list[str] = []
    unproven: list[str] = []

    for port in sorted(muxplex_ports):
        for addr in PROBE_LOOPBACK:
            verdict = _probe_as(SIDECAR_USER, addr, port)
            if verdict == "OPEN":
                reached.append(f"{addr}:{port}")
            elif verdict != "BLOCKED":
                unproven.append(f"{addr}:{port} ({verdict})")

    assert not reached, (
        f"FENCE BREACH: {SIDECAR_USER} opened a connection to muxplex at "
        f"{', '.join(reached)}.\n\n"
        f"muxplex grants an UNAUTHENTICATED bypass to peers at 127.0.0.1 "
        f"(muxplex/auth.py, _LOCALHOST_ADDRS), and the kernel selects "
        f"src=127.0.0.1 for any 127.0.0.0/8 destination -- so this is not "
        f"'the sidecar can see the port'. It is 'the sidecar has the full "
        f"muxplex API with no credential'. The feature's security claim is "
        f"currently false.\n\n"
        f"Repair: /usr/local/sbin/muxplex-agent-fence apply && "
        f"/usr/local/sbin/muxplex-agent-fence verify"
    )
    assert not unproven, (
        f"FENCE UNPROVEN at {', '.join(unproven)}. A timeout or probe error "
        f"is not evidence of blocking -- the fence rejects with tcp-reset so "
        f"a real block is immediate. Scoring this as a pass is how a fence "
        f"disappears unnoticed, so it fails instead."
    )


@needs_sidecar_deployment
@needs_root
def test_sidecar_cannot_reach_muxplex_via_this_hosts_own_lan_address(muxplex_ports):
    """The sidecar cannot loop back in via the host's external address.

    A loopback-only fence is bypassed by dialling the box's own LAN IP: the
    packet leaves via the NIC path and arrives at the same server. muxplex
    binds ``0.0.0.0``, so this is a real route, not a theoretical one.
    """
    lan = _local_ipv4_addresses()
    if not lan:
        pytest.skip("host has no global IPv4 address; nothing to probe")

    reached: list[str] = []
    unproven: list[str] = []
    for port in sorted(muxplex_ports):
        for addr in sorted(lan):
            verdict = _probe_as(SIDECAR_USER, addr, port)
            if verdict == "OPEN":
                reached.append(f"{addr}:{port}")
            elif verdict != "BLOCKED":
                unproven.append(f"{addr}:{port} ({verdict})")

    assert not reached, (
        f"FENCE BREACH: {SIDECAR_USER} reached muxplex via this host's own "
        f"LAN address at {', '.join(reached)}. A fence that covers only "
        f"loopback does not stop the sidecar; it just makes it take one more "
        f"hop."
    )
    assert not unproven, f"FENCE UNPROVEN at {', '.join(unproven)}; see above."


# --------------------------------------------------------------------------
# 3. drift -- the fence must cover what is actually running
# --------------------------------------------------------------------------


@needs_sidecar_deployment
@needs_root
def test_every_running_muxplex_instance_is_fenced(muxplex_ports):
    """A second muxplex on another port must not be a way around the fence.

    The POC host ran two instances -- ``0.0.0.0:8088`` and
    ``127.0.0.1:8188``. The fence named only 8088, so the sidecar had a
    complete, unauthenticated muxplex API on 8188. Same binary, same
    endpoints, no fence. This test enumerates instances from the OS, so
    adding one without extending the fence turns red here rather than
    quietly reopening the hole.
    """
    unfenced: list[int] = []
    for port in sorted(muxplex_ports):
        if _probe_as(SIDECAR_USER, "127.0.0.1", port) == "OPEN":
            unfenced.append(port)

    assert not unfenced, (
        f"A muxplex instance is listening on port(s) {unfenced} that "
        f"{SIDECAR_USER} can reach. Every muxplex on this box is the same "
        f"API with the same loopback auth bypass -- fencing one and not the "
        f"others fences nothing. Add the port to MUXPLEX_PORTS in "
        f"/etc/muxplex-agent-fence.conf and re-apply."
    )


# --------------------------------------------------------------------------
# 4. the enforcement must itself be un-removable-by-accident
# --------------------------------------------------------------------------


@needs_sidecar_deployment
def test_sidecar_unit_cannot_start_without_the_fence():
    """The sidecar's dependency on the fence is structural, not documentary.

    The fence's original failure mode was not that it was wrong -- it was
    that it could vanish (a reboot, a flush, a stray ``-D``) while every
    surface still looked healthy. The systemd wiring is what converts that
    into an outage instead of a silent downgrade, so its absence has to be a
    test failure in its own right; by the time the probe tests above notice,
    the sidecar has already been running unprotected.
    """
    if shutil.which("systemctl") is None:
        pytest.skip("systemd not present on this host")

    requires = _systemctl_show(SIDECAR_UNIT, "Requires")
    binds = _systemctl_show(SIDECAR_UNIT, "BindsTo")
    after = _systemctl_show(SIDECAR_UNIT, "After")

    assert FENCE_UNIT in requires, (
        f"{SIDECAR_UNIT} no longer Requires={FENCE_UNIT}. Without it the "
        f"sidecar starts happily on a box with no fence -- exactly the "
        f"silent-degradation state this whole mechanism exists to make "
        f"impossible."
    )
    assert FENCE_UNIT in binds, (
        f"{SIDECAR_UNIT} no longer BindsTo={FENCE_UNIT}. Requires= governs "
        f"startup only; BindsTo= is what takes the sidecar down when the "
        f"fence fails at 3am on an already-running box."
    )
    assert FENCE_UNIT in after, (
        f"{SIDECAR_UNIT} is not ordered After={FENCE_UNIT}. Without "
        f"ordering, both can start concurrently and the sidecar can be live "
        f"and un-fenced for the width of that race."
    )


@needs_sidecar_deployment
def test_fence_is_enabled_for_boot():
    """The fence must come back by itself after a reboot.

    The POC's rules were applied by hand with no persistence: correct until
    the first restart, then gone, with nothing on any dashboard changing. An
    enabled unit is what makes reboot a non-event.
    """
    if shutil.which("systemctl") is None:
        pytest.skip("systemd not present on this host")

    state = subprocess.run(
        ["systemctl", "is-enabled", FENCE_UNIT],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    ).stdout.strip()

    assert state == "enabled", (
        f"{FENCE_UNIT} is '{state}', not 'enabled'. It will not be applied on "
        f"the next boot, and the sidecar will come up un-fenced while "
        f"everything still appears to work. Run: systemctl enable {FENCE_UNIT}"
    )
