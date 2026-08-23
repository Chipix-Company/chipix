const COLORS = ["#60a5fa", "#34d399", "#fbbf24", "#a78bfa", "#f472b6", "#94a3b8"];
let lastBurstAt = 0;

function createCanvas() {
  const canvas = document.createElement("canvas");
  canvas.className = "update-confetti-canvas";
  canvas.setAttribute("aria-hidden", "true");
  canvas.style.cssText = [
    "position:fixed",
    "inset:0",
    "width:100%",
    "height:100%",
    "pointer-events:none",
    "z-index:9999",
  ].join(";");
  document.body.appendChild(canvas);
  return canvas;
}

function burst(intensity = "small", originX = 0.5, originY = 0.4) {
  if (typeof window === "undefined" || typeof document === "undefined") return;

  const now = Date.now();
  if (now - lastBurstAt < 700) return;
  lastBurstAt = now;

  const canvas = createCanvas();
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    canvas.remove();
    return;
  }

  const dpr = window.devicePixelRatio || 1;
  const resize = () => {
    canvas.width = window.innerWidth * dpr;
    canvas.height = window.innerHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };
  resize();

  const count = intensity === "medium" ? 56 : 36;
  const spread = intensity === "medium" ? 260 : 180;
  const originPx = {
    x: window.innerWidth * originX,
    y: window.innerHeight * originY,
  };

  const particles = Array.from({ length: count }, () => {
    const angle = (Math.random() * spread - spread / 2) * (Math.PI / 180) - Math.PI / 2;
    const speed = 2.5 + Math.random() * 4.5;
    return {
      x: originPx.x,
      y: originPx.y,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed - 1.5,
      w: 4 + Math.random() * 4,
      h: 2 + Math.random() * 3,
      rot: Math.random() * Math.PI,
      vr: (Math.random() - 0.5) * 0.2,
      color: COLORS[Math.floor(Math.random() * COLORS.length)],
      life: 1,
      decay: 0.012 + Math.random() * 0.01,
    };
  });

  let frameId = 0;
  const start = performance.now();
  const maxMs = intensity === "medium" ? 1400 : 1100;

  const tick = (now) => {
    ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    let alive = 0;

    for (const p of particles) {
      if (p.life <= 0) continue;
      alive += 1;
      p.vy += 0.12;
      p.x += p.vx;
      p.y += p.vy;
      p.rot += p.vr;
      p.life -= p.decay;

      ctx.save();
      ctx.globalAlpha = Math.max(0, p.life);
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
      ctx.restore();
    }

    if (alive > 0 && now - start < maxMs) {
      frameId = window.requestAnimationFrame(tick);
      return;
    }

    window.cancelAnimationFrame(frameId);
    canvas.remove();
  };

  frameId = window.requestAnimationFrame(tick);
}

export function fireUpdateReadyConfetti() {
  burst("medium", 0.5, 0.38);
}

export function fireUpToDateConfetti() {
  burst("small", 0.06, 0.92);
}

export function maybeCelebrateUpdate(previous, next) {
  if (!next || !previous) return;

  const becameReady = next.status === "ready"
    && next.updateDownloaded
    && !(previous.status === "ready" && previous.updateDownloaded);

  const becameUpToDate = Boolean(next.upToDate) && !previous.upToDate;

  const becameCurrent = !next.updateAvailable
    && next.remoteVersion
    && next.currentVersion === next.remoteVersion
    && previous.status !== "ready"
    && (previous.status === "checking-remote" || previous.status === "checking" || previous.loading)
    && next.status === "idle";

  if (becameReady) {
    fireUpdateReadyConfetti();
    return;
  }

  if (becameUpToDate || becameCurrent) {
    fireUpToDateConfetti();
  }
}
