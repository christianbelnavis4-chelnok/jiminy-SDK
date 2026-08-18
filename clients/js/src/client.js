/**
 * Client — thin ergonomic wrapper around POST /evaluate.
 *
 * Mirrors clients/python/jiminy_sdk/client.py's Client: same method shape,
 * same error type, built on the global `fetch` (Node >=18) rather than a
 * dependency, matching the Python SDK's zero-runtime-dependency design.
 *
 * Usage:
 *
 *   const { Client, TraceBuilder } = require('@jiminy/sdk');
 *
 *   const client = new Client({ apiKey: '...', baseUrl: 'https://jiminy-api-...' });
 *   const trace = new TraceBuilder({ ... }).addStep(...).finalize('...').build();
 *   const result = await client.evaluate(trace);
 */

'use strict';

const { DEFAULT_BASE_URL, loadCredentials } = require('./auth');

class JiminyAPIError extends Error {
  constructor(status, body) {
    const detail = body && typeof body === 'object' ? body.detail : body;
    super(`Jiminy API error ${status}: ${JSON.stringify(detail)}`);
    this.name = 'JiminyAPIError';
    this.status = status;
    this.body = body;
  }
}

class Client {
  /**
   * Both apiKey and baseUrl are optional. If omitted, apiKey falls back to
   * the JIMINY_API_KEY env var, then to credentials saved by `jiminy auth
   * login` (see ./auth.js); baseUrl falls back to JIMINY_BASE_URL, then the
   * saved credentials' base_url, then the public API's default. So after
   * running `jiminy auth login` once, `new Client()` works with no
   * arguments at all.
   */
  constructor({ apiKey, baseUrl, timeoutMs = 30000 } = {}) {
    let credentials = null;
    if (!apiKey || !baseUrl) {
      credentials = loadCredentials() || {};
    }

    apiKey = apiKey || process.env.JIMINY_API_KEY || credentials?.api_key;
    if (!apiKey) {
      throw new Error(
        'No Jiminy API key found. Pass apiKey, set JIMINY_API_KEY, or run `jiminy auth login`.'
      );
    }
    baseUrl = baseUrl || process.env.JIMINY_BASE_URL || credentials?.base_url || DEFAULT_BASE_URL;

    this._apiKey = apiKey;
    this._baseUrl = baseUrl.replace(/\/+$/, '');
    this._timeoutMs = timeoutMs;
  }

  /**
   * POST a built trace object (e.g. from TraceBuilder.build()) to /evaluate.
   *
   * `trace` is the plain object returned by TraceBuilder.build() — this
   * method does not build or sign the trace itself, only submits it.
   *
   * Set mode="calibrate" for a diagnostic run that isn't persisted and
   * doesn't count against quota.
   *
   * Throws JiminyAPIError on any non-2xx response.
   */
  async evaluate(trace, { force = false, runs = 1, mode = 'evaluate' } = {}) {
    const params = new URLSearchParams();
    if (force) params.set('force', 'true');
    if (runs !== 1) params.set('runs', String(runs));
    if (mode !== 'evaluate') params.set('mode', mode);
    const qs = params.toString();
    const url = `${this._baseUrl}/evaluate${qs ? `?${qs}` : ''}`;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this._timeoutMs);
    let response;
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: {
          'X-API-Key': this._apiKey,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(trace),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }

    const text = await response.text();
    let body;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = text;
    }

    if (!response.ok) {
      throw new JiminyAPIError(response.status, body);
    }
    return body;
  }
}

module.exports = { Client, JiminyAPIError };
