import test from 'node:test';
import assert from 'node:assert/strict';
import { ToolExecutor } from '../../src/services/toolExecutor.js';

test('ToolExecutor.execute sends auth header and parses JSON success', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, 'http://localhost:7348/api/v1/tools/listFiles');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers.Authorization, 'Bearer token-123');
    assert.deepEqual(JSON.parse(options.body), { project_id: 'p1' });
    return {
      ok: true,
      async json() {
        return { success: true, files: [{ id: 'a1' }] };
      },
    };
  };

  try {
    const executor = new ToolExecutor('http://localhost:7348', 'token-123');
    const result = await executor.listFiles('p1');
    assert.equal(result.success, true);
    assert.deepEqual(result.data.files, [{ id: 'a1' }]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('ToolExecutor returns error payload when HTTP request fails', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: false,
    status: 404,
    async text() {
      return 'not found';
    },
  });

  try {
    const executor = new ToolExecutor('http://localhost:7348', null);
    const result = await executor.readFile('missing-id');
    assert.equal(result.success, false);
    assert.equal(result.error, 'not found');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('ToolExecutor returns network error when fetch throws', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new Error('socket closed');
  };

  try {
    const executor = new ToolExecutor('http://localhost:7348', null);
    const result = await executor.createFile('p1', 'a.sv', 'rtl', 'module a; endmodule');
    assert.equal(result.success, false);
    assert.match(result.error, /socket closed/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
