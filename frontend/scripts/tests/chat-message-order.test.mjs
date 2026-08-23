import assert from "node:assert/strict";
import { sortChatMessagesChronologically } from "../../src/components/thread-first/chatMessageOrder.js";

const sameTime = "2026-05-18T12:00:00.000Z";
const rows = [
  { id: "z-assistant", role: "assistant", content: "Hello", created_at: sameTime },
  { id: "a-user", role: "user", content: "hi", created_at: sameTime },
];

const sorted = sortChatMessagesChronologically(rows);
assert.equal(sorted[0].role, "user");
assert.equal(sorted[1].role, "assistant");
console.log("chat-message-order.test.mjs: ok");
