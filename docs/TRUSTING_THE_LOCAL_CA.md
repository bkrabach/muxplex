# Trusting the local CA

This guide walks through getting browser-trusted HTTPS for muxplex on plain LAN names (e.g. `https://my-host:8088`, `https://192.168.1.5:8088`) without requiring Tailscale on every client and without buying a public domain.

## When this is the right approach

Use `muxplex setup-tls --method ca` when **all** of the following apply:

- You want to access muxplex by its plain LAN hostname (`my-host`) or IP address.
- Tailscale isn't an option on every client device (corporate IT policy blocks it; can't install on a phone; the URL must be the bare hostname; etc.).
- You don't want to buy and configure a public domain just to point it at a LAN IP.
- You can install a root certificate on each client device. A user-level (`CurrentUser`) trust install is enough; admin/elevation is **not** needed on most platforms.

If Tailscale is acceptable on every client, prefer `--method tailscale` — it gives you a real Let's Encrypt certificate with zero per-client setup beyond the Tailscale app itself.

## Why this fixes PWA install + reopen flakiness

Browsers (Chromium, WebKit, Firefox) refuse to keep an installed PWA in standalone mode against an origin that doesn't present a trusted certificate. The behavior the user sees:

1. First install: click through the cert warning, install the PWA, it works.
2. Reopen: the OS launches the PWA shell; the browser re-evaluates the cert; the click-through exception isn't re-applied to the standalone launch path; the PWA is kicked back into a regular browser tab to surface the warning. Once you click through there, the standalone shell isn't re-entered.

A self-signed leaf in the trust store *can* fix this, but you have to re-import on every cert rotation. A persistent **CA** in the trust store fixes it permanently — leaf rotations don't break trust because clients trust the issuer, not the leaf.

## How the `ca` method works

```
muxplex setup-tls --method ca
```

generates and persists a private CA, then signs a leaf TLS certificate with it.

| Artifact | Path | Validity |
|---|---|---|
| CA cert | `~/.config/muxplex/ca/muxplex-ca.crt` | 10 years |
| CA private key | `~/.config/muxplex/ca/muxplex-ca.key` (mode `0600`) | 10 years |
| Leaf cert | `~/.config/muxplex/muxplex.crt` | 397 days |
| Leaf private key | `~/.config/muxplex/muxplex.key` (mode `0600`) | 397 days |

The leaf's Subject Alternative Name (SAN) automatically includes:

- `<hostname>` — `socket.gethostname()`
- `<hostname>.local` — for mDNS / Bonjour resolution
- `localhost`
- The Tailscale MagicDNS name (`<host>.<tailnet>.ts.net`) — only if `tailscale status` reports one
- IPs: `127.0.0.1`, `::1`, and the primary outbound IPv4 address of the host (auto-detected)

The CA cert has the standard CA extensions (`BasicConstraints CA:TRUE, pathlen:0`, `KeyUsage keyCertSign+cRLSign`), so OS trust stores and browsers accept it as a Root.

Re-running `muxplex setup-tls --method ca` is idempotent for the CA: if `~/.config/muxplex/ca/muxplex-ca.crt` and `.key` already exist, they're reused — only a fresh leaf is generated. Clients that already trust the CA don't need to re-import anything when the leaf rotates.

## Per-platform install instructions

### Windows (Chrome / Edge)

PowerShell, **no admin needed**. Replace `<path-to-ca.crt>` with the path you copied the file to.

```powershell
Import-Certificate -FilePath <path-to-ca.crt> -CertStoreLocation Cert:\CurrentUser\Root
```

If you'd rather not type the path, paste the PEM inline:

```powershell
$pem = @'
-----BEGIN CERTIFICATE-----
... paste PEM contents from ~/.config/muxplex/ca/muxplex-ca.crt ...
-----END CERTIFICATE-----
'@
$path = "$env:TEMP\muxplex-ca.crt"
$pem | Set-Content -Path $path -Encoding ASCII
Import-Certificate -FilePath $path -CertStoreLocation Cert:\CurrentUser\Root
```

Verify:

```powershell
Get-ChildItem Cert:\CurrentUser\Root | Where-Object Subject -like "*muxplex Local CA*"
```

**Restart Edge / Chrome** (fully quit, not just close the window) for them to re-read the cert store. Firefox uses its own store — see the Firefox section below.

### macOS (Safari / Chrome / Edge)

```sh
sudo security add-trusted-cert -d -r trustRoot \
    -k /Library/Keychains/System.keychain \
    /path/to/muxplex-ca.crt
```

This adds the CA to the System keychain and marks it as trusted for SSL. Safari, Chrome, and Edge all use the system keychain. Firefox uses its own store — see below.

Verify:

```sh
security find-certificate -c "muxplex Local CA" /Library/Keychains/System.keychain
```

To remove later:

```sh
sudo security delete-certificate -c "muxplex Local CA" /Library/Keychains/System.keychain
```

### Linux (system-wide)

```sh
sudo cp /path/to/muxplex-ca.crt /usr/local/share/ca-certificates/muxplex-ca.crt
sudo update-ca-certificates
```

This covers the system trust store used by `curl`, `git`, etc., and Chrome / Chromium / Edge on most distros.

For RPM-based systems (Fedora, RHEL):

```sh
sudo cp /path/to/muxplex-ca.crt /etc/pki/ca-trust/source/anchors/muxplex-ca.crt
sudo update-ca-trust
```

Firefox on Linux uses its own store — see below.

### iOS / iPadOS (Safari + Web Apps)

1. Get the CA cert onto the device — easiest is AirDrop from a Mac, or email the `.crt` file to yourself.
2. Open the file in Mail or Files; iOS will offer to download a profile.
3. Go to **Settings → General → VPN & Device Management → Downloaded Profile** and tap **Install**.
4. **Critical second step:** go to **Settings → General → About → Certificate Trust Settings** and toggle **Enable Full Trust for Root Certificates** for the muxplex CA. Without this, Safari and WKWebView don't honor the cert.

After both steps, Safari accepts the cert and PWAs added to the home screen launch in standalone mode without warnings.

### Android (Chrome)

Chrome on Android uses the system trust store, but user-installed CAs are flagged with a persistent "your traffic may be monitored" notice. The basic flow:

1. Copy the `.crt` file to the device (USB, Drive, Files, etc.).
2. **Settings → Security & privacy → More security & privacy → Encryption & credentials → Install a certificate → CA certificate**.
3. Acknowledge the warning and select the file.

Result varies by Android version and OEM. Some PWA features (service workers, push) may degrade in this state; basic browser HTTPS works.

For a more reliable Android experience, prefer Tailscale or a public-domain Let's Encrypt path.

### Firefox (any platform)

Firefox maintains its own cert store — system trust install does **not** apply.

1. **Tools → Settings → Privacy & Security → Certificates → View Certificates**.
2. **Authorities** tab → **Import**.
3. Pick `muxplex-ca.crt`.
4. Tick **Trust this CA to identify websites**, click OK.

## Verification

After installing, fully restart the browser, then visit `https://<host>:<port>/`. You should see a green padlock with no warnings.

You can also verify from the command line on any client:

```sh
# Linux/macOS
curl -v https://<host>:<port>/ 2>&1 | grep -i "verify\|certificate verify"
```

Look for `SSL certificate verify ok.` — if you see `SSL certificate verify failed` (and you're sure you imported the CA into the **right** store and restarted the browser), see the Troubleshooting section.

## Cert rotation

The leaf cert is valid for 397 days, which keeps it under the 398-day public-trust ceiling that some browsers enforce even for privately-installed CAs. To rotate:

```sh
muxplex setup-tls --method ca
muxplex service restart
```

The CA is reused (clients keep trusting it), the leaf is regenerated, and the server picks up the new leaf on restart. **No client-side re-trust is required** — that's the whole point of using a CA structure instead of a bare self-signed cert.

The CA itself is valid for 10 years; you only need to re-deploy a new CA + re-trust on clients once per decade.

## Troubleshooting

**"Browser still shows the warning after I imported the CA."**

- Did you fully quit the browser and reopen it? (Closing the window isn't enough on Chrome/Edge.)
- Are you visiting a URL that's actually in the cert's SAN? Check `muxplex setup-tls --status` to see the SAN list. Common gotcha: the cert covers `my-host` but you're visiting `https://my-host.lan` — `.lan` isn't in the SAN.
- Did you import into the right store?
  - Windows: `CurrentUser\Root` (not `My`/Personal). Verify with `Get-ChildItem Cert:\CurrentUser\Root | Where-Object Subject -like "*muxplex*"`.
  - macOS: System keychain. Verify with `security find-certificate -c "muxplex Local CA"`.
  - Linux: `/usr/local/share/ca-certificates/` then `update-ca-certificates`.
  - Firefox: imported under **Authorities**, not **Your Certificates**.

**"`curl` says `verify ok` but the browser still warns."**

The browser uses a different trust store than the system CLI in some configurations. Most often this is a Firefox issue (separate store) or a stale browser session that hasn't reloaded the cert store.

**"My LAN IP changed and now the cert doesn't cover the new IP."**

The leaf SAN is fixed at generation time. Re-run `muxplex setup-tls --method ca` to regenerate the leaf with the new auto-detected LAN IP, then `muxplex service restart`. The CA stays the same, so clients don't need to re-trust.

**"`muxplex setup-tls --method ca` fails with `cryptography` import errors."**

Make sure you're running muxplex >= 0.5.0 (which introduced the `ca` method). Older versions accept `--method ca` only if it's been backported.

## Security notes

- The CA's private key (`~/.config/muxplex/ca/muxplex-ca.key`) is the secret that lets you mint TLS certs trusted by every device that's installed your CA. **Treat it like an SSH private key.** It's written with mode `0600`. Don't copy it off the host. Don't commit it to a repo.
- Anyone with that key can sign certs for *any* hostname that targets a client trusting your CA. The blast radius is "every device on which you installed this CA." That's much smaller than a public CA's blast radius, but worth understanding.
- The CA cert (`muxplex-ca.crt`) is public information — it goes onto every client device. Sharing it doesn't reduce your security. Sharing the `.key` does.
- The CA's pathlen is set to `0`, meaning it can sign leaf certificates but cannot issue intermediate sub-CAs. This limits its ability to be misused if the key is ever compromised.
- This CA is local to one host. If you run muxplex on multiple hosts and want browser-trusted certs on all of them, each host has its own CA — install all the CAs on each client. There's no harm in trusting multiple muxplex CAs; they're independent.

## Comparison with other methods

| Method | What it generates | Per-client setup | Browser-trusted? |
|---|---|---|---|
| `tailscale` | Real Let's Encrypt cert for `<host>.<tailnet>.ts.net` | Install + sign in to Tailscale | ✅ everywhere |
| `mkcert` | Cert signed by mkcert's local CA, installed into the *host's* trust store automatically | Manually install mkcert's CA into each *other* client | ✅ on the host; manual elsewhere |
| `ca` (this) | Cert signed by a persistent muxplex CA | Manually install muxplex's CA on each client (one-time) | ✅ after install |
| `selfsigned` | Self-issued leaf, no CA structure | None — but the cert must be re-imported on every rotation | ❌ unless re-imported |

The `ca` method's key advantage over `mkcert` for this use case is that mkcert's `-install` step only configures the *host* it runs on. To get clients to trust mkcert certs, you'd still need to copy mkcert's root CA to each client and import it — exactly the same step as `--method ca`, but with mkcert as a dependency. `--method ca` cuts out the mkcert installation requirement and uses the same `cryptography` library that's already a muxplex dependency.

## See also

- [`muxplex setup-tls --help`](../README.md#https--tls-setup) — CLI reference.
- [`muxplex setup-tls --status`](../README.md#https--tls-setup) — inspect the current cert (subject, SAN, expiry, issuer).
- The [HTTPS / TLS section of the README](../README.md#https--tls-setup) — overview of all four cert methods.
