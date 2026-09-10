#!/usr/bin/env python3
"""
Deepgram Speak (per-beat) → silence pads → scripts/demo/out/vo.wav

Requires local key file (never commit):
  scripts/demo/.secrets/deepgram.key

Script format: scripts/demo/vo/sha256_walkthrough.txt with ===BEAT NN===
sections, optional [SILENCE:Ns] after each beat, # comments and [SCREEN:…]
stripped before TTS.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
KEY_PATH = ROOT / "scripts" / "demo" / ".secrets" / "deepgram.key"
SCRIPT_PATH = ROOT / "scripts" / "demo" / "vo" / "sha256_walkthrough.txt"
OUT_DIR = ROOT / "scripts" / "demo" / "out"
BEATS_DIR = OUT_DIR / "beats"
OUT_WAV = OUT_DIR / "vo.wav"
TIMING_PATH = OUT_DIR / "beat_timing.json"

MODEL = os.getenv("CHIPVERIFY_DEMO_DG_MODEL", "flux-hannah-en")
SPEED = os.getenv("CHIPVERIFY_DEMO_DG_SPEED", "1")
EXPRESSIVITY = os.getenv("CHIPVERIFY_DEMO_DG_EXPRESSIVITY", "0")
SAMPLE_RATE = int(os.getenv("CHIPVERIFY_DEMO_DG_RATE", "24000"))
# Quiet under-speech music bed. 0 disables.
BGM_LEVEL = float(os.getenv("CHIPVERIFY_DEMO_BGM_LEVEL", "0.12"))
BGM_PATH = Path(
    os.getenv(
        "CHIPVERIFY_DEMO_BGM",
        str(ROOT / "scripts" / "demo" / "assets" / "the_mountain-saas-systems-140452.mp3"),
    )
)

# Flux / Aura-2 Speak (v2 preferred for flux-*).
SPEAK_BASE = os.getenv("CHIPVERIFY_DEMO_DG_SPEAK_URL", "https://api.deepgram.com/v2/speak")

BEAT_RE = re.compile(r"^===BEAT\s+(\d+)===\s*$", re.MULTILINE)
SILENCE_RE = re.compile(r"\[SILENCE:([\d.]+)s?\]", re.IGNORECASE)
SCREEN_RE = re.compile(r"\[SCREEN:[^\]]*\]", re.IGNORECASE)


def parse_beats(raw: str) -> list[dict]:
    parts = BEAT_RE.split(raw)
    # parts: [preamble, id1, body1, id2, body2, ...]
    beats: list[dict] = []
    if len(parts) < 3:
        # Fallback: whole file as one beat
        text, silence = _clean_body(raw)
        if text:
            beats.append({"id": "01", "text": text, "silence_after": silence})
        return beats

    for i in range(1, len(parts), 2):
        beat_id = parts[i].zfill(2)
        body = parts[i + 1] if i + 1 < len(parts) else ""
        text, silence = _clean_body(body)
        if not text:
            continue
        beats.append({"id": beat_id, "text": text, "silence_after": silence})
    return beats


def _clean_body(body: str) -> tuple[str, float]:
    silence = 2.0
    silences = SILENCE_RE.findall(body)
    if silences:
        silence = float(silences[-1])
    lines: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = SCREEN_RE.sub("", s)
        s = SILENCE_RE.sub("", s)
        s = s.strip()
        if s:
            lines.append(s)
    text = " ".join(lines)
    text = re.sub(r"\s+", " ", text).strip()
    return text, silence


def speak_beat(api_key: str, text: str, out_path: Path) -> float:
    """Call Deepgram Speak (Flux v2 or Aura v1); return duration seconds as WAV."""
    if len(text) > 2000:
        raise SystemExit(f"Beat text exceeds 2000 chars ({len(text)}): {out_path.name}")

    params = {
        "model": MODEL,
        "speed": SPEED,
    }
    # Flux v2 supports expressivity; Aura v1 uses encoding=wav.
    if MODEL.startswith("flux-") or "/v2/" in SPEAK_BASE:
        params["expressivity"] = EXPRESSIVITY
        url = SPEAK_BASE
        accept_mp3 = True
    else:
        params.update(
            {
                "encoding": "linear16",
                "container": "wav",
                "sample_rate": str(SAMPLE_RATE),
            }
        )
        url = SPEAK_BASE.replace("/v2/speak", "/v1/speak")
        accept_mp3 = False

    resp = requests.post(
        url,
        params=params,
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        },
        json={"text": text},
        timeout=180,
    )
    if resp.status_code >= 400:
        raise SystemExit(f"Deepgram error {resp.status_code}: {resp.text[:500]}")

    raw = resp.content
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if accept_mp3 or "mpeg" in ctype or "mp3" in ctype or not _looks_like_wav(raw):
        mp3_path = out_path.with_suffix(".mp3")
        mp3_path.write_bytes(raw)
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise SystemExit("ffmpeg required to convert Flux Speak MP3 → WAV")
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(mp3_path),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-sample_fmt",
            "s16",
            str(out_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    else:
        out_path.write_bytes(raw)
        if "wav" not in ctype and not _looks_like_wav(raw):
            print(f"warn: unexpected content-type {ctype!r} for {out_path.name}")
    return _wav_duration(out_path)


def _looks_like_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _wav_duration(path: Path) -> float:
    probe = shutil.which("ffprobe")
    if probe:
        try:
            out = subprocess.run(
                [
                    probe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nk=1:nw=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            val = float((out.stdout or "").strip() or "0")
            if val > 0:
                return val
        except Exception:
            pass
    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return frames / float(rate) if rate else 0.0


def write_silence(path: Path, seconds: float, sample_rate: int = SAMPLE_RATE) -> None:
    n_frames = int(sample_rate * max(0.0, seconds))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)


def mix_bgm_under(vo_wav: Path, level: float = BGM_LEVEL, bgm: Path | None = None) -> None:
    """Lay quiet upbeat instrumental under speech (replaces room-tone noise)."""
    if level <= 0:
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("warn: ffmpeg missing; skip BGM mix")
        return
    bed = bgm or BGM_PATH
    if not bed.is_file():
        # Generate local SaaS-style bed once.
        gen = ROOT / "scripts" / "demo" / "gen_saas_bgm.py"
        if gen.is_file():
            subprocess.run([sys.executable, str(gen)], check=False)
        if not bed.is_file():
            print(f"warn: BGM missing at {bed}; skip mix")
            return
    dur = _wav_duration(vo_wav)
    if dur <= 0:
        return
    mixed = OUT_DIR / "vo_mixed.wav"
    # Loop/trim BGM to VO length, duck volume, sidechain-ish via lower gain under speech.
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(vo_wav),
        "-stream_loop",
        "-1",
        "-i",
        str(bed),
        "-filter_complex",
        (
            f"[1:a]atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,"
            f"volume={level:.3f},lowpass=f=12000[bg];"
            f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            f"volume=1.05[a]"
        ),
        "-map",
        "[a]",
        str(mixed),
    ]
    print("Mixing BGM under VO:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    mixed.replace(vo_wav)


def stitch_with_ffmpeg(segments: list[Path], out_wav: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found on PATH — required to stitch VO beats")
    list_file = BEATS_DIR / "concat.txt"
    lines = []
    for seg in segments:
        # ffmpeg concat demuxer wants forward slashes / escaped quotes
        p = seg.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{p}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(out_wav),
    ]
    print("Stitching:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def silence_from_timing(beat_index: int, default: float) -> float:
    """Script silence wins by default. Timing stretch is opt-in."""
    use_timing = (os.getenv("CHIPVERIFY_DEMO_VO_USE_TIMING") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not use_timing or not TIMING_PATH.is_file():
        return default
    try:
        data = json.loads(TIMING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default
    keys = [
        ("cold_open", "project_selected"),
        ("project_selected", "import_intent"),
        ("import_intent", "upload_rtl"),
        ("upload_rtl", "upload_spec"),
        ("upload_spec", "mm_narrative"),
        ("mm_narrative", "mm_tour"),
        ("mm_tour", "mm_approve"),
        ("mm_approve", "tb_written"),
        ("tb_written", "verify_mode"),
        ("verdict", "end_hold"),
    ]
    if beat_index < 0 or beat_index >= len(keys):
        return default
    a, b = keys[beat_index]
    if a in data and b in data:
        gap = float(data[b]) - float(data[a])
        if gap > default:
            return min(gap * 0.55, default + 20.0)
    return default


def resolve_api_key() -> str:
    for env_name in ("CHIPVERIFY_DEEPGRAM_KEY", "DEEPGRAM_API_KEY"):
        val = (os.getenv(env_name) or "").strip()
        if val:
            return val
    if KEY_PATH.is_file():
        return KEY_PATH.read_text(encoding="utf-8").strip()
    return ""


def main() -> int:
    api_key = resolve_api_key()
    if not api_key:
        print(
            f"Missing Deepgram key.\n"
            f"  • Write one line to {KEY_PATH}\n"
            f"  • Or set CHIPVERIFY_DEEPGRAM_KEY / DEEPGRAM_API_KEY\n"
            "Stopping before VO mix.",
            file=sys.stderr,
        )
        return 2
    if not SCRIPT_PATH.is_file():
        print(f"Missing VO script {SCRIPT_PATH}", file=sys.stderr)
        return 3

    raw = SCRIPT_PATH.read_text(encoding="utf-8")
    if not raw.strip():
        print("Empty script", file=sys.stderr)
        return 4

    # Persist env key locally for later exports (gitignored).
    if not KEY_PATH.is_file():
        KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        KEY_PATH.write_text(api_key + "\n", encoding="utf-8")
        print(f"Wrote key to {KEY_PATH} (gitignored)")

    beats = parse_beats(raw)
    if not beats:
        print("No beats parsed from script", file=sys.stderr)
        return 5

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    BEATS_DIR.mkdir(parents=True, exist_ok=True)

    meta_beats = []
    concat_paths: list[Path] = []
    total = 0.0

    for i, beat in enumerate(beats):
        wav_path = BEATS_DIR / f"beat{beat['id']}.wav"
        print(f"Speak beat {beat['id']} ({len(beat['text'])} chars)…")
        dur = speak_beat(api_key, beat["text"], wav_path)
        concat_paths.append(wav_path)
        total += dur

        silence_s = silence_from_timing(i, float(beat["silence_after"]))
        if silence_s > 0.05:
            sil_path = BEATS_DIR / f"silence{beat['id']}.wav"
            write_silence(sil_path, silence_s)
            concat_paths.append(sil_path)
            total += silence_s

        meta_beats.append(
            {
                "id": beat["id"],
                "chars": len(beat["text"]),
                "speech_s": round(dur, 3),
                "silence_after_s": round(silence_s, 3),
                "text": beat["text"],
            }
        )
        print(f"  speech={dur:.2f}s silence={silence_s:.2f}s")

    stitch_with_ffmpeg(concat_paths, OUT_WAV)
    try:
        mix_bgm_under(OUT_WAV, BGM_LEVEL)
    except Exception as exc:
        print(f"warn: BGM mix failed: {exc}")
    meta = {
        "path": str(OUT_WAV),
        "model": MODEL,
        "speed": SPEED,
        "sample_rate": SAMPLE_RATE,
        "bgm": str(BGM_PATH) if BGM_LEVEL > 0 else None,
        "bgm_level": BGM_LEVEL,
        "total_s": round(_wav_duration(OUT_WAV) or total, 3),
        "beats": meta_beats,
    }
    (OUT_DIR / "vo_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_WAV} (~{meta['total_s']:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
