import test from 'node:test';
import assert from 'node:assert/strict';
import { dispatchUserChatMessage } from '../../src/hooks/chatSendCompat.js';

test('dispatchUserChatMessage prefers sendMessage when available', async () => {
  const calls = [];
  const sendMessage = async (payload, options) => {
    calls.push({ method: 'sendMessage', payload, options });
  };
  const append = async (payload, options) => {
    calls.push({ method: 'append', payload, options });
  };

  const requestOptions = { body: { thread_id: 't1' } };
  const method = await dispatchUserChatMessage({ sendMessage, append, text: 'hi there', requestOptions });

  assert.equal(method, 'sendMessage');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'sendMessage');
  assert.equal(calls[0].payload.role, 'user');
  assert.deepEqual(calls[0].options, requestOptions);
});

test('dispatchUserChatMessage falls back to append when sendMessage is unavailable', async () => {
  const calls = [];
  const append = async (payload, options) => {
    calls.push({ method: 'append', payload, options });
  };

  const requestOptions = { body: { thread_id: 't2' } };
  const method = await dispatchUserChatMessage({ sendMessage: undefined, append, text: 'hello', requestOptions });

  assert.equal(method, 'append');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'append');
  assert.deepEqual(calls[0].payload, { role: 'user', content: 'hello' });
  assert.deepEqual(calls[0].options, requestOptions);
});

test('dispatchUserChatMessage throws when neither send API exists', async () => {
  await assert.rejects(
    () => dispatchUserChatMessage({ sendMessage: null, append: null, text: 'x' }),
    /No supported chat send function available/,
  );
});
