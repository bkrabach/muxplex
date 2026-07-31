"""Self-serve CA install page: platform detection + HTML rendering for GET /setup.

Split out of main.py because this is pure presentation logic with a clean
input/output contract (`detect_platform`, `render_setup_page`) -- neither
function touches the filesystem, network, or FastAPI request/response
objects, so both are trivially unit-testable and regeneratable in isolation.

Security note: `detect_platform` returns ONLY one of a fixed, closed set of
labels (never the raw User-Agent string), and `render_setup_page` never
echoes any request-supplied text into the HTML it returns. There is nothing
here for a hostile User-Agent header to inject into -- the output is fully
determined by two closed-set inputs (`platform`, `ca_available: bool`).
"""

from __future__ import annotations

# Platform labels this module understands. "other" is the fallback for
# anything unrecognized (desktop Linux, unusual browsers, bots, etc.) --
# those users see all four instruction blocks collapsed with no default.
_PLATFORMS = ("android", "ios", "macos", "windows")


def detect_platform(user_agent: str) -> str:
    """Classify a User-Agent header into one of the platforms this page has
    instructions for, or "other" if none match.

    Order matters: iOS devices' UA always includes "Mobile" alongside
    "iPhone"/"iPad"/"iPod", so those are checked before the broader
    "Macintosh" match (iPadOS 13+ *can* present a desktop-class UA
    indistinguishable from real macOS Safari when the user hasn't
    requested the mobile site -- there is no server-side signal that
    resolves this ambiguity; see main.py caller's docstring note).

    Args:
        user_agent: The raw `User-Agent` request header (may be empty).

    Returns:
        One of "android", "ios", "macos", "windows", or "other".

    Example:
        >>> detect_platform("Mozilla/5.0 (Linux; Android 14; Pixel 8)")
        'android'
        >>> detect_platform("")
        'other'
    """
    ua = user_agent or ""
    # Android UAs also contain "Linux", so Android must be checked first.
    if "Android" in ua:
        return "android"
    if "iPhone" in ua or "iPad" in ua or "iPod" in ua:
        return "ios"
    if "Macintosh" in ua or "Mac OS X" in ua:
        return "macos"
    if "Windows" in ua:
        return "windows"
    return "other"


_PLATFORM_LABELS = {
    "android": "Android",
    "ios": "iOS (iPhone / iPad)",
    "macos": "macOS",
    "windows": "Windows",
    "other": "your device",
}

_INSTRUCTIONS = {
    "android": """
      <ol>
        <li>Tap <strong>Download muxplex-ca.crt</strong> above.</li>
        <li>Open <strong>Downloads</strong> (or your notification shade) and
            tap the downloaded <code>muxplex-ca.crt</code> file. If Android
            says <em>"Can't install CA certificates &mdash; this certificate
            must be installed in Settings,"</em> that's expected on some
            versions &mdash; use the manual route below instead.</li>
        <li>When asked what to use it for, Android offers two options.
            Choose <strong>CA certificate</strong>.
            <strong>Do not choose "VPN and app user certificate"</strong>
            &mdash; that option is for a different file format (one bundled
            with a private key) and will fail with <em>"This file can't be
            used as a VPN or app certificate."</em></li>
        <li>You'll see a warning that only mentions installing certificates
            from organizations you trust &mdash; that's expected for any CA
            certificate. Tap <strong>Install anyway</strong>.</li>
        <li><strong>Fully close and reopen your browser</strong> (not just
            reload the page) &mdash; swipe it away from Recent Apps, or
            force-stop it in Settings, then relaunch and come back here.
            You should now see a padlock instead of a warning shield.</li>
      </ol>
      <p class="alt-path">Prefer the manual route? <strong>Settings &rarr;
        Security &amp; privacy &rarr; More security settings &rarr; Encryption
        &amp; credentials &rarr; Install a certificate &rarr; CA
        certificate</strong> (not "VPN and app user certificate"), then pick
        the downloaded file.</p>
    """,
    "ios": """
      <ol>
        <li>Tap <strong>Download muxplex-ca.crt</strong> above. Safari will
            offer to install a configuration profile.</li>
        <li>Open <strong>Settings</strong> &mdash; you'll see "Profile
            Downloaded" near the top. Tap it, then <strong>Install</strong>
            (you'll need your passcode).</li>
        <li><strong>This is the step people miss:</strong> go to
            <strong>Settings &rarr; General &rarr; About &rarr; Certificate
            Trust Settings</strong>, and toggle the muxplex CA to
            <strong>ON</strong> (full trust).</li>
        <li><strong>Fully close and reopen your browser</strong> (not just
            reload the page) &mdash; swipe it away in the App Switcher, or
            confirm it isn't still running in the background, then relaunch
            and come back here. You should now see a padlock instead of a
            warning shield.</li>
      </ol>
    """,
    "macos": """
      <ol>
        <li>Click <strong>Download muxplex-ca.crt</strong> above.</li>
        <li>Double-click the downloaded file &mdash; it opens
            <strong>Keychain Access</strong>.</li>
        <li>Find "muxplex" in the list, double-click it, expand
            <strong>Trust</strong>, and set <strong>When using this
            certificate</strong> to <strong>Always Trust</strong>.</li>
        <li>Enter your password if prompted. Then <strong>fully quit and
            reopen your browser</strong> (not just reload the page)
            &mdash; quit it completely (Cmd+Q, not just closing the
            window), or confirm it isn't still running in the Dock, then
            relaunch and come back here. You should now see a padlock
            instead of a warning shield.</li>
      </ol>
    """,
    "windows": """
      <ol>
        <li>Click <strong>Download muxplex-ca.crt</strong> above.</li>
        <li>Double-click the downloaded file &rarr; <strong>Install
            Certificate&hellip;</strong></li>
        <li>Choose <strong>Local Machine</strong> (needs admin) or
            <strong>Current User</strong>, then <strong>Place all
            certificates in the following store</strong> &rarr;
            <strong>Trusted Root Certification Authorities</strong>.</li>
        <li>Confirm the security warning. Then <strong>fully close and
            reopen your browser</strong> (not just reload the page)
            &mdash; close all windows and confirm it isn't still running in
            the system tray, then relaunch and come back here. You should
            now see a padlock instead of a warning shield.</li>
      </ol>
    """,
}

_STYLE = """
    :root { color-scheme: dark; }
    body {
      margin: 0; padding: 24px 16px 64px; background: #0D1117; color: #E6EDF3;
      font: 16px/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
        sans-serif;
    }
    main { max-width: 560px; margin: 0 auto; }
    h1 { font-size: 1.5rem; margin-bottom: 0.25em; }
    .subtitle { color: #8B949E; margin-top: 0; }
    .download-btn {
      display: inline-block; margin: 20px 0; padding: 14px 24px;
      background: #2F81F7; color: #fff; text-decoration: none;
      border-radius: 8px; font-weight: 600; font-size: 1.05rem;
    }
    .download-btn:active { background: #1F6FEB; }
    .detected {
      background: #161B22; border: 1px solid #30363D; border-radius: 8px;
      padding: 10px 14px; margin-bottom: 20px; font-size: 0.95rem;
    }
    details {
      background: #161B22; border: 1px solid #30363D; border-radius: 8px;
      margin-bottom: 12px; padding: 4px 14px;
    }
    summary {
      cursor: pointer; padding: 12px 0; font-weight: 600; font-size: 1.05rem;
    }
    details ol { padding-left: 1.25em; }
    details li { margin-bottom: 0.6em; }
    .alt-path { color: #8B949E; font-size: 0.9rem; }
    .warning {
      background: #2D1B1B; border: 1px solid #6E2A2A; border-radius: 8px;
      padding: 16px; line-height: 1.6;
    }
    code {
      background: #21262D; padding: 0.1em 0.4em; border-radius: 4px;
      font-size: 0.9em;
    }
"""


def _unavailable_html() -> str:
    """Body content shown when no local CA certificate is configured."""
    return """
    <p class="warning">
      No local CA certificate is configured on this server, so there is
      nothing to download here.
      <br><br>
      This usually means the server is using a different TLS setup
      (e.g. Tailscale certs, mkcert, or a self-signed leaf) rather than
      <code>muxplex setup-tls --method ca</code>. If you're seeing a
      certificate warning in your browser, ask whoever runs this server
      which TLS method is configured.
    </p>
    """


def _available_html(platform: str) -> str:
    """Body content shown when a local CA certificate is available for download."""
    detected_label = _PLATFORM_LABELS[platform]
    if platform == "other":
        detected_note = (
            "We couldn't detect your platform automatically &mdash; "
            "pick your device below."
        )
    else:
        detected_note = f"Detected: <strong>{detected_label}</strong>. That section is open below; tap any other to expand it."

    blocks = []
    for p in _PLATFORMS:
        open_attr = " open" if p == platform else ""
        blocks.append(
            f'<details data-platform="{p}"{open_attr}>'
            f"<summary>{_PLATFORM_LABELS[p]}</summary>"
            f"{_INSTRUCTIONS[p]}"
            f"</details>"
        )

    return f"""
    <a class="download-btn" href="/ca.crt">Download muxplex-ca.crt</a>
    <p class="detected">{detected_note}</p>
    {"".join(blocks)}
    """


def render_setup_page(platform: str, ca_available: bool) -> str:
    """Render the full `/setup` HTML page.

    Args:
        platform: One of the labels `detect_platform` returns ("android",
            "ios", "macos", "windows", "other"). An unrecognized value is
            treated the same as "other" rather than raising.
        ca_available: Whether a local CA certificate is currently available
            to download (mirrors `GET /ca.crt` / `GET /api/ca`'s own
            availability check, done by the caller).

    Returns:
        A complete, self-contained HTML document (inline `<style>`, no
        external stylesheet/script/framework dependency).

    Example:
        >>> html = render_setup_page("android", True)
        >>> "<details data-platform=\\"android\\" open>" in html
        True
    """
    if platform not in _PLATFORMS:
        platform = "other"

    body = _available_html(platform) if ca_available else _unavailable_html()

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>muxplex &mdash; install certificate</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
<h1>Install the muxplex certificate</h1>
<p class="subtitle">One-time setup so your browser trusts this server.</p>
{body}
</main>
</body>
</html>
"""
