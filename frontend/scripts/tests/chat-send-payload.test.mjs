import test from 'node:test';
import assert from 'node:assert/strict';
import { createUserChatMessagePayload } from '../../src/hooks/chatSendPayload.js';

test('createUserChatMessagePayload builds an explicit user UIMessage payload', () => {
  const payload = createUserChatMessagePayload('hi there');

  assert.equal(payload.role, 'user');
  assert.deepEqual(payload.parts, [{ type: 'text', text: 'hi there' }]);
});

test('createUserChatMessagePayload trims leading/trailing whitespace', () => {
  const payload = createUserChatMessagePayload('   hello world   ');
  assert.equal(payload.parts[0].text, 'hello world');
});
