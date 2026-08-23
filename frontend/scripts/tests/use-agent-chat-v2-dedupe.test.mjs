import test from 'node:test';
import assert from 'node:assert/strict';
import { shouldProcessToolCall } from '../../src/hooks/toolCallDedupe.js';

test('shouldProcessToolCall executes only once per toolCallId', () => {
  const handled = new Set();

  assert.equal(shouldProcessToolCall(handled, 'tc_list_1'), true);
  assert.equal(shouldProcessToolCall(handled, 'tc_list_1'), false);
  assert.equal(shouldProcessToolCall(handled, 'tc_list_1'), false);

  assert.equal(handled.has('tc_list_1'), true);
  assert.equal(handled.size, 1);
});

test('shouldProcessToolCall allows different toolCallIds', () => {
  const handled = new Set();

  assert.equal(shouldProcessToolCall(handled, 'tc_list_1'), true);
  assert.equal(shouldProcessToolCall(handled, 'tc_list_2'), true);
  assert.equal(shouldProcessToolCall(handled, 'tc_list_3'), true);

  assert.equal(handled.size, 3);
});

test('shouldProcessToolCall keeps set bounded', () => {
  const handled = new Set();
  const maxEntries = 3;

  assert.equal(shouldProcessToolCall(handled, 'tc_a', maxEntries), true);
  assert.equal(shouldProcessToolCall(handled, 'tc_b', maxEntries), true);
  assert.equal(shouldProcessToolCall(handled, 'tc_c', maxEntries), true);
  assert.equal(shouldProcessToolCall(handled, 'tc_d', maxEntries), true);

  // Once max is exceeded, cache is reset to only the most recent call id.
  assert.deepEqual([...handled], ['tc_d']);
});
