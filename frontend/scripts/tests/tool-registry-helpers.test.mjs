import test from 'node:test';
import assert from 'node:assert/strict';
import { __TEST_ONLY__ } from '../../src/hooks/useToolRegistry.js';

const {
  normalizeToolName,
  normalizeArgsShape,
  normalizeToolArgs,
  normalizePathLike,
  isLikelyArtifactId,
} = __TEST_ONLY__;

test('normalizeToolName resolves aliases for core tools', () => {
  assert.equal(normalizeToolName('list_dir'), 'listFiles');
  assert.equal(normalizeToolName('read_file'), 'readFile');
  assert.equal(normalizeToolName('create_file'), 'createFile');
  assert.equal(normalizeToolName('apply_code_to_file'), 'applyCodeToFile');
});

test('normalizeArgsShape parses JSON argument strings safely', () => {
  assert.deepEqual(normalizeArgsShape('{"a":1}'), { a: 1 });
  assert.deepEqual(normalizeArgsShape('not-json'), {});
  assert.deepEqual(normalizeArgsShape(null), {});
});

test('normalizeToolArgs infers artifact_type and common fields', () => {
  const normalized = normalizeToolArgs({ file_name: 'alu.sv', code: 'module alu; endmodule' });
  assert.equal(normalized.filename, 'alu.sv');
  assert.equal(normalized.artifact_type, 'rtl');
  assert.equal(normalized.content, 'module alu; endmodule');
});

test('normalizePathLike and isLikelyArtifactId handle path and UUID forms', () => {
  assert.equal(normalizePathLike('\\src\\RTL\\alu.sv'), '/src/rtl/alu.sv');
  assert.equal(isLikelyArtifactId('123e4567-e89b-12d3-a456-426614174000'), true);
  assert.equal(isLikelyArtifactId('alu.sv'), false);
});
