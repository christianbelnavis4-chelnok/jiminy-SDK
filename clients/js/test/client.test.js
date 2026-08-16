'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { Client, JiminyAPIError } = require('../src/client');

function fakeFetch(handler) {
  const original = globalThis.fetch;
  globalThis.fetch = handler;
  return () => {
    globalThis.fetch = original;
  };
}

test('evaluate() sends X-API-Key header and JSON body, no query string by default', async () => {
  let captured;
  const restore = fakeFetch(async (url, init) => {
    captured = { url, init };
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ overall_verdict: 'approved' }),
    };
  });
  try {
    const client = new Client({ apiKey: 'k', baseUrl: 'https://api.example.com/' });
    const result = await client.evaluate({ trace_id: 't-1' });
    assert.equal(result.overall_verdict, 'approved');
    assert.equal(captured.url, 'https://api.example.com/evaluate');
    assert.equal(captured.init.headers['X-API-Key'], 'k');
    assert.deepEqual(JSON.parse(captured.init.body), { trace_id: 't-1' });
  } finally {
    restore();
  }
});

test('evaluate() appends query params for non-default options', async () => {
  let capturedUrl;
  const restore = fakeFetch(async (url) => {
    capturedUrl = url;
    return { ok: true, status: 200, text: async () => JSON.stringify({}) };
  });
  try {
    const client = new Client({ apiKey: 'k', baseUrl: 'https://api.example.com' });
    await client.evaluate({}, { force: true, runs: 3, mode: 'calibrate' });
    assert.match(capturedUrl, /force=true/);
    assert.match(capturedUrl, /runs=3/);
    assert.match(capturedUrl, /mode=calibrate/);
  } finally {
    restore();
  }
});

test('evaluate() throws JiminyAPIError with parsed body on non-2xx', async () => {
  const restore = fakeFetch(async () => ({
    ok: false,
    status: 403,
    text: async () => JSON.stringify({ detail: 'Invalid API key.' }),
  }));
  try {
    const client = new Client({ apiKey: 'bad', baseUrl: 'https://api.example.com' });
    await assert.rejects(
      () => client.evaluate({}),
      (err) => {
        assert.ok(err instanceof JiminyAPIError);
        assert.equal(err.status, 403);
        assert.equal(err.body.detail, 'Invalid API key.');
        return true;
      }
    );
  } finally {
    restore();
  }
});
