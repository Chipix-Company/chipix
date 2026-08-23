# Chipix marketing site

Static landing page with an **embedded interactive product preview** that reuses the thread-first UI mock and `thread-first.css` from the desktop app.

## Local preview

From the repo root:

```bash
# Python
cd website && python -m http.server 8080

# or npx
cd website && npx serve -p 8080
```

Open [http://localhost:8080](http://localhost:8080).

The demo iframe loads `website/demo/index.html`, which reuses the thread-first UI mock plus the shipped side rail and Design/Verify toggle.

## Structure

| Path | Purpose |
|------|---------|
| `index.html` | Marketing copy aligned with seed pitch + thread-first product |
| `css/landing.css` | Landing layout (Cursor-style demo stage) |
| `demo/index.html` | Full interactive Chipix workspace (simulated backend) |
| `demo/embed.css` | Iframe + side-rail shell bridge |

## Copy source of truth

- Product UI: `frontend/src/components/thread-first/`
