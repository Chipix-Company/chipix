# Designing RTL

Chipix can **create RTL from a specification** or **extend and fix** designs you already have. Design work always flows through the thread so you see questions, plans, and files in order.

## Starting a new design

1. Select or create a project.
2. Set **Design** mode in the composer if you are starting from scratch.
3. Describe the block you want (interface, protocol, parameters).
4. Pick **SystemVerilog** or **Verilog** when prompted for a greenfield design.
5. Answer clarifying questions — in-thread **Question** cards or the **clarifier overlay** (one question at a time, **Escape** to dismiss).
6. Review **Plan** cards if shown, then wait for **File** cards.

Typical outputs include RTL sources, specs, and sometimes testbench material depending on what your organization has enabled.

## Importing existing RTL

Choose the **Verify code I already have** starter (or say you have existing RTL). Chipix will offer:

- Upload RTL files
- Upload an RTL folder (keeps directory structure)
- Upload a specification
- Build a mental model, then verify

Use **Attach** in the composer to add specs or RTL to the project or to your next message.

## Iterating on a design

When files already exist:

- **Add a feature** — describe the change; expect a **Diff** card before anything is applied.
- **Start over** — ask for a new design while keeping the same project container.

Always read diff cards carefully; nothing is applied until you choose **Apply**.

## Design phase and verification gates

On some flows, Chipix pauses after design milestones (for example, after a mental-model checkpoint) and asks whether to continue to verification. Respond in the thread — you stay in control of when runs start.

## Tips for good results

- Name the **clock**, **reset style**, and **interfaces** (APB, AXI, custom) up front.
- State **parameters** (width, depth, parity mode) explicitly.
- If you have a written spec, upload it before asking for RTL.
- Open **File** cards to sanity-check ports and parameters before running verification.

See [Running verification](running-verification.md) when the design is ready to check.
