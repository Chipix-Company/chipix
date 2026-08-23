import test from "node:test";
import assert from "node:assert/strict";
import {
  TASK_COLUMNS,
  formatTaskDisplayId,
  groupTasksByColumn,
  taskStatusLabel,
} from "../../src/components/thread-first/taskBoardModel.js";

test("formatTaskDisplayId uses display_number", () => {
  assert.equal(formatTaskDisplayId({ display_number: 42 }), "T-0042");
  assert.equal(formatTaskDisplayId({ display_id: "T-0007" }), "T-0007");
});

test("groupTasksByColumn buckets by status", () => {
  const grouped = groupTasksByColumn([
    { id: "1", status: "todo", title: "A" },
    { id: "2", status: "in_progress", title: "B" },
    { id: "3", status: "completed", title: "C" },
    { id: "4", status: "blocked", title: "D" },
  ]);
  assert.equal(grouped.todo.length, 2);
  assert.equal(grouped.in_progress.length, 1);
  assert.equal(grouped.completed.length, 1);
  assert.equal(grouped.todo.some((t) => t.id === "4"), true);
});

test("taskStatusLabel maps verification states", () => {
  assert.equal(taskStatusLabel("in_verification"), "In verification");
  assert.equal(TASK_COLUMNS.length, 4);
});
