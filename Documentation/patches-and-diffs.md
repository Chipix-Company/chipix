# Patches and diffs

Chipix never silently overwrites your RTL. Changes go through a **propose → review → apply** flow using **Diff** cards in the thread.

## Reading a diff card

Each card usually includes:

- **File name** and short rationale
- **Red/green** (or side-by-side) line-level changes — toggle view if both are offered
- Actions such as **Apply**, **Open file to edit**, and **Reject**

## Apply

**Apply** writes the change to the project and may trigger a follow-up question (“Re-verify?”). Say yes if you want a fresh verification run on the new code.

## Reject

**Reject** dismisses the proposal. You can optionally give a reason. Tell Chipix what you wanted instead in the composer.

## Open file to edit

Use this when the idea is right but the wording or style is wrong. Edit manually, save (**⌘S** / **Ctrl+S**), then re-verify.

## Patch inbox

Pending patches also appear in the **History** panel under **Patches**. Open a patch from there to review the same diff in the thread or editor context.

## Multiple pending patches

Review one diff at a time. Applying or rejecting a patch updates the project; finish or dismiss pending items before assuming all proposed changes are live.

## Editing after apply

Applied changes update **File** cards and the IDE workspace. You can still edit sources manually; the thread records your iteration with section **break** labels when you move to a new chapter after a completed run.

## Safety habit

If a diff touches interfaces, clocking, or reset logic, open the full file once before apply — diff cards show hunks, not the entire module.
