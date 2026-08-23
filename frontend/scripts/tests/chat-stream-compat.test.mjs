import test from 'node:test';
import assert from 'node:assert/strict';
import { __TEST_ONLY__ } from '../../src/hooks/chatStreamCompat.js';

const { mapSseJsonEventToDataLines, normalizeFinishReason, buildStreamFallbackUrl } = __TEST_ONLY__;

test('mapSseJsonEventToDataLines maps text-delta into code 0 line', () => {
  const lines = mapSseJsonEventToDataLines({ type: 'text-delta', delta: 'hello' });
  assert.equal(lines.length, 1);
  assert.match(lines[0], /^0:"hello"\n$/);
});

test('mapSseJsonEventToDataLines maps error into code 3 line', () => {
  const lines = mapSseJsonEventToDataLines({
    type: 'error',
    error: { message: 'bad stream' },
  });
  assert.equal(lines.length, 1);
  assert.match(lines[0], /^3:"bad stream"\n$/);
});

test('mapSseJsonEventToDataLines maps tool-input-available into code 9 line', () => {
  const lines = mapSseJsonEventToDataLines({
    type: 'tool-input-available',
    toolCallId: 'tc1',
    toolName: 'listFiles',
    input: { project_id: 'p1' },
  });
  assert.equal(lines.length, 1);
  assert.match(lines[0], /^9:/);
  const payload = JSON.parse(lines[0].slice(2));
  assert.equal(payload.toolCallId, 'tc1');
  assert.equal(payload.toolName, 'listFiles');
  assert.deepEqual(payload.args, { project_id: 'p1' });
});

test('normalizeFinishReason coerces known and unknown values', () => {
  assert.equal(normalizeFinishReason('tool_calls'), 'tool-calls');
  assert.equal(normalizeFinishReason('length'), 'length');
  assert.equal(normalizeFinishReason('unknown'), 'stop');
});

test('buildStreamFallbackUrl returns fallback URL for non-7348 stream hosts', () => {
  const fallback = buildStreamFallbackUrl('http://127.0.0.1:5173/api/v1/chat/stream');
  assert.equal(fallback, 'http://127.0.0.1:7348/api/v1/chat/stream');
});

test('buildStreamFallbackUrl disables fallback for canonical backend stream URL', () => {
  const fallback = buildStreamFallbackUrl('http://127.0.0.1:7348/api/v1/chat/stream');
  assert.equal(fallback, '');
});
