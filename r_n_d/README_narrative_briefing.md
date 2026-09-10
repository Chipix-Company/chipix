# Chipix / ChipVerify — README & Product Narrative Briefing

**Purpose:** Planning note for sharpening GitHub README copy, landing-page narrative, and demo scripts for an open-source, AI-native hardware verification SaaS.

**Context snapshot:** Current README (`README.md`) already has strong mission framing (“software tools are free; chip verification tools are not”), a clear three-part architecture, staged verification, and persona blocks. Website hero repeats “world’s first” claims. Demo VO (`scripts/demo/vo/sha256_walkthrough.txt`) already follows problem-first, fail→fix storytelling. This briefing adds external craft references and a recommended structure to tighten credibility without dulling ambition.

---

## Executive summary

Great deep-tech READMEs and demos borrow from **Jobs-style contrast + live proof**, **StoryBrand’s customer-as-hero framing**, and **April Dunford’s category positioning**—not from hype adjectives. For Chipix, the winning narrative is: *the engineer finished RTL and needs proof before tape-out* → *open tools + human-approved AI* → *evidence tied to requirements*. Lead with that job-to-be-done, support “revolutionary” claims with one concrete artifact (SHA-256 walkthrough, green tests, traceability), and reserve superlatives for places you can defend (first **open-source, AI-native, staged verification studio** on named simulators). Avoid AI-slop words the demo script already bans (“landscape,” “delve,” empty “platform”).

---

## 1. How Steve Jobs conveyed products in demos

Jobs treated keynotes as **product stories with proof**, not spec sheets. Five techniques directly applicable to Chipix README + demo video:

### 1.1 Contrast before reveal (villain → hero)

**Technique:** Open on the broken status quo; make the audience feel the pain before showing the product.

| Example | What he did |
|--------|-------------|
| **iPhone (Jan 2007)** | Mocked “smartphones” with fixed keyboards and styluses; positioned them as unusable for normal people. |
| **iPod (2001)** | Compared carrying CDs vs. one device; framed competitors as bulky or limited. |

**Chipix application:** Start README/demo on *“You finished the RTL. Now you need proof it matches the spec—before tape-out.”* (already in demo VO). Pair with a concrete cost contrast: *$10k/seat/year EDA* vs. *open stack on your laptop*—but name tools (Verilator, SymbiYosys), not vague “enterprise lock-in.”

### 1.2 One memorable headline per idea (not a feature list)

**Technique:** Reduce each capability to a phrase a journalist could quote.

| Example | Headline |
|--------|----------|
| **iPod** | “1,000 songs in your pocket.” |
| **iPhone 2007** | “An iPod, a phone, and an internet communicator”—three beats, one device. |
| **MacBook Air (2008)** | Pulled laptop from manila envelope—visual headline. |

**Chipix application:** Pick **one** primary tagline for README hero, e.g. **“Spec to proven—on open tools, with evidence you can audit.”** Subordinate features (mental model, staged UnitSim/Formal/UVM, compile gates) as proof points under that line—not three competing taglines (“world’s first,” “AI-native,” “thread-first”) in the first screen.

### 1.3 Live demo as the argument (show, then explain)

**Technique:** The demo *is* the thesis; narration explains *why* each click matters.

| Example | What he did |
|--------|-------------|
| **iPhone 2007** | Scrolling, pinch-zoom, prank call to Starbucks—capabilities shown on device, not slides. |
| **Mac OS X** | Live app launches; failures were rare because rehearsals were extreme. |

**Chipix application:** README should link **60–90s GIF or video** of SHA-256 flow: ingest spec+RTL → mental model → plan → human approve → run → **named failure → patch → green**. Text supports what the eye already saw. Your staged verification loop is inherently demo-friendly—lead with it above the architecture diagram.

### 1.4 Rule of three (structure memory)

**Technique:** Group capabilities in threes; brains retain triads.

| Example | Three |
|--------|-------|
| **iPhone intro** | Three devices in one. |
| **Many keynotes** | Three apps, three features, three reasons to care. |

**Chipix application:** Three pillars: **Understand** (mental model) → **Plan** (staged strategy, human approval) → **Prove** (sim/formal/UVM + evidence). Maps cleanly to README sections and demo beats.

### 1.5 Pause, simplify, and let moments land

**Technique:** Strip slides to almost nothing; use silence after big lines; hide complexity until needed.

| Example | What he did |
|--------|-------------|
| **“One more thing…”** | Delayed reveal created tension and shareable moments. |
| **Product shots** | Full-bleed product image, minimal text. |

**Chipix application:** Demo VO already uses `[SILENCE]` pads—keep them. README: short paragraphs, one idea each; move repo layout and adapter lists below the fold. Don’t open with GPL, badges, and architecture—open with outcome.

```mermaid
flowchart LR
  A[Status quo pain] --> B[Simple headline]
  B --> C[Live proof demo]
  C --> D[Rule of 3 pillars]
  D --> E[CTA: clone / try demo]
```

---

## 2. Recommended books — core ideas (actionable)

### *Made to Stick* — Chip Heath & Dan Heath (2007)

- **SUCCESs framework:** Simple, Unexpected, Concrete, Credible, Emotional, Stories—score every README paragraph against it.
- **Simple = core + compact:** One priority message; “Commander’s Intent” for the whole page.
- **Unexpected = curiosity gap:** Open with a stat or paradox (software tooling free vs. verification not).
- **Concrete = sensory language:** “SHA-256 block processes in N cycles” beats “AI-powered workflow.”
- **Credible = anti-authority + details:** Named simulators, compile gate logs, trace IDs to requirements.
- **Stories = simulation stories:** Mini before/after (“fail on reset polarity → one-click patch → regression green”).

### *Building a StoryBrand* — Donald Miller (2017)

- **Customer is the hero; brand is the guide**—not “Chipix is revolutionary,” but “You can verify without a seat license.”
- **StoryBrand one-liner:** Character + problem + plan + success + failure to avoid.
- **Villain:** Expensive opaque tooling, expert bottlenecks, tape-out fear—not “legacy EDA” as vague snark.
- **Plan:** 3-step process (ingest → approve plan → run evidence)—mirrors staged verification.
- **Calls to action:** Direct (Download / Clone) and transitional (Try interactive demo, watch 90s walkthrough).
- **Avoid noise:** Cut jargon that doesn’t advance the story; one CTA per screen section.

### *Obviously Awesome* — April Dunford (2019)

- **Positioning is context:** Say what category you’re in so buyers compare you correctly—“open verification studio,” not “ChatGPT for chips.”
- **Competitive alternatives:** Excel + scripts, commercial sim licenses, consulting DV—state honestly.
- **Unique attributes → value:** Only Chipix combines OSS + agent + staged human approval + open sim adapters (if true, prove each).
- **Best-fit customers:** Startups, students, teams self-hosting—already in README; tighten to **primary** ICP first.
- **Trend layer:** AI agents + local LLMs + open EDA momentum—optional fourth paragraph, not the lead.
- **“World’s first” test:** Replace with **defensible niche claim** unless you’ve verified no prior art.

### *Contagious* — Jonah Berger (2013)

- **STEPPS:** Social Currency, Triggers, Emotion, Public, Practical Value, Stories.
- **Social currency:** Make users look smart sharing (“I ran UVM on a laptop with Verilator”).
- **Triggers:** Link to daily engineer pain (“post-RTL,” “pre-tape-out,” “coverage closure”).
- **Emotion:** High-arousal feelings—awe at speed, relief at approval gates—not bland “innovation.”
- **Public:** OSS + GitHub stars + demo GIF = visible artifact others can fork.
- **Practical value:** Quick start in <5 commands; “how to verify SHA-256 in 10 minutes” guide.
- **Stories:** Contagious content is narrative—“the test that failed on beat 9” beats feature bullets.

### *The Presentation Secrets of Steve Jobs* — Carmine Gallo (2009)

- **Answer the one question:** “Why should I care?” in first 30 seconds.
- **Twitter-friendly headlines:** Every section title quotable in ≤140 chars.
- **10-minute rule:** One big idea per ~10 minutes of demo; cut the rest.
- **Demo rituals:** Rehearse transitions; never apologize for the product mid-demo.
- **Visual simplicity:** One visual per idea; README = screenshots/GIFs, not walls of tables up top.
- **Sell the benefit, demo the delight:** Emotional payoff after technical proof (green tests, coverage).

### *Breakthrough Advertising* — Eugene Schwartz (1966)

- **Market awareness stages:** Meet readers where they are (most DV engineers = “problem aware,” not “product aware”).
- **Mass desire channeling:** Don’t create desire for verification—**intensify** existing fear of shipping wrong silicon.
- **Mechanism:** Explain *how* it works differently (mental model → staged plans → compile gates)—the “mechanism” is your credibility engine.
- **Specificity beats hype:** Concrete numbers, named flows, real log excerpts.
- **Headline job:** Select the right reader and promise a believable outcome.

### *Scientific Advertising* — Claude Hopkins (1923)

- **Advertising (copy) is salesmanship in print:** README is a salesperson—every line should earn the next line.
- **Reason-why copy:** Because-statements tied to proof, not adjectives.
- **Test and measure:** Track clone→install→first green test; iterate README CTAs like ad variants.
- **Offer clarity:** What do I get today? Desktop app, demo mode, extension—pick primary offer above fold.
- **No bragging:** Let results speak; “claims invite skepticism.”

### *Pitch Anything* — Oren Klaff (2011)

- **Frame control:** You set the frame (“verification access crisis”), not incumbent vendor frame (“which EDA suite”).
- **Status + intrigue:** Bold mission, then **immediately** substantiate—avoid looking like “another AI wrapper.”
- **Tension and prize:** What’s at stake (tape-out, startup runway); what they win (auditable evidence).
- **Hot cognitions:** Emotion first, logic second—hero video before architecture table.
- **Beta / novelty:** Early OSS = exclusivity of shaping the stack—good for YC/community narrative.

### *Resonate* — Nancy Duarte (2010)

- **Sparkline:** Alternate **what is** vs. **what could be**—README “Why we exist” already does this; extend through whole page.
- **STAR moment:** Something They’ll Always Remember—e.g. named failing test → Apply patch → green in one thread.
- **Contrast creates meaning:** Old way (weeks, licenses) vs. new way (staged agent, open tools)—parallel structure in copy.
- **Call to adventure:** Reader leaves status quo; CTA is the threshold.
- **Presentation = transformation map:** Demo script beats should mirror README section order.

### *On Writing Well* — William Zinsser (1976)

- **Clarity, simplicity, brevity, humanity**—cut every word that doesn’t work.
- **Active verbs:** “Run,” “approve,” “trace”—not “leverages” or “enables.”
- **Avoid jargon clusters:** Define UVM/formal once; link to docs for depth.
- **Read aloud test:** If VO script sounds human but README doesn’t, align them.
- **Unity of tone:** Mission seriousness + engineer plain speech—not marketing brochure.

---

## 3. Principles for “revolutionary” but credible README copy

### Do

| Principle | Practice for Chipix |
|-----------|---------------------|
| **Lead with job-to-be-done** | Engineer needs proof RTL matches spec before tape-out. |
| **Defensible differentiation** | OSS + human-in-loop staged verification + open sim adapters + local LLM—list mechanisms, not moods. |
| **Proof adjacent to claim** | Every bold sentence followed by link: demo video, benchmark dir, sample artifact path. |
| **Name real tools** | Verilator, Icarus, SymbiYosys, Yosys—credibility with DV audience. |
| **Quantify when honest** | Seat costs, time-to-first-test, lines of generated TB—only if measured. |
| **One hero customer story** | SHA-256 accelerator walkthrough as canonical reference design. |
| **Plain speech** | Match demo VO style; short sentences; engineer-to-engineer. |
| **Acknowledge limits** | “Beta,” “Windows/Linux,” “requires bring-your-own LLM key or local GGUF”—trust via honesty. |
| **Parallel structure** | “Understand → Plan → Prove” repeated in hero, demo, and docs. |

### Avoid

| Anti-pattern | Why it hurts |
|--------------|--------------|
| **Unverified “world’s first”** | Invites nitpicking; one counterexample erodes trust. |
| **AI-slop vocabulary** | “Landscape,” “delve,” “holistic,” “revolutionize,” “seamless”—your demo script already flags these. |
| **Feature laundry lists above fold** | Readers bounce before understanding why. |
| **Vague “platform”** | Say what it does in one verb: verify, plan, trace evidence. |
| **Competitor dunking without naming alternatives** | Looks petty; name the alternative workflow you replace. |
| **Security/privacy claims without specifics** | “Local-only” + BYOK + self-host path—or don’t claim. |
| **Three taglines fighting** | Pick one headline; demote others to badges or subheads. |
| **Burying quick start** | OSS readers want clone→run in first scroll on mobile. |
| **Marketing superlatives without mechanism** | “AI-native” must mean agent tools + compile gates, not chat box. |

**Credibility formula:** `Bold claim = specific mechanism + visible artifact + honest boundary`

Example rewrite pattern:

- ❌ “Revolutionary AI-native verification platform.”
- ✅ “Upload spec + RTL → review the agent’s verification plan → run UnitSim/Formal/UVM on Verilator—with every result traced to a requirement.”

---

## 4. Recommended README structure (open-source / YC deep-tech)

Optimized for GitHub skimmers, YC reviewers, and DV engineers evaluating fork risk.

```text
1. Hero (5–8 lines)
   - One-line outcome headline
   - One-line mechanism subhead
   - Badges (license, platform) — minimal
   - Primary CTA: Demo | Download | Quick start (pick ONE primary)

2. Proof strip (visual)
   - 90s demo GIF or link to chipix_sha256_walkthrough.mp4
   - 3 trust chips: Runs locally | Human-approved | Open simulators

3. Why we exist (short)
   - Contrast paragraph (software free vs verification gated)
   - Mission in 2–3 sentences max

4. How it works — Rule of 3
   - Understand (mental model)
   - Plan (staged verification, approval)
   - Prove (adapters, evidence, compile gates)
   - Optional: mermaid diagram

5. What you get today
   - Bullet list of shippable capabilities (not roadmap)
   - “Works with” tool list

6. Who it's for
   - 3 personas × one sentence each (startup / student / team)
   - Link to case study or benchmark

7. Quick start
   - Copy-paste install (≤10 lines)
   - Link to DEVELOPMENT.md for LLM setup

8. Architecture (brief)
   - 3 components: UI / backend / agent_core
   - Table → deeper docs

9. Roadmap teaser (optional, small)
   - 3 next milestones—honest, dated if possible

10. Community & license
    - Contributing, security, AGPL/GPL note
    - Company / YC / contact
```

### Suggested hero copy (draft — paste-ready starting point)

```markdown
# Chipix Studio

**Spec to proven—open-source chip verification with human-approved AI.**

Bring a specification and RTL. Chipix builds a mental model, proposes a verification plan you approve, runs UnitSim/Formal/UVM on open simulators, and records evidence tied to requirements—on your machine, with your models.

[▶ 90s demo](link) · [Try interactive demo](https://chipix.in/?demo=1) · [Quick start ↓](#quick-start)
```

### Narrative alignment checklist

- [ ] README hero matches website hero (one tagline)
- [ ] Demo VO beat 1 mirrors README “Why we exist”
- [ ] Every “first” claim has a footnote or narrower wording
- [ ] SHA-256 project is linked as reference flow
- [ ] `AGENTS.md` linked from architecture, not hero
- [ ] ChipVerify vs Chipix naming note if both appear (product vs runtime)

---

## Key files (Chipix codebase)

| Path | Relevance |
|------|-----------|
| `README.md` | Current GitHub narrative—strong mission, long mid-section |
| `website/index.html` | Landing hero, CTAs, trust strip |
| `scripts/demo/vo/sha256_walkthrough.txt` | Demo storytelling craft notes + beats |
| `scripts/demo/out/chipix_sha256_walkthrough.mp4` | Primary proof asset for README embed |
| `AGENTS.md` | Deep architecture—for secondary README link |
| `DEVELOPMENT.md` | Quick start overflow—keep README lean |
| `backend/demo/fixtures/sha256/` | Canonical demo design + spec |

---

## Open questions / follow-ups

1. **Positioning audit:** Is “world’s first open-source, AI-native chip verification studio” legally/technically defensible? Consider “first open studio combining staged agentic verification with Verilator/SymbiYosys adapters” if needed.
2. **Primary ICP:** Should README optimize for students, YC startups, or enterprise self-hosters first? StoryBrand says pick one hero per page version.
3. **Proof assets:** Embed GIF in README vs. GitHub video vs. YouTube—measure which drives clones.
4. **Naming:** Standardize Chipix vs ChipVerify in public copy (studio vs runtime).
5. **Benchmarks:** Surface `backend/benchmarks/uvm_human_parity/` in README as credibility for “AI-generated UVM” claims.
6. **A/B test:** Try hero variant “Without the $10M ELA” (website) vs. “Spec to proven” (outcome-led)—track demo clicks.

---

## Sources & further reading

- Carmine Gallo, *The Presentation Secrets of Steve Jobs* (2009)
- Chip Heath & Dan Heath, *Made to Stick* (2007)
- Donald Miller, *Building a StoryBrand* (2017)
- April Dunford, *Obviously Awesome* (2019)
- Jonah Berger, *Contagious* (2013)
- Eugene Schwartz, *Breakthrough Advertising* (1966)
- Claude Hopkins, *Scientific Advertising* (1923)
- Oren Klaff, *Pitch Anything* (2011)
- Nancy Duarte, *Resonate* (2010)
- William Zinsser, *On Writing Well* (1976)
- Apple keynotes: iPhone (2007), iPod (2001), MacBook Air (2008)—widely analyzed; Gallo and Duarte both deconstruct them.

---

*Generated for Chipix planning — Sep 2026.*
