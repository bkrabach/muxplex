# muxplex

A web-based dashboard for tmux sessions. Access and manage all your tmux sessions from any device — browser, phone, tablet.

## Features
- Live tile grid showing all running tmux sessions  
- Click any session to open a full interactive terminal
- Bell/activity notifications across sessions
- Multi-device support with state sync
- Mobile-friendly, responsive, PWA-capable
- Works over Tailscale private network

## Stack
- **Backend:** Python FastAPI coordinator (`coordinator/`)
- **Frontend:** Vanilla JS + xterm.js (`frontend/`)
- **Terminal:** ttyd (WebSocket bridge to tmux)
- **Proxy:** Caddy

## Running

```bash
# Install dependencies (runtime + dev)
pip install -e ".[dev]"

# Start coordinator (from this directory)
python -m uvicorn coordinator.main:app --host 0.0.0.0 --port 8099

# Start Caddy proxy
caddy run --config Caddyfile
```

## Development

```bash
# Run tests
python -m pytest
```

## Brand assets

Design language, tokens, and brand assets in `assets/branding/`.
To regenerate PNG/favicon assets from SVG sources:
```bash
python3 scripts/render-brand-assets.py
```
