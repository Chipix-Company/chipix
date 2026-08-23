/**
 * useChipixSound — the app's single source of truth for sound.
 *
 * Philosophy: sounds are felt, not heard. A toggle "tick" should feel like
 * the latch of a quality switch, not like an alert. Every sample is a tiny
 * synthesised envelope (sine + light triangle harmonic) under 120ms with a
 * soft attack so it never punches. Master volume is intentionally low
 * (≈0.06 peak) so the design holds up against speakers, headphones, and
 * laptops without surprising anyone.
 *
 * AudioContext is lazy: browsers refuse to instantiate it before a user
 * gesture, so we create it on the first play call and warm-resume it if
 * it ever falls into the `suspended` state.
 *
 * Sound respects `chipverify.soundEnabled` in localStorage (default true).
 * Subscribers can listen via `useChipixSoundEnabled()` for UI mirroring.
 */

const STORAGE_KEY = "chipverify.soundEnabled";
const MASTER_GAIN = 0.06;

let ctx = null;
let masterGainNode = null;
let enabled = readEnabled();
const listeners = new Set();

function readEnabled() {
  if (typeof window === "undefined") return true;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw == null) return true;
    return raw !== "false";
  } catch {
    return true;
  }
}

function writeEnabled(value) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, value ? "true" : "false");
  } catch {
    // ignore
  }
}

function ensureContext() {
  if (typeof window === "undefined") return null;
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try {
      ctx = new AC();
      masterGainNode = ctx.createGain();
      masterGainNode.gain.value = MASTER_GAIN;
      masterGainNode.connect(ctx.destination);
    } catch {
      ctx = null;
      return null;
    }
  }
  if (ctx.state === "suspended") {
    try { ctx.resume(); } catch { /* ignore */ }
  }
  return ctx;
}

function notify() {
  listeners.forEach((cb) => {
    try { cb(enabled); } catch { /* ignore */ }
  });
}

export function setSoundEnabled(value) {
  const next = Boolean(value);
  if (enabled === next) return;
  enabled = next;
  writeEnabled(next);
  notify();
}

export function isSoundEnabled() { return enabled; }

export function subscribeSoundEnabled(cb) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

/**
 * One-shot tone with an exponential decay. Used as the building block for
 * every sound the app produces.
 */
function playTone({
  freq = 880,
  duration = 0.08,
  type = "sine",
  attack = 0.005,
  peakGain = 1.0,
  detuneCents = 0,
  glideTo = null,
}) {
  if (!enabled) return;
  const audio = ensureContext();
  if (!audio) return;

  const now = audio.currentTime;
  const osc = audio.createOscillator();
  const gain = audio.createGain();

  osc.type = type;
  osc.frequency.setValueAtTime(freq, now);
  if (glideTo) {
    osc.frequency.exponentialRampToValueAtTime(glideTo, now + duration * 0.9);
  }
  if (detuneCents) osc.detune.setValueAtTime(detuneCents, now);

  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(peakGain, now + attack);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);

  osc.connect(gain).connect(masterGainNode);
  osc.start(now);
  osc.stop(now + duration + 0.02);
}

/**
 * playTick — the latch click of a high-end switch. Short, dense, satisfying.
 * Used by the mode toggle and any other binary surface.
 *
 * Two-layer composite: a high sine spike for the "click", a sub-harmonic
 * triangle for the "body". The body lands ~12ms after the click — the same
 * trick used in synth percussion to feel weighty.
 */
export function playTick({ pitch = "high" } = {}) {
  const base = pitch === "low" ? 520 : 880;
  playTone({ freq: base * 1.6, duration: 0.045, type: "sine", peakGain: 0.55, attack: 0.002 });
  setTimeout(() => {
    playTone({ freq: base, duration: 0.09, type: "triangle", peakGain: 0.35, attack: 0.004 });
  }, 12);
}

/**
 * playSwoosh — for "sent" / "submitted" actions. A short downward glide
 * that says "off it goes". Used on composer send.
 */
export function playSwoosh() {
  playTone({
    freq: 720,
    glideTo: 360,
    duration: 0.18,
    type: "sine",
    peakGain: 0.45,
    attack: 0.008,
  });
}

/**
 * playChime — success / verdict pass. Two-note major-third ping, gentle.
 */
export function playChime() {
  playTone({ freq: 880, duration: 0.22, type: "sine", peakGain: 0.5, attack: 0.006 });
  setTimeout(() => {
    playTone({ freq: 1108, duration: 0.32, type: "sine", peakGain: 0.4, attack: 0.008 });
  }, 90);
}

/**
 * playThud — failure / blocked. A low, padded note. Not a buzzer, not a
 * red-alert — just a "the floor is here".
 */
export function playThud() {
  playTone({
    freq: 220,
    glideTo: 150,
    duration: 0.22,
    type: "triangle",
    peakGain: 0.55,
    attack: 0.012,
  });
}

/**
 * playHover — for important affordances that benefit from a felt
 * acknowledgement on focus. Used sparingly (mode toggle hover only).
 */
export function playHover() {
  playTone({ freq: 1320, duration: 0.025, type: "sine", peakGain: 0.18, attack: 0.001 });
}

/**
 * Tiny haptic — if the device exposes the Vibration API, give a 6ms tick.
 * No-op everywhere else.
 */
export function pulseHaptic(ms = 6) {
  if (typeof navigator === "undefined" || !navigator.vibrate) return;
  try { navigator.vibrate(ms); } catch { /* ignore */ }
}
