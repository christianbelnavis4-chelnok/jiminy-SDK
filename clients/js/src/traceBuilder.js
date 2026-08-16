/**
 * TraceBuilder — construct attested DecisionTraces for the Jiminy API.
 *
 * Line-for-line port of clients/python/jiminy_sdk/builder.py — same
 * canonicalisation (see ./canonical.js), same HMAC-SHA256 chaining, same
 * GENESIS_HASH. Cross-language hash parity with the Python SDK and the
 * server is the entire point of a shared attestation spec
 * (docs/ATTESTATION_SPEC.md) — verified against the same golden vectors
 * the Python SDK is checked against, see test/traceBuilder.test.js.
 *
 * Usage:
 *
 *   const { TraceBuilder } = require('@jiminy/sdk');
 *
 *   const trace = new TraceBuilder({
 *     traceId: '...',
 *     agentId: 'PA-Agent-01',
 *     agentOwner: 'Acme Insurance',
 *     submittedBy: 'Acme Compliance',
 *     taskDescription: 'Evaluate prior authorisation for MRI scan.',
 *     timestamp: new Date(),
 *     domainProfile: 'health_insurance_prior_auth',
 *     hmacKey: 'your-per-tenant-hmac-key',
 *   })
 *     .addStep(1, 'eligibility_check', { input: { memberId: '123' }, output: { status: 'Active' }, reasoning: 'Confirmed member active.' })
 *     .finalize('Approved. Auth reference: PA-2026-001.')
 *     .build(); // plain object — POST to POST /evaluate
 */

'use strict';

const crypto = require('node:crypto');
const { canonical } = require('./canonical');

const GENESIS_HASH = '0'.repeat(64);

function stepPayload(stepId, tool, inputVal, outputVal, reasoning, prevHash) {
  const payload = {
    step_id: stepId,
    tool,
    input: canonical(inputVal),
    output: canonical(outputVal),
    reasoning: reasoning || '',
    prev_hash: prevHash,
  };
  return Buffer.from(canonical(payload), 'utf-8');
}

function rootPayload(traceId, agentId, stepHashes) {
  const payload = {
    trace_id: traceId,
    agent_id: agentId,
    step_hashes: stepHashes,
  };
  return Buffer.from(canonical(payload), 'utf-8');
}

function hmacSha256Hex(key, data) {
  return crypto.createHmac('sha256', key).update(data).digest('hex');
}

class TraceBuilder {
  constructor({
    traceId,
    agentId,
    agentOwner,
    submittedBy,
    taskDescription,
    timestamp,
    domainProfile,
    hmacKey,
    escalationEvents,
    errorEvents,
    callbackUrl,
    environment,
    framework,
  }) {
    this._traceId = traceId;
    this._agentId = agentId;
    const ts = timestamp instanceof Date ? timestamp.toISOString() : timestamp;
    this._meta = {
      trace_id: traceId,
      agent_id: agentId,
      agent_owner: agentOwner,
      submitted_by: submittedBy,
      task_description: taskDescription,
      timestamp: ts,
      domain_profile: domainProfile,
      final_output: '',
      escalation_events: escalationEvents || [],
      error_events: errorEvents || [],
    };
    if (callbackUrl !== undefined && callbackUrl !== null) {
      this._meta.callback_url = callbackUrl;
    }
    if (environment !== undefined && environment !== null) {
      this._meta.environment = environment;
    }
    if (framework !== undefined && framework !== null) {
      this._meta.framework = framework;
    }
    this._key = Buffer.isBuffer(hmacKey) ? hmacKey : Buffer.from(String(hmacKey), 'utf-8');
    this._steps = [];
    this._prevHash = GENESIS_HASH;
  }

  /**
   * Add a signed step to the trace. Must be called in step order.
   * @param {number} stepId
   * @param {string} tool
   * @param {{input: any, output: any, reasoning?: string}} fields
   */
  addStep(stepId, tool, { input, output, reasoning } = {}) {
    const payload = stepPayload(stepId, tool, input, output, reasoning, this._prevHash);
    const stepHash = hmacSha256Hex(this._key, payload);
    this._steps.push({
      step_id: stepId,
      tool,
      input,
      output,
      reasoning: reasoning === undefined ? null : reasoning,
      step_hash: stepHash,
    });
    this._prevHash = stepHash;
    return this;
  }

  /** Set the trace final output. Call before build(). */
  finalize(finalOutput) {
    this._meta.final_output = finalOutput;
    return this;
  }

  /** Return the complete trace object, ready for POST to /evaluate. */
  build() {
    if (this._steps.length === 0) {
      throw new Error('TraceBuilder.build() called with no steps added.');
    }
    const stepHashes = this._steps.map((s) => s.step_hash);
    const rootHash = hmacSha256Hex(
      this._key,
      rootPayload(this._traceId, this._agentId, stepHashes)
    );

    const steps = this._steps.map((s) => {
      const stepDict = {
        step_id: s.step_id,
        tool: s.tool,
        input: s.input,
        output: s.output,
        step_hash: s.step_hash,
      };
      if (s.reasoning !== null) {
        stepDict.reasoning = s.reasoning;
      }
      return stepDict;
    });

    return {
      ...this._meta,
      steps,
      trace_root_hash: rootHash,
    };
  }
}

module.exports = { TraceBuilder, GENESIS_HASH };
