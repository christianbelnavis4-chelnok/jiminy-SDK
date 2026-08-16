/**
 * Verifies TraceBuilder against the same golden vectors the Python SDK and
 * server (api/attestation.py) are checked against
 * (../../../attestation_vectors/*.json, see tests/test_reliability_block.py's
 * test_golden_vector_step_hashes / test_golden_vector_root_hash).
 *
 * This is the concrete, checkable proof that the JS canonicalisation
 * (src/canonical.js) produces byte-identical HMAC payloads to the Python
 * implementation — the whole point of a shared, cross-language attestation
 * spec (docs/ATTESTATION_SPEC.md).
 */

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const { TraceBuilder, GENESIS_HASH } = require('../src/traceBuilder');
const { canonical } = require('../src/canonical');

const VECTORS_DIR = path.join(__dirname, '..', '..', '..', 'attestation_vectors');

function loadVectors() {
  return fs
    .readdirSync(VECTORS_DIR)
    .filter((f) => f.endsWith('.json'))
    .sort()
    .map((f) => JSON.parse(fs.readFileSync(path.join(VECTORS_DIR, f), 'utf-8')));
}

function hmacHex(key, buf) {
  return crypto.createHmac('sha256', key).update(buf).digest('hex');
}

for (const vector of loadVectors()) {
  test(`golden vector ${vector.vector_id}: step hashes match`, () => {
    const key = Buffer.from(vector.hmac_key, 'utf-8');
    let prev = GENESIS_HASH;
    const computed = [];
    for (const step of vector.trace.steps) {
      const payload = {
        step_id: step.step_id,
        tool: step.tool,
        input: canonical(step.input),
        output: canonical(step.output),
        reasoning: step.reasoning || '',
        prev_hash: prev,
      };
      const h = hmacHex(key, Buffer.from(canonical(payload), 'utf-8'));
      computed.push(h);
      prev = h;
    }
    assert.deepEqual(computed, vector.expected_step_hashes);
  });

  test(`golden vector ${vector.vector_id}: root hash matches`, () => {
    const key = Buffer.from(vector.hmac_key, 'utf-8');
    const payload = {
      trace_id: vector.trace.trace_id,
      agent_id: vector.trace.agent_id,
      step_hashes: vector.expected_step_hashes,
    };
    const root = hmacHex(key, Buffer.from(canonical(payload), 'utf-8'));
    assert.equal(root, vector.expected_root_hash);
  });

  test(`golden vector ${vector.vector_id}: TraceBuilder end-to-end matches`, () => {
    const builder = new TraceBuilder({
      traceId: vector.trace.trace_id,
      agentId: vector.trace.agent_id,
      agentOwner: 'irrelevant-for-hash',
      submittedBy: 'irrelevant-for-hash',
      taskDescription: 'irrelevant-for-hash',
      timestamp: new Date('2026-01-01T00:00:00Z'),
      domainProfile: 'general',
      hmacKey: vector.hmac_key,
    });
    for (const step of vector.trace.steps) {
      builder.addStep(step.step_id, step.tool, {
        input: step.input,
        output: step.output,
        reasoning: step.reasoning,
      });
    }
    builder.finalize('irrelevant-for-hash');
    const trace = builder.build();

    assert.deepEqual(
      trace.steps.map((s) => s.step_hash),
      vector.expected_step_hashes
    );
    assert.equal(trace.trace_root_hash, vector.expected_root_hash);
  });
}

test('mutation breaks the chain (tamper detectable)', () => {
  const vector = loadVectors()[0];
  const key = Buffer.from(vector.hmac_key, 'utf-8');
  const step = vector.trace.steps[0];
  const payload = {
    step_id: step.step_id,
    tool: step.tool + '_tampered',
    input: canonical(step.input),
    output: canonical(step.output),
    reasoning: step.reasoning || '',
    prev_hash: GENESIS_HASH,
  };
  const h = hmacHex(key, Buffer.from(canonical(payload), 'utf-8'));
  assert.notEqual(h, vector.expected_step_hashes[0]);
});
