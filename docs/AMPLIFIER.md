# Driving muxplex from Amplifier (optional)

**muxplex does not depend on [Amplifier](https://github.com/microsoft/amplifier).**
It never imports it, never requires it, and works exactly the same whether or not
Amplifier is installed anywhere on the box. Amplifier is one convenient way to
point an AI agent at muxplex's HTTP API — the API itself is vendor-neutral and
documented in [`AGENT_GUIDE.md`](AGENT_GUIDE.md).

This page is for people who already use Amplifier and want their agents to see
and drive their tmux sessions.

---

## What the bundle gives you

[`bkrabach/amplifier-bundle-muxplex`](https://github.com/bkrabach/amplifier-bundle-muxplex)
is an Amplifier **behavior bundle**. Composed into your own Amplifier
configuration, it lets agents:

- **list** sessions and read their pane contents,
- **create** and delete sessions,
- **monitor** long-running work — poll a session, watch for completion, notice
  which sessions need attention.

The part worth paying for is not any single call — it's that connection setup
happens **once, in one place**. Without it, every consumer that wants to talk to
muxplex re-derives the same three things: which URL this instance is on, where
the federation key lives, and which certificate file to verify against. Each of
those has a known failure mode (a 307 to `/login` instead of a 401, a 403 from a
missing Bearer header, `unable to get local issuer certificate` from trusting the
leaf instead of the CA). The bundle resolves them once so your agents don't each
get to rediscover them.

Everything it does is available over plain HTTP. Nothing here is a capability
muxplex only exposes to Amplifier.

---

## Install it at the app level

**Install this into your own Amplifier app/bundle configuration — not into
muxplex.** This is the part people get wrong: it is not a muxplex dependency, not
something `uv tool install muxplex` should pull in, and not something that lives
in this repo. It belongs alongside the rest of *your* Amplifier setup, so that
your agents get the capability and muxplex stays a plain HTTP server.

Add the behavior to your own bundle's `includes:`:

```yaml
---
bundle:
  name: my-agent-setup
  version: 1.0.0

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - bundle: git+https://github.com/bkrabach/amplifier-bundle-muxplex@main#subdirectory=behaviors/muxplex
---
```

To try it before wiring it into your own bundle, run it standalone:

```bash
amplifier run --bundle git+https://github.com/bkrabach/amplifier-bundle-muxplex@main \
  "list my muxplex sessions"
```

---

## Configuration

Three values, all resolved from the environment and the muxplex config directory
— the same ones a hand-written client would need.

| What | Where | Notes |
|---|---|---|
| Server URL | `MUXPLEX_URL` env var | e.g. `https://my-host:8088`. `https://` if the instance has TLS configured. |
| Federation key | `~/.config/muxplex/federation_key` **on the server** (mode `0600`) | Override the path with `MUXPLEX_FEDERATION_KEY_FILE`. Generate one with `POST /api/federation/generate-key`. **Not needed at all** when the agent runs on the same host as the server — muxplex bypasses auth for socket-level `127.0.0.1`/`::1`. |
| CA certificate | a local file, e.g. `muxplex-ca.crt` | Only for TLS instances using `muxplex setup-tls --method ca`. Fetch it with `curl -sk "$MUXPLEX_URL/api/ca" -o muxplex-ca.crt`. |

Two things about the CA file that cost real debugging time if you get them wrong:

1. **Fetch the CA, never the leaf.** `/api/ca` serves `muxplex-ca.crt` (the trust
   anchor). `muxplex.crt` is the *leaf* the server presents on the wire; verifying
   against it produces `unable to get local issuer certificate`.
2. **`/api/ca` returns 404 unless the server used `--method ca`.** Under
   `tailscale` the cert is already publicly trusted (no CA file needed); under
   `mkcert` or `selfsigned` there is no servable CA. The 404 body says which case
   you're in.

Full detail, including the `/ca.crt` and `/setup` variants and what to do in each
non-`ca` case, is in [`AGENT_GUIDE.md` §1](AGENT_GUIDE.md#1-authentication).

---

## Not using Amplifier?

Point whatever you *are* using at
[**`AGENT_GUIDE.md`**](AGENT_GUIDE.md) — the vendor-neutral operational contract
for the raw API: authentication, TLS trust bootstrap, reading state, session
lifecycle, typing into a live pane, and the completion/attention patterns for
unattended work. It assumes no framework and is safe to hand to any agent or
script.

For Python specifically, [`muxplex-client`](../client/README.md) is a typed
sync/async client (`pip install muxplex-client`) with no server dependencies:

```python
from muxplex_client import MuxplexClient

with MuxplexClient(server_url, federation_key, ca_file="muxplex-ca.crt") as client:
    for session in client.sessions():
        print(session.name)
```
