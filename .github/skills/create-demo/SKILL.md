---
name: create-demo
description: >-
  Use when recording or rebuilding a ChipiX product demo video with OpenScreen,
  demo-mode fixtures, and optional Deepgram narration.
---
# Create ChipiX demo video

## Goal
Ship a **working-software walkthrough** of ChipiX (not a slideshow): cold-open the real UI, drive SHA-256 (not fifo), show mocked-but-visible AI (mental model, streaming code write, verification), export with OpenScreen auto-zoom and optional Deepgram VO.

## Beats on camera
1. Cold open ChipVerify/ChipiX
2. Import SHA-256 RTL
3. Spec
4. Mental model (editable, on screen)
5. Implementation with live code writing
6. Verification UnitSim + Formal
7. Hold VERIFICATION COMPLETE / scores
Do not ship a take that ends before UnitSim/Formal evidence.

## Demo mode
```bash
export CHIPVERIFY_DEMO_MODE=true
export CHIPVERIFY_DEMO_RELAX_MENTAL_MODEL_GATES=true
export CHIPVERIFY_ALLOW_DEV_AUTH=true
```
Fixtures: `backend/demo/fixtures/sha256/`. Start backend `:7348` + frontend `:5173`. Fix `LICENSE_MISSING` via demo-mode license short-circuit or desktop activation bypass — never commit secrets.

## OpenScreen
https://github.com/getopenscreen/openscreen
```bash
openscreen sources --json
openscreen record --window "ChipVerify" --duration 120 --project demo.openscreen --json
openscreen export demo.openscreen -o walkthrough.mp4 --auto-zoom --quality source --json
```
Auto-zoom import, mental model, code write, evidence. Prefer OpenScreen over raw x11grab when display/GTK works.

## Deepgram VO
Script to beats → Speak API → mix with `--audio` on export. Keep API key in a local secrets file only; never commit or paste into chat.

## Ship gate
videoReview (or equivalent) must confirm full inflow + hard land on UnitSim PASS + Formal PROVEN.

Repo skill mirror: Chipix `.github/skills/create-demo/SKILL.md`.
