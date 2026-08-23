# Glossary

Short definitions for terms you will see in Chipix Studio and in verification results.

## Product terms

| Term | Meaning |
| --- | --- |
| **Chipix** | The AI assistant that designs, explains, and verifies hardware in your project. |
| **Thread** | The main conversation timeline of cards and messages. |
| **Project** | Container for RTL files, runs, settings, and one or more conversation threads. |
| **Starter** | Suggested next-step chip at the bottom of the thread (design, verify, dashboard, and so on). |
| **Card** | A structured block in the thread (file, run, verdict, diff, and so on). |
| **Palette** | Command palette (**⌘K**) for quick navigation and actions. |
| **Side rail** | Vertical icon bar on the left — editor, dashboard, task board, and related surfaces. |
| **History panel** | Drawer opened from **Threads** or **Settings** — conversations, runs, files, patches. |
| **Staged verification** | Plan → refine → execute flow before a full run. |
| **Staged flow** | Side-rail entry point for staged verification. |
| **Mental model** | Chipix’s structured understanding of your design (graph overlay). |
| **Clarifier overlay** | Step-by-step questions when Chipix needs more detail before designing. |
| **Live write peek** | Streaming preview of code while Chipix is still writing a file. |
| **Task board** | Kanban view of project tasks by status. |
| **File peek** | Quick single-file editor opened from a thread card. |
| **Overlay** | Full-screen or large panel (dashboard, IDE, docs) over the conversation. |
| **Design / Verify lens** | Composer mode that biases Chipix toward creating RTL or checking it. |

## Hardware and verification

| Term | Meaning |
| --- | --- |
| **RTL** | Register-transfer level design (Verilog, SystemVerilog, VHDL). |
| **DUT** | Device under test — the design being verified. |
| **Testbench** | Code that stimulates the DUT and checks behavior. |
| **Scenario** | One verification case or test configuration in a run. |
| **Assertion / SVA** | SystemVerilog assertions that must hold for all allowed inputs. |
| **Formal verification** | Exhaustive or bounded proofs using a formal tool. |
| **Simulation** | Running tests in a simulator (unit-level or UVM). |
| **Coverage** | Measure of how much of the design or spec was exercised. |
| **Verdict** | Overall pass/fail outcome of a verification run. |
| **Waveform** | Time-domain view of signals shown in the UI. |

## File types you may see

| Extension | Typical role |
| --- | --- |
| `.sv` / `.svh` | SystemVerilog RTL or headers |
| `.v` / `.vh` | Verilog RTL or headers |
| `.md` | Specification or notes |
| `.json` | Project or tool settings |

Your deployment may include additional test or constraint files — open **File** cards to see what was generated for your project.
