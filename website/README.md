# Chipix marketing site

Static landing page with an **embedded product teaser** that opens a **full-screen interactive demo** on click. The demo reuses the thread-first UI mock and `thread-first.css` from the desktop app.

## Local preview

From the repo root:

```bash
# Python
cd website && python -m http.server 8080

# or npx
cd website && npx serve -p 8080
```

Open [http://localhost:8080](http://localhost:8080) (landing) or [http://localhost:8080/mission.html](http://localhost:8080/mission.html) (Mission).

Auto-open fullscreen demo: [http://localhost:8080/?demo=1](http://localhost:8080/?demo=1).

## Demo UX

- **Landing teaser:** live iframe under `#demo` is visible but non-interactive (hit layer on top).
- **Scroll / composer-zone click:** short nudge animation + hint to open full demo.
- **Click teaser or “Try demo”:** expands into a viewport overlay (`demo/?fs=1`) with Back / Esc / Reset.
- **Standalone** `/demo/`: full page with “Back to Chipix” when not embedded.

## Structure

| Path | Purpose |
|------|---------|
| `index.html` | Marketing landing: OSS hero, people trust strip, demo teaser + FS overlay |
| `mission.html` | Mission / open-ecosystem narrative |
| `css/landing.css` | Landing + mission + demo overlay layout |
| `js/demo-expand.js` | Teaser hit-layer, nudge, fullscreen open/close |
| `demo/index.html` | Full interactive Chipix workspace (simulated backend) |
| `demo/thread-first.css` | Copied from `frontend/.../thread-first.css` (keep in sync) |
| `demo/embed.css` | Iframe + side-rail shell bridge; `?fs=1` fullscreen tweaks |

## Copy source of truth

- Product UI: `frontend/src/components/thread-first/`
