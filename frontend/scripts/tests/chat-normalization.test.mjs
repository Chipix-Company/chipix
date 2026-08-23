import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeChatMessage } from '../../src/hooks/chatMessageNormalization.js';

test('normalizeMessage preserves content when parts are absent', () => {
  const msg = {
    id: 'm1',
    role: 'user',
    content: 'hi there',
    parts: [],
  };

  const normalized = normalizeChatMessage(msg, []);
  assert.equal(normalized.content, 'hi there');
  assert.equal(normalized.role, 'user');
});

test('normalizeMessage prefers text parts when available', () => {
  const msg = {
    id: 'm2',
    role: 'assistant',
    content: 'fallback',
    parts: [{ type: 'text', text: 'streamed' }],
  };

  const normalized = normalizeChatMessage(msg, []);
  assert.equal(normalized.content, 'streamed');
});

test('normalizeMessage marks pending tool call status correctly', () => {
  const msg = {
    id: 'm3',
    role: 'assistant',
    content: '',
    parts: [{
      type: 'tool-createFile',
      toolCallId: 'tc1',
      state: 'input-available',
      input: { filename: 'a.sv' },
    }],
  };

  const normalized = normalizeChatMessage(msg, [{ toolCallId: 'tc1' }]);
  assert.equal(normalized.toolCalls[0].status, 'awaiting_confirmation');
});
