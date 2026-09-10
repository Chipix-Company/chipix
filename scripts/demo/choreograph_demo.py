#!/usr/bin/env python3
"""
Human-paced Chipix demo beat choreographer (Playwright).

Drives a real user flow: empty project → upload RTL/spec → mental model tour →
IDE code write → Verify once → VERIFICATION COMPLETE.

Env:
  CHIPVERIFY_DEMO_DRY_RUN=1     — no OpenScreen record
  CHIPVERIFY_DEMO_RECORD_SECONDS — record duration (default 200)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "scripts" / "demo" / ".demo_state.json"
OUT_DIR = ROOT / "scripts" / "demo" / "out"
FRONTEND = os.getenv("CHIPVERIFY_DEMO_FRONTEND", "http://127.0.0.1:5173")

OPENSCREEN_CANDIDATES = [
    Path(r"C:\Users\affan\AppData\Local\Programs\Openscreen\Openscreen.exe"),
    Path(r"C:\Users\affan\AppData\Local\Programs\OpenScreen\openscreen.exe"),
]


def find_openscreen() -> Path | None:
    for path in OPENSCREEN_CANDIDATES:
        if path.is_file():
            return path
    which = shutil.which("openscreen") or shutil.which("Openscreen")
    return Path(which) if which else None


def load_state() -> dict:
    if not STATE.is_file():
        raise SystemExit(f"Missing {STATE}; run seed_sha256_project.py first")
    return json.loads(STATE.read_text(encoding="utf-8"))


def human_pause(page, ms: int = 600) -> None:
    # Prefer wall-clock sleep — page.wait_for_timeout can hang if CDP freezes under OpenScreen.
    time.sleep(max(0, ms) / 1000.0)


def type_human(page, locator, text: str, delay_ms: int = 42) -> None:
    locator.click(timeout=15_000)
    locator.fill("")
    locator.type(text, delay=delay_ms)


def mark_shell_tour_skipped(page) -> None:
    """Prevent / end react-joyride shell tour without clicking through it."""
    try:
        page.evaluate(
            """() => {
              const key = 'chipverify.onboarding.shell.v1';
              const prev = (() => { try { return JSON.parse(localStorage.getItem(key) || '{}'); } catch { return {}; } })();
              localStorage.setItem(key, JSON.stringify({
                ...prev,
                skippedAt: new Date().toISOString(),
                updatedAt: new Date().toISOString(),
              }));
            }"""
        )
    except Exception:
        pass


def dismiss_tour(page) -> None:
    """End Joyride: storage flag + optional Skip click + remove portal overlay."""
    mark_shell_tour_skipped(page)
    for label in ("Skip", "Skip tour", "Got it", "Close", "Maybe later", "Don't show again"):
        loc = page.get_by_role("button", name=label)
        try:
            if loc.count():
                loc.first.click(timeout=800, force=True)
                human_pause(page, 200)
                break
        except Exception:
            continue
    try:
        page.evaluate(
            """() => {
              const portal = document.getElementById('react-joyride-portal');
              if (portal) portal.remove();
              document.querySelectorAll('.react-joyride__overlay, .react-joyride__spotlight').forEach((el) => el.remove());
            }"""
        )
    except Exception:
        pass


def dismiss_joyride(page) -> None:
    dismiss_tour(page)


def wait_text(page, needle: str, timeout_ms: int = 90_000) -> None:
    page.get_by_text(needle, exact=False).first.wait_for(state="visible", timeout=timeout_ms)


def open_ide_and_show_tb(page) -> None:
    if page.is_closed():
        print("warn: page closed before IDE beat")
        return

    # Live write peek panel / peekable status chip
    peek = page.locator(".tf-live-write-peek, .tf-composer-status.peekable")
    try:
        if peek.count() and peek.first.is_visible():
            peek.first.click(timeout=2000)
            human_pause(page, 2800)
    except Exception as exc:
        print(f"warn: live-write peek: {exc}")

    try:
        page.locator('[data-tour="side-rail-ide"]').click(timeout=8000)
        human_pause(page, 2800)
    except Exception as exc:
        print(f"warn: open IDE: {exc}")
        dismiss_surfaces(page)
        return

    # Prefer explorer filenames inside the IDE — never click thread markdown.
    for name in ("tb_sha256_unit.sv", "tb_sha256_unit", "sha256_accelerator.sv"):
        if page.is_closed():
            print("warn: page closed during IDE file select")
            return
        try:
            row = page.locator(".tf-ide.open .ename", has_text=name)
            if row.count():
                row.first.click(timeout=4000, force=True)
                # Slow read: page through so code isn't a flash.
                human_pause(page, 4500)
                try:
                    page.keyboard.press("PageDown")
                    human_pause(page, 2200)
                    page.keyboard.press("PageDown")
                    human_pause(page, 2200)
                    page.keyboard.press("Home")
                    human_pause(page, 1800)
                except Exception:
                    human_pause(page, 4000)
                break
        except Exception as exc:
            print(f"warn: IDE file '{name}': {exc}")

    # Hold on code a beat longer before closing.
    human_pause(page, 3500)
    dismiss_surfaces(page)
    # Toggle rail closed if IDE still open
    if _visible(page.locator(".tf-ide.open")):
        try:
            page.locator('[data-tour="side-rail-ide"]').click(timeout=3000)
            human_pause(page, 700)
        except Exception:
            pass
        dismiss_surfaces(page)


def send_prompt(page, text: str) -> None:
    wait_composer_idle(page)
    dismiss_surfaces(page)
    box = composer(page)
    type_human(page, box, text)
    human_pause(page, 400)
    # Prefer visible send control; fall back to Ctrl+Enter
    send_btn = page.locator(
        '[data-tour="composer"] button[type="submit"], '
        '[data-tour="composer"] button[aria-label*="Send" i], '
        '.tf-composer button.send, .tf-composer button[title*="Send" i]'
    )
    try:
        if send_btn.count() and send_btn.first.is_enabled():
            send_btn.first.click(timeout=3000)
        else:
            page.keyboard.press("Control+Enter")
    except Exception:
        page.keyboard.press("Control+Enter")
    human_pause(page, 800)


def wait_any_text(page, needles: list[str], timeout_ms: int = 180_000) -> str:
    deadline = time.time() + timeout_ms / 1000.0
    last_err = None
    while time.time() < deadline:
        if page.is_closed():
            raise TimeoutError("page closed while waiting for text")
        try:
            body = page.locator("body").inner_text(timeout=2000)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            body = ""
        for needle in needles:
            if needle and needle.lower() in body.lower():
                return needle
            loc = page.get_by_text(needle, exact=False)
            try:
                if loc.count():
                    return needle
            except Exception as exc:  # noqa: BLE001
                last_err = exc
        page.wait_for_timeout(500)
    raise TimeoutError(f"None of {needles!r} became visible ({last_err})")


def wait_composer_idle(page, timeout_ms: int = 120_000) -> None:
    """Wait until composer is not in busy state."""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        busy = page.locator(".tf-composer.busy, [data-tour='composer'] .busy")
        try:
            if busy.count() == 0 or not busy.first.is_visible():
                # Also ensure textarea is enabled
                box = composer(page)
                if box.count() and box.is_enabled():
                    return
        except Exception:
            pass
        page.wait_for_timeout(400)
    print("warn: composer idle wait timed out; continuing")


def _visible(locator) -> bool:
    try:
        return bool(locator.count() and locator.first.is_visible())
    except Exception:
        return False


def dismiss_surfaces(page) -> None:
    """Close History drawer, full-screen IDE, and mental-model overlay."""
    for _ in range(6):
        if page.is_closed():
            return
        closed_any = False

        drawer = page.locator(".tf-drawer-overlay.open")
        if _visible(drawer):
            try:
                drawer.first.click(timeout=1500, force=True)
                human_pause(page, 350)
                closed_any = True
            except Exception:
                pass

        ide = page.locator(".tf-ide.open")
        if _visible(ide):
            close_btn = page.locator(
                ".tf-ide.open .tf-overlay-close, "
                ".tf-ide.open header button[aria-label*='Close' i], "
                ".tf-ide.open header button:last-child"
            )
            try:
                if close_btn.count():
                    close_btn.first.click(timeout=2000, force=True)
                else:
                    page.keyboard.press("Escape")
                human_pause(page, 400)
                closed_any = True
            except Exception:
                try:
                    page.keyboard.press("Escape")
                    human_pause(page, 300)
                    closed_any = True
                except Exception:
                    pass

        mm = page.locator(".tf-mm-overlay.open")
        if _visible(mm):
            close_btn = page.locator(".tf-mm-overlay.open .tf-overlay-close")
            try:
                if _visible(close_btn):
                    close_btn.first.click(timeout=2000)
                else:
                    page.keyboard.press("Escape")
                human_pause(page, 400)
                closed_any = True
            except Exception:
                try:
                    page.keyboard.press("Escape")
                    human_pause(page, 300)
                    closed_any = True
                except Exception:
                    pass

        if not closed_any:
            # One Escape pass for any remaining modal, then stop if clear.
            page.keyboard.press("Escape")
            human_pause(page, 250)
            if not (
                _visible(page.locator(".tf-drawer-overlay.open"))
                or _visible(page.locator(".tf-ide.open"))
                or _visible(page.locator(".tf-mm-overlay.open"))
            ):
                return


def click_mm_checkpoint(page) -> None:
    """Approve mental model via in-thread gate before implement."""
    dismiss_surfaces(page)
    for label in ("Looks right — continue", "Looks right"):
        loc = page.get_by_role("button", name=label)
        try:
            if loc.count():
                loc.first.click(timeout=4000)
                human_pause(page, 600)
                return
        except Exception:
            continue
        # Fallback: text match
        try:
            alt = page.get_by_text(label, exact=False)
            if alt.count():
                alt.first.click(timeout=4000)
                human_pause(page, 600)
                return
        except Exception:
            continue
    print("warn: MM checkpoint button not found; will rely on implement prompt")


def pick_staged_strategy(page, strategy_label: str = "Unit simulation") -> None:
    """Click a staged strategy chip (Unit simulation / Formal / All)."""
    dismiss_surfaces(page)
    for label in (strategy_label, "UnitSim", "Unit simulation", "All strategies", "Formal"):
        chip = page.locator(".tf-staged-chip", has_text=label)
        try:
            if chip.count():
                chip.first.click(timeout=8000)
                human_pause(page, 1200)
                print(f"Picked strategy chip: {label}", flush=True)
                return
        except Exception as exc:
            print(f"warn: strategy chip '{label}': {exc}", flush=True)
    # Fallback: any recommended chip
    try:
        reco = page.locator(".tf-staged-chip.reco").first
        if reco.count():
            reco.click(timeout=5000)
            human_pause(page, 1200)
            print("Picked recommended strategy chip", flush=True)
    except Exception as exc:
        print(f"warn: reco chip: {exc}", flush=True)


def click_plan_approve(page) -> None:
    """Click staged plan Approve / Implement."""
    dismiss_surfaces(page)
    for label in ("Implement", "Approve & run", "Approve and run", "Approve", "Run plan"):
        try:
            btn = page.locator(".tf-card.tf-plan .tf-card-actions button", has_text=label)
            if btn.count():
                btn.first.click(timeout=8000)
                human_pause(page, 1000)
                return
        except Exception:
            continue
        try:
            alt = page.get_by_role("button", name=label)
            if alt.count():
                alt.first.click(timeout=8000)
                human_pause(page, 1000)
                return
        except Exception:
            continue
    raise TimeoutError("Plan approve/Implement button not found")


def click_apply_diff(page) -> None:
    """Click DiffCard Apply change."""
    dismiss_surfaces(page)
    for label in ("Apply change", "Apply", "Approve & apply"):
        try:
            btn = page.locator(".tf-diff .tf-btn.primary, .tf-card.tf-diff button", has_text=label)
            if btn.count():
                btn.first.click(timeout=10000)
                human_pause(page, 1200)
                return
        except Exception:
            continue
        try:
            alt = page.get_by_role("button", name=label)
            if alt.count():
                alt.first.click(timeout=10000)
                human_pause(page, 1200)
                return
        except Exception:
            continue
    raise TimeoutError("Diff Apply button not found")


def click_verdict_rerun(page) -> None:
    """Click Re-run on a bad/good verdict if present."""
    try:
        btn = page.locator(".tf-verdict button, .tf-card button", has_text="Re-run")
        if btn.count():
            btn.first.click(timeout=5000)
            human_pause(page, 800)
            return
    except Exception as exc:
        print(f"warn: verdict re-run: {exc}", flush=True)


def composer(page):
    return page.locator(
        '[data-tour="composer"] textarea, [data-tour="composer"] [contenteditable="true"], .tf-composer textarea'
    ).first


def upload_via_hidden_input(page, accept_substr: str, file_path: Path) -> None:
    # Prefer clicking the attach menu so the UI action is visible, then set files.
    paperclip = page.locator('[data-tour="composer"] button[title="Attach files"], .tf-attach-wrap button.tool').first
    paperclip.click(timeout=10_000)
    human_pause(page, 500)

    if "sv" in accept_substr or "rtl" in accept_substr.lower():
        menu_item = page.get_by_role("menuitem", name="Upload RTL file(s)…")
        if not menu_item.count():
            menu_item = page.get_by_text("Upload RTL file(s)", exact=False)
    else:
        menu_item = page.get_by_role("menuitem", name="Upload specification…")
        if not menu_item.count():
            menu_item = page.get_by_text("Upload specification", exact=False)

    # File chooser may open from the menu click — intercept it.
    try:
        with page.expect_file_chooser(timeout=4000) as fc_info:
            menu_item.first.click(timeout=5000)
        chooser = fc_info.value
        chooser.set_files(str(file_path))
    except Exception:
        # Fallback: set on hidden inputs directly (still after menu open attempt).
        if ".sv" in accept_substr:
            inp = page.locator('input[type="file"][accept*=".sv"]').first
        else:
            inp = page.locator('input[type="file"][accept*=".md"]').first
        inp.set_input_files(str(file_path))
    human_pause(page, 1500)


def open_project_modal(page) -> None:
    # Title bar project control often exposes create via menu / palette.
    try:
        page.locator('[data-tour="titlebar-project"]').click(timeout=4000)
        human_pause(page, 400)
        for label in ("New project", "Start a new project", "Create project", "New Project"):
            loc = page.get_by_text(label, exact=False)
            if loc.count():
                loc.first.click(timeout=3000)
                human_pause(page, 500)
                return
    except Exception:
        pass
    # Palette fallback
    page.keyboard.press("Control+k")
    human_pause(page, 400)
    page.keyboard.type("new project", delay=20)
    human_pause(page, 400)
    page.keyboard.press("Enter")
    human_pause(page, 600)


def create_project_ui(page, name: str) -> None:
    open_project_modal(page)
    # Modal input
    name_input = page.locator('input[placeholder*="name" i], input[name="name"], .new-project input, form input').first
    try:
        name_input.wait_for(state="visible", timeout=8000)
        name_input.fill(name)
        human_pause(page, 400)
        submit = page.get_by_role("button", name=lambda n: n and ("create" in n.lower() or "open" in n.lower()))
        if submit.count():
            submit.first.click()
        else:
            page.keyboard.press("Enter")
        human_pause(page, 2000)
    except Exception as exc:
        print(f"create project UI warning: {exc} — will select seeded project if present")


def select_project(page, project_name: str, project_id: str | None = None) -> None:
    """Select the seeded project without hanging the take."""
    if project_id:
        try:
            page.evaluate(
                """([pid]) => {
                  localStorage.setItem('chipverify.desktop.activeProjectId', pid);
                  const key = 'chipverify.onboarding.shell.v1';
                  const prev = (() => { try { return JSON.parse(localStorage.getItem(key) || '{}'); } catch { return {}; } })();
                  localStorage.setItem(key, JSON.stringify({
                    ...prev,
                    skippedAt: new Date().toISOString(),
                    updatedAt: new Date().toISOString(),
                  }));
                }""",
                [project_id],
            )
            page.goto(FRONTEND, wait_until="domcontentloaded", timeout=60_000)
            page.evaluate("() => { document.title = 'ChipVerify'; }")
            human_pause(page, 1000)
            dismiss_tour(page)
            print(f"Selected project via storage: {project_name}", flush=True)
            return
        except Exception as exc:
            print(f"storage project select warning: {exc}", flush=True)

    try:
        page.locator('[data-tour="titlebar-project"]').click(timeout=2500)
        human_pause(page, 300)
        page.get_by_text(project_name, exact=False).first.click(timeout=3000)
        human_pause(page, 800)
    except Exception as exc:
        print(f"select project warning: {exc}", flush=True)
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        human_pause(page, 200)


def click_welcome_import(page) -> None:
    try:
        loc = page.get_by_text("Verify code I already have", exact=False)
        loc.first.click(timeout=3000)
        human_pause(page, 800)
        return
    except Exception:
        pass
    try:
        mode = page.locator('[data-tour="mode-toggle"]')
        mode.get_by_text("Verify", exact=False).first.click(timeout=2500)
        human_pause(page, 400)
    except Exception as exc:
        print(f"welcome import warning: {exc}", flush=True)


def tour_mental_model(page, aspect_dwell_ms: int = 2000) -> None:
    page.locator('[data-tour="side-rail-mental"]').click(timeout=8000)
    human_pause(page, 1500)
    for label in ("Overview", "Modules", "Ports", "Parameters", "Functionalities"):
        if page.is_closed():
            return
        btn = page.locator(".tf-mm-rail-item", has_text=label)
        try:
            if btn.count():
                btn.first.click(timeout=4000)
                human_pause(page, aspect_dwell_ms)
            else:
                alt = page.get_by_role("button", name=label)
                if alt.count():
                    alt.first.click(timeout=3000)
                    human_pause(page, aspect_dwell_ms)
        except Exception as exc:
            print(f"warn: MM rail '{label}': {exc}")
    dismiss_surfaces(page)


def switch_verify(page) -> None:
    dismiss_surfaces(page)
    toggle = page.locator('[data-tour="mode-toggle"]')
    if toggle.count():
        verify_btn = toggle.get_by_text("Verify", exact=False)
        if verify_btn.count():
            try:
                verify_btn.first.click(timeout=5000)
            except Exception:
                dismiss_surfaces(page)
                verify_btn.first.click(timeout=5000, force=True)
            human_pause(page, 700)


def ffprobe_duration(path: Path) -> float:
    probe = shutil.which("ffprobe")
    if not probe or not path.is_file():
        return 0.0
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
        return float((out.stdout or "").strip() or "0")
    except Exception:
        return 0.0


def load_vo_timeline() -> list[dict]:
    """Per-beat start/speech_end/end seconds from vo_meta + ffprobe on beat WAVs."""
    meta_path = OUT_DIR / "vo_meta.json"
    if not meta_path.is_file():
        return []
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    beats = meta.get("beats") or []
    timeline: list[dict] = []
    t = 0.0
    for beat in beats:
        bid = str(beat.get("id") or "").zfill(2)
        wav = OUT_DIR / "beats" / f"beat{bid}.wav"
        speech = ffprobe_duration(wav)
        if speech <= 0:
            speech = float(beat.get("speech_s") or 0)
            if speech > 1000:
                speech = 8.0
        silence = float(beat.get("silence_after_s") or 0)
        timeline.append(
            {
                "id": bid,
                "start": t,
                "speech_end": t + speech,
                "end": t + speech + silence,
                "speech": speech,
                "silence": silence,
            }
        )
        t = t + speech + silence
    return timeline


def sync_to(
    page,
    vo_origin: float | None,
    target_s: float,
    label: str,
    *,
    enabled: bool,
) -> None:
    """Wait until VO timeline clock reaches target_s (only during record)."""
    if not enabled or vo_origin is None:
        return
    wait = target_s - (time.time() - vo_origin)
    if wait > 0.12:
        print(f"sync {label}: +{wait:.1f}s -> t={target_s:.1f}s", flush=True)
        time.sleep(wait)


def run_beats(
    page,
    state: dict,
    record: bool,
    vo_origin: float | None = None,
) -> None:
    prompts = state.get("prompts") or {}
    project_name = state.get("project_name") or "SHA-256"
    project_id = state.get("project_id")
    rtl_path = Path(state["rtl_path"])
    spec_path = Path(state["spec_path"])
    timing: dict[str, float] = {"t0": time.time()}
    timeline = load_vo_timeline() if record else []

    def mark(name: str) -> None:
        timing[name] = round(time.time() - timing["t0"], 2)
        print(f"[beat] {name} @ {timing[name]}s")

    def hold(ms: int) -> None:
        human_pause(page, ms)

    def beat(i: int) -> dict | None:
        return timeline[i] if i < len(timeline) else None

    def sync_beat_start(i: int, label: str) -> None:
        b = beat(i)
        if b:
            sync_to(page, vo_origin, b["start"], label, enabled=record)

    def sync_beat_end(i: int, label: str) -> None:
        b = beat(i)
        if b:
            sync_to(page, vo_origin, b["end"], label, enabled=record)

    # Avoid a second full reload when main() already opened the app.
    if "chipverify" not in (page.title() or "").lower():
        page.goto(FRONTEND, wait_until="domcontentloaded", timeout=120_000)
        page.evaluate("() => { document.title = 'ChipVerify'; }")
    else:
        page.evaluate("() => { document.title = 'ChipVerify'; }")

    sync_beat_start(0, "beat01")
    hold(1800)
    dismiss_tour(page)
    mark("cold_open")

    # Beat 1 — seeded empty project
    print("Selecting project…", flush=True)
    select_project(page, project_name, project_id=project_id)
    print("Post-select dismiss...", flush=True)
    dismiss_joyride(page)
    print("Post-select hold...", flush=True)
    hold(400)
    print("Post-select sync...", flush=True)
    sync_beat_end(0, "beat01-end")
    mark("project_selected")
    print("Project ready - continuing beats", flush=True)

    # Beat 2 — import intent
    sync_beat_start(1, "beat02")
    click_welcome_import(page)
    hold(900)
    sync_beat_end(1, "beat02-end")
    mark("import_intent")

    # Beats 3–4 — uploads
    sync_beat_start(2, "beat03")
    print("Uploading RTL…")
    upload_via_hidden_input(page, ".sv", rtl_path)
    wait_text(page, "sha256", timeout_ms=60_000)
    hold(600)
    sync_beat_end(2, "beat03-end")
    mark("upload_rtl")

    sync_beat_start(3, "beat04")
    print("Uploading spec…")
    upload_via_hidden_input(page, ".md", spec_path)
    hold(600)
    sync_beat_end(3, "beat04-end")
    mark("upload_spec")

    # Beat 5 — mental model
    sync_beat_start(4, "beat05")
    gate = page.get_by_text("Yes — build mental model", exact=False)
    if gate.count():
        gate.first.click(timeout=5000)
        hold(600)
    else:
        send_prompt(
            page,
            prompts.get("mental_model")
            or "Build a mental model from the uploaded SHA-256 spec and RTL.",
        )
    mark("mm_started")

    print("Waiting for mental model narrative…")
    wait_text(page, "how I understand", timeout_ms=120_000)
    hold(1200)
    sync_beat_end(4, "beat05-end")
    mark("mm_narrative")

    # Beat 6 — MM overlay tour (spread across speech)
    sync_beat_start(5, "beat06")
    print("Touring mental model overlay…")
    tour_dwell = 2200
    if beat(5):
        # ~5 aspects into the speech window
        tour_dwell = max(1800, int((beat(5)["speech"] / 5.0) * 1000))
    tour_mental_model(page, aspect_dwell_ms=tour_dwell)
    sync_beat_end(5, "beat06-end")
    mark("mm_tour")

    # Beat 7 — approve
    sync_beat_start(6, "beat07")
    print("Approving mental model checkpoint…")
    click_mm_checkpoint(page)
    wait_composer_idle(page, timeout_ms=60_000)
    hold(500)
    sync_beat_end(6, "beat07-end")
    mark("mm_approve")

    # Beat 8 — implement / IDE
    sync_beat_start(7, "beat08")
    print("Sending implement prompt…")
    send_prompt(
        page,
        prompts.get("implement")
        or "Looks right — continue. Write the directed unit testbench for sha256_accelerator.",
    )
    wait_text(page, "tb_sha256_unit", timeout_ms=120_000)
    hold(800)
    mark("tb_written")
    open_ide_and_show_tb(page)
    wait_composer_idle(page)
    hold(600)
    sync_beat_end(7, "beat08-end")
    mark("ide_shown")

    # Beat 9+ — staged verify: plan → run (fail) → Apply → re-run (pass)
    sync_beat_start(8, "beat09")
    print("Switching to Verify and starting staged plan…", flush=True)
    dismiss_surfaces(page)
    switch_verify(page)
    hold(500)
    mark("verify_mode")
    send_prompt(
        page,
        prompts.get("verify")
        or "Plan and run verification for sha256_accelerator.",
    )

    # Staged prepare often shows a mental-model checkpoint before strategy chips.
    print("Waiting for staged MM checkpoint or strategy chips…", flush=True)
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            if page.locator(".tf-staged-chip").count():
                print("Strategy chips already visible", flush=True)
                break
            btn = page.get_by_role("button", name="Looks right — continue")
            if not btn.count():
                btn = page.get_by_role("button", name="Looks right")
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=5000)
                print("Approved staged mental-model checkpoint", flush=True)
                human_pause(page, 1500)
                # After approve, recommend() should push chips
                continue
            # Prep card CTA
            review = page.get_by_role("button", name="Review my understanding")
            if review.count() and review.first.is_visible():
                review.first.click(timeout=4000)
                human_pause(page, 800)
        except Exception as exc:
            print(f"warn: staged gate loop: {exc}", flush=True)
        human_pause(page, 500)

    print("Waiting for staged strategy chips…", flush=True)
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            if page.locator(".tf-staged-chip").count():
                break
        except Exception:
            pass
        human_pause(page, 500)
    else:
        try:
            body = page.locator("body").inner_text(timeout=3000)
            print("DEBUG: no staged chips. Tail:\n", body[-1500:], flush=True)
        except Exception:
            pass
        raise TimeoutError("Staged strategy chips never appeared")

    hold(800)
    try:
        pick_staged_strategy(page, "Unit simulation")
    except Exception as exc:
        print(f"warn: pick strategy: {exc}", flush=True)
    hold(1200)

    # Wait for plan card actions (plan API can take a bit)
    print("Waiting for plan Implement button…", flush=True)
    deadline = time.time() + 120
    found_plan = False
    while time.time() < deadline:
        try:
            if page.locator(".tf-card.tf-plan .tf-card-actions button").count():
                found_plan = True
                break
            # Also match by action text anywhere in plan card
            if page.locator(".tf-card.tf-plan").get_by_role("button", name="Implement").count():
                found_plan = True
                break
        except Exception:
            pass
        human_pause(page, 600)
    if not found_plan:
        # Debug dump
        try:
            body = page.locator("body").inner_text(timeout=3000)[:2000]
            print("DEBUG body snippet:\n", body, flush=True)
        except Exception:
            pass
        raise TimeoutError("Plan card with Implement never appeared")
    print("Approving plan (Implement)…", flush=True)
    click_plan_approve(page)
    sync_beat_end(8, "beat09-end")
    mark("plan_approved")

    # Beat 10 — dwell on RunCard / failure
    sync_beat_start(9, "beat10")
    print("Waiting for run card / failure…", flush=True)
    wait_any_text(
        page,
        [
            "Verification run",
            "multi_block_padding",
            "UnitSim failed",
            "failed",
            "FAIL",
            "Some checks need attention",
            "Apply change",
            "msg_last",
        ],
        timeout_ms=180_000,
    )
    hold(2500)
    sync_beat_end(9, "beat10-end")
    mark("run_fail")

    # Beat 11 — Apply fix
    sync_beat_start(10, "beat11")
    print("Waiting for DiffCard…", flush=True)
    wait_any_text(page, ["Apply change", "msg_last", "Fix msg_last", "padded block"], timeout_ms=90_000)
    hold(1000)
    print("Applying DiffCard fix…", flush=True)
    click_apply_diff(page)
    sync_beat_end(10, "beat11-end")
    mark("fix_applied")

    # Beat 12 — second run to green
    sync_beat_start(11, "beat12")
    print("Waiting for re-run / second plan…", flush=True)
    hold(1500)
    # Apply triggers "Re-run verification after applying…" → staged begin again
    try:
        wait_any_text(
            page,
            ["UnitSim", "Implement", "What I will implement", "Plan approved", "Verification run"],
            timeout_ms=90_000,
        )
        if page.locator(".tf-staged-chip").count():
            pick_staged_strategy(page, "UnitSim")
            hold(500)
        if page.locator(".tf-card.tf-plan .tf-card-actions button").count():
            print("Approving second plan…", flush=True)
            click_plan_approve(page)
        else:
            # Fallback: click Re-run on verdict or send plan prompt again
            click_verdict_rerun(page)
            if page.locator(".tf-card.tf-plan .tf-card-actions button").count():
                click_plan_approve(page)
            else:
                send_prompt(page, "Plan and run verification for sha256_accelerator.")
                wait_any_text(page, ["Implement", "What I will implement"], timeout_ms=90_000)
                pick_staged_strategy(page, "UnitSim")
                click_plan_approve(page)
    except Exception as exc:
        print(f"warn: second plan path: {exc}", flush=True)
        send_prompt(page, "Plan and run verification for sha256_accelerator.")
        try:
            pick_staged_strategy(page, "UnitSim")
            click_plan_approve(page)
        except Exception as exc2:
            print(f"warn: second approve: {exc2}", flush=True)

    print("Waiting for green verdict / coverage…", flush=True)
    matched = wait_any_text(
        page,
        [
            "VERIFICATION COMPLETE",
            "All checks passed",
            "UnitSim PASS",
            "Formal PROVEN",
            "3/3",
            "coverage",
            "passed",
        ],
        timeout_ms=180_000,
    )
    print(f"Landed on: {matched}", flush=True)
    hold(2000)
    sync_beat_end(11, "beat12-end")
    mark("verdict")

    # Beat 13 — hold on complete
    sync_beat_start(12, "beat13")
    hold(2000)
    sync_beat_end(12, "beat13-end")
    mark("end_hold")

    # Beat 14 — soft CTA outro on VERIFICATION COMPLETE
    sync_beat_start(13, "beat14")
    hold(3500)
    sync_beat_end(13, "beat14-end")
    mark("outro")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "beat_timing.json").write_text(
        json.dumps({"timing": timing, "vo_timeline": timeline}, indent=2),
        encoding="utf-8",
    )
    print("Beats complete.")


def main() -> int:
    # Windows consoles default to cp1252; force utf-8 for progress prints.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    state = load_state()
    dry = (os.getenv("CHIPVERIFY_DEMO_DRY_RUN") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    duration_sec = int(os.getenv("CHIPVERIFY_DEMO_RECORD_SECONDS") or "0")
    if duration_sec <= 0:
        # Default: cover VO length + cold-open buffer
        duration_sec = 200
        vo_probe = OUT_DIR / "vo.wav"
        if vo_probe.is_file() and shutil.which("ffprobe"):
            try:
                out = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "default=nk=1:nw=1",
                        str(vo_probe),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                vo_s = float((out.stdout or "").strip() or "0")
                if vo_s > 0:
                    # End shortly after VO — avoid minutes of dead air after narration.
                    duration_sec = max(int(vo_s) + 12, int(vo_s * 1.02) + 8)
                    duration_sec = min(duration_sec, int(vo_s) + 25)
            except Exception:
                pass
    print(f"OpenScreen record duration: {duration_sec}s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Archive prior takes locally
    old = OUT_DIR / "chipix_sha256_walkthrough.mp4"
    if old.is_file() and not dry:
        awkward = OUT_DIR / "chipix_sha256_walkthrough_awkward_v1.mp4"
        fast = OUT_DIR / "chipix_sha256_walkthrough_fast_v2.mp4"
        try:
            if not awkward.is_file():
                old.rename(awkward)
            elif not fast.is_file():
                old.rename(fast)
            else:
                old.rename(OUT_DIR / f"chipix_sha256_walkthrough_prev_{int(time.time())}.mp4")
        except OSError as exc:
            print(f"warn: could not archive prior mp4 ({exc}); continuing", flush=True)

    project_file = OUT_DIR / "chipix_sha256_demo.openscreen"
    mp4_file = OUT_DIR / "chipix_sha256_walkthrough.mp4"
    openscreen = find_openscreen()

    from playwright.sync_api import sync_playwright

    recorder = None
    vo_origin = None
    os_log_f = None
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--start-maximized", "--window-name=ChipVerify"],
        )
        context = browser.new_context(no_viewport=True)
        page = context.new_page()

        # Cold open before record so OpenScreen can find the window
        page.goto(FRONTEND, wait_until="domcontentloaded", timeout=120_000)
        page.evaluate("() => { document.title = 'ChipVerify'; }")
        human_pause(page, 2000)

        if not dry:
            if not openscreen:
                raise SystemExit("OpenScreen not found")
            sources = subprocess.run(
                [str(openscreen), "sources", "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
            use_window = "ChipVerify" in (sources.stdout or "")
            rec_cmd = [
                str(openscreen),
                "record",
                "--duration",
                str(duration_sec),
                "--project",
                str(project_file),
                "--json",
            ]
            if use_window:
                rec_cmd[2:2] = ["--window", "ChipVerify"]
            else:
                rec_cmd[2:2] = ["--display", "0"]
            print("Starting OpenScreen:", " ".join(rec_cmd))
            os_log = OUT_DIR / "openscreen_record.log"
            os_log_f = open(os_log, "w", encoding="utf-8", errors="replace")
            recorder = subprocess.Popen(
                rec_cmd,
                cwd=str(OUT_DIR),
                stdout=os_log_f,
                stderr=subprocess.STDOUT,
                text=True,
            )
            vo_origin = time.time()
            time.sleep(1.2)

        beats_ok = False
        try:
            run_beats(page, state, record=not dry, vo_origin=vo_origin if not dry else None)
            beats_ok = True
        except Exception as exc:
            # Print immediately — don't hide errors behind OpenScreen wait.
            print(f"Beats failed: {type(exc).__name__}: {exc}", flush=True)
            raise
        finally:
            if recorder is not None:
                try:
                    if beats_ok:
                        recorder.wait(timeout=duration_sec + 40)
                    else:
                        # Don't mask choreography errors behind a full-duration wait.
                        recorder.terminate()
                        try:
                            recorder.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            recorder.kill()
                except subprocess.TimeoutExpired:
                    recorder.kill()
                    print("OpenScreen record timed out; killed")
                if os_log_f is not None:
                    try:
                        os_log_f.close()
                    except Exception:
                        pass
            browser.close()

    if dry:
        print("Dry-run finished (no OpenScreen export).")
        return 0

    if not project_file.is_file():
        print(f"Missing project file {project_file}")
        return 2

    # Intentional zooms at story beats (not late auto-zoom only).
    inject = ROOT / "scripts" / "demo" / "inject_zoom_regions.py"
    if inject.is_file():
        print("Injecting zoom regions…")
        subprocess.run(
            [sys.executable, str(inject), str(project_file), str(OUT_DIR / "beat_timing.json")],
            check=False,
        )

    vo_wav = OUT_DIR / "vo.wav"
    vo_mp3 = OUT_DIR / "vo.mp3"
    export_cmd = [
        str(openscreen),
        "export",
        str(project_file),
        "-o",
        str(mp4_file),
        "--quality",
        "source",
        "--json",
    ]
    vo = vo_wav if vo_wav.is_file() else vo_mp3 if vo_mp3.is_file() else None
    if vo is not None:
        export_cmd.extend(["--audio", str(vo), "--audio-mode", "mix"])
    print("Exporting:", " ".join(export_cmd))
    subprocess.run(export_cmd, check=False)
    print(f"Video: {mp4_file if mp4_file.is_file() else '(missing)'}")
    return 0 if mp4_file.is_file() else 3


if __name__ == "__main__":
    raise SystemExit(main())
