'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { Client, JiminyAPIError } = require('../src/client');

async function withIsolatedCredentials(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jiminy-client-test-'));
  const originalCreds = process.env.JIMINY_CREDENTIALS_PATH;
  const originalKey = process.env.JIMINY_API_KEY;
  const originalUrl = process.env.JIMINY_BASE_URL;
  process.env.JIMINY_CREDENTIALS_PATH = path.join(dir, 'credentials.json');
  delete process.env.JIMINY_API_KEY;
  delete process.env.JIMINY_BASE_URL;
  try {
    return await fn();
  } finally {
    if (originalCreds === undefined) delete process.env.JIMINY_CREDENTIALS_PATH;
    else process.env.JIMINY_CREDENTIALS_PATH = originalCreds;
    if (originalKey === undefined) delete process.env.JIMINY_API_KEY;
    else process.env.JIMINY_API_KEY = originalKey;
    if (originalUrl === undefined) delete process.env.JIMINY_BASE_URL;
    else process.env.JIMINY_BASE_URL = originalUrl;
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

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

test('Client() falls back to JIMINY_API_KEY and the default base URL', async () => {
  await withIsolatedCredentials(() => {
    process.env.JIMINY_API_KEY = 'env-key';
    delete require.cache[require.resolve('../src/client')];
    delete require.cache[require.resolve('../src/auth')];
    const { Client: FreshClient } = require('../src/client');
    const client = new FreshClient();
    assert.equal(client._apiKey, 'env-key');
    assert.equal(client._baseUrl, 'https://jiminy-api-REDACTED_PROJECT_NUMBER.europe-west2.run.app');
  });
});

test('Client() falls back to saved credentials when no key or env var', async () => {
  await withIsolatedCredentials(() => {
    delete require.cache[require.resolve('../src/client')];
    delete require.cache[require.resolve('../src/auth')];
    const { saveCredentials } = require('../src/auth');
    saveCredentials({ api_key: 'cli-key', tenant_id: 't', base_url: 'https://saved.example.com' });
    const { Client: FreshClient } = require('../src/client');
    const client = new FreshClient();
    assert.equal(client._apiKey, 'cli-key');
    assert.equal(client._baseUrl, 'https://saved.example.com');
  });
});

test('Client() throws a clear error when no key is found anywhere', async () => {
  await withIsolatedCredentials(() => {
    delete require.cache[require.resolve('../src/client')];
    delete require.cache[require.resolve('../src/auth')];
    const { Client: FreshClient } = require('../src/client');
    assert.throws(() => new FreshClient(), /jiminy auth login/);
  });
});

test('Client() explicit args take priority over env and saved credentials', async () => {
  await withIsolatedCredentials(() => {
    process.env.JIMINY_API_KEY = 'env-key';
    delete require.cache[require.resolve('../src/client')];
    delete require.cache[require.resolve('../src/auth')];
    const { saveCredentials } = require('../src/auth');
    saveCredentials({ api_key: 'cli-key', tenant_id: 't', base_url: 'https://saved.example.com' });
    const { Client: FreshClient } = require('../src/client');
    const client = new FreshClient({ apiKey: 'explicit-key', baseUrl: 'https://explicit.example.com' });
    assert.equal(client._apiKey, 'explicit-key');
    assert.equal(client._baseUrl, 'https://explicit.example.com');
  });
});
