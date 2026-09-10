# README Humanization Checklist (Chipix)

**Summary:** AI copy leans on symmetrical lists, vague superlatives, and hedge phrases. Engineer docs name real tools, admit limits, and vary rhythm. Use this when rewriting Chipix README: cut banned words, swap abstractions for demo artifacts, test every claim against 30 seconds of screen recording.

---

## 1. Banned / suspicious patterns

**Words:** delve, landscape, robust, seamless, leverage, empower, unlock, cutting-edge, game-changing, revolutionary, holistic, comprehensive, innovative, next-generation, ecosystem, journey, paradigm, synergy, elevate, streamline, harness, foster, navigate.

**Structures:** em-dash overload; "Whether you're a…"; triads ("fast, reliable, scalable"); stacked rhetorical questions; "In today's world…"; "It's not just X, it's Y"; passive chains ("is designed to enable").

**Cadence:** uniform 12–18 word sentences; every bullet = verb + "-ing" noun; adjective-only headers; closers like "We're excited to…" or "Join us on this journey."

---

## 2. Human engineer docs

- Direct, slightly opinionated; say "experimental" or "not yet."
- Mix one-liners with longer explanations.
- Name tools (Verilator, SymbiYosys), files (.sv), failures (`msg_last`), real commands.
- Proof = screenshots, log lines, version pins—not adjectives.
- "We" for team choices; "you" for workflow; skip "users" and "stakeholders."

---

## 3. Chipix README rules

1. Lead with job: RTL done → proof before tape-out—not "AI-native platform."
2. One headline ("Spec to proven"); demote "world's first" unless footnoted.
3. Name stack: UnitSim / Formal / UVM + Verilator / Icarus / Yosys.
4. Show loop: ingest → approve → run → named failure → patch → re-run → coverage.
5. Split "ships today" from roadmap; no future tense in today's list.
6. Always mention human approval before execute.
7. Break bullet symmetry—rewrite one item as prose.
8. Read aloud: keynote = cut; Slack-to-DV-engineer = ship.

---

## 4. Before → after

| Before | After |
|--------|-------|
| Chipix seamlessly leverages AI to revolutionize your verification workflow. | You finished the RTL. Chipix helps you prove it matches the spec—before tape-out. |
| Our platform empowers teams to unlock robust coverage insights. | Run UnitSim on Verilator. Failures and coverage tie back to requirements. |
| Whether startup or enterprise, Chipix streamlines the verification journey. | Two-person team? No $10k/seat license. Self-host on your machine. |
| Cutting-edge mental models enable holistic design understanding. | Chipix reads spec + RTL, builds a mental model, waits for your approval. |
| We're excited to announce next-gen AI-native chip verification. | SHA-256 demo: `msg_last` fails on padding. Apply patch. Re-run. Green. |

---

**Open:** Which superlatives are defensible? Lint banned words in CI on `README.md`?
