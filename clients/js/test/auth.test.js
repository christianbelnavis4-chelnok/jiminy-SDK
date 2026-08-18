'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

async function withIsolatedCredentials(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jiminy-auth-test-'));
  const original = process.env.JIMINY_CREDENTIALS_PATH;
  process.env.JIMINY_CREDENTIALS_PATH = path.join(dir, 'credentials.json');
  try {
    return await fn();
  } finally {
    if (original === undefined) delete process.env.JIMINY_CREDENTIALS_PATH;
    else process.env.JIMINY_CREDENTIALS_PATH = original;
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function fakeFetchSequence(responses) {
  let n = 0;
  const original = globalThis.fetch;
  globalThis.fetch = async () => {
    const body = responses[n];
    n += 1;
    return { ok: true, status: 200, text: async () => JSON.stringify(body) };
  };
  return () => {
    globalThis.fetch = original;
  };
}

test('credentials: load returns null when no file', async () => {
  await withIsolatedCredentials(() => {
    delete require.cache[require.resolve('../src/auth')];
    const { loadCredentials } = require('../src/auth');
    assert.equal(loadCredentials(), null);
  });
});

test('credentials: save then load round-trips and restricts permissions', async () => {
  await withIsolatedCredentials(() => {
    delete require.cache[require.resolve('../src/auth')];
    const { saveCredentials, loadCredentials, credentialsPath } = require('../src/auth');
    saveCredentials({ api_key: 'k', tenant_id: 't' });
    assert.deepEqual(loadCredentials(), { api_key: 'k', tenant_id: 't' });
    if (process.platform !== 'win32') {
      const mode = fs.statSync(credentialsPath()).mode & 0o777;
      assert.equal(mode, 0o600);
    }
  });
});

test('credentials: clear reports whether a file existed', async () => {
  await withIsolatedCredentials(() => {
    delete require.cache[require.resolve('../src/auth')];
    const { saveCredentials, clearCredentials } = require('../src/auth');
    assert.equal(clearCredentials(), false);
    saveCredentials({ api_key: 'k', tenant_id: 't' });
    assert.equal(clearCredentials(), true);
  });
});

test('login: success saves and returns credentials', async () => {
  await withIsolatedCredentials(async () => {
    delete require.cache[require.resolve('../src/auth')];
    const { login, loadCredentials } = require('../src/auth');

    const restoreFetch = fakeFetchSequence([
      {
        device_code: 'dc-1',
        user_code: 'ABCD-1234',
        verification_url: 'https://app.jiminy.uk/cli-auth',
        poll_interval: 0,
        expires_in: 60,
      },
      { error: 'authorization_pending' },
      { api_key: 'sk-live-1', tenant_id: 'self-acme-1', tier: 'starter', org_name: 'Acme' },
    ]);
    try {
      const opened = [];
      const result = await login({
        baseUrl: 'https://api.example.com',
        openBrowser: (url) => opened.push(url) && true,
        print: () => {},
        sleepFn: () => Promise.resolve(),
      });
      assert.equal(result.api_key, 'sk-live-1');
      assert.equal(result.tenant_id, 'self-acme-1');
      assert.deepEqual(opened, ['https://app.jiminy.uk/cli-auth']);
      assert.equal(loadCredentials().api_key, 'sk-live-1');
    } finally {
      restoreFetch();
    }
  });
});

test('login: access_denied rejects and does not save', async () => {
  await withIsolatedCredentials(async () => {
    delete require.cache[require.resolve('../src/auth')];
    const { login, loadCredentials, DeviceAuthError } = require('../src/auth');

    const restoreFetch = fakeFetchSequence([
      {
        device_code: 'dc-1',
        user_code: 'ABCD-1234',
        verification_url: 'https://app.jiminy.uk/cli-auth',
        poll_interval: 0,
        expires_in: 60,
      },
      { error: 'access_denied' },
    ]);
    try {
      await assert.rejects(
        () =>
          login({
            baseUrl: 'https://api.example.com',
            openBrowser: () => true,
            print: () => {},
            sleepFn: () => Promise.resolve(),
          }),
        DeviceAuthError
      );
      assert.equal(loadCredentials(), null);
    } finally {
      restoreFetch();
    }
  });
});
