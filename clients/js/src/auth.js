/**
 * auth.js — device-authorization login and local credential storage.
 *
 * Mirrors clients/python/jiminy_sdk/auth.py: same protocol, same
 * ~/.jiminy/credentials.json file (shared between the Python and JS SDKs,
 * so `jiminy auth login` from either package works for both), same
 * chmod 600 restriction. Built on Node's `fs`/`https` only — no runtime
 * dependency, matching the rest of this package.
 *
 * Protocol (RFC 8628-shaped, not yet implemented server-side — this
 * client is written against the contract the API will be built to match):
 *
 *   POST {baseUrl}/auth/device/start
 *     -> {device_code, user_code, verification_url,
 *         verification_url_complete?, poll_interval, expires_in}
 *
 *   POST {baseUrl}/auth/device/poll   {"device_code": "..."}
 *     -> 200 {"error": "authorization_pending" | "slow_down"}   (still waiting)
 *     -> 200 {"error": "expired_token" | "access_denied", ...}  (terminal failure)
 *     -> 200 {api_key, tenant_id, tier, org_name}                (success, no "error" key)
 */

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const DEFAULT_BASE_URL = 'https://jiminy-api-REDACTED_PROJECT_NUMBER.europe-west2.run.app';

class DeviceAuthError extends Error {}

function credentialsPath() {
  return process.env.JIMINY_CREDENTIALS_PATH || path.join(os.homedir(), '.jiminy', 'credentials.json');
}

function loadCredentials() {
  let raw;
  try {
    raw = fs.readFileSync(credentialsPath(), 'utf-8');
  } catch {
    return null;
  }
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!data || typeof data !== 'object' || !data.api_key) return null;
  return data;
}

function saveCredentials(data) {
  const p = credentialsPath();
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(data, null, 2), { mode: 0o600 });
  fs.chmodSync(p, 0o600);
}

function clearCredentials() {
  try {
    fs.unlinkSync(credentialsPath());
    return true;
  } catch {
    return false;
  }
}

async function postJson(url, body) {
  let response;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (err) {
    throw new DeviceAuthError(`Could not reach ${url}: ${err.message}`);
  }
  const text = await response.text();
  if (!response.ok) {
    throw new DeviceAuthError(`Jiminy API error ${response.status}: ${text}`);
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new DeviceAuthError(`Unexpected response from ${url}: ${text}`);
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Run the device-authorization flow and persist the resulting credentials.
 *
 * Opens the verification URL in a browser, polls until the user completes
 * Firebase sign-in, then saves {api_key, tenant_id, base_url, tier,
 * org_name} to ~/.jiminy/credentials.json and returns that object.
 *
 * Options: baseUrl, orgName, openBrowser(url), print(line), sleepFn(ms),
 * maxWaitMs. All optional, overridable for testing.
 *
 * Throws DeviceAuthError on timeout, denial, or an API error.
 */
async function login({
  baseUrl = DEFAULT_BASE_URL,
  orgName = null,
  openBrowser = defaultOpenBrowser,
  print = console.log,
  sleepFn = sleep,
  maxWaitMs = 300000,
} = {}) {
  const base = baseUrl.replace(/\/+$/, '');
  const start = await postJson(`${base}/auth/device/start`, orgName ? { org_name: orgName } : {});

  const { device_code: deviceCode, user_code: userCode, verification_url: verificationUrl } = start;
  let pollIntervalMs = Number(start.poll_interval || 5) * 1000;
  const expiresInMs = Number(start.expires_in || maxWaitMs / 1000) * 1000;

  print(`To sign in, visit: ${verificationUrl}`);
  print(`And enter code: ${userCode}`);
  openBrowser(start.verification_url_complete || verificationUrl);

  const deadline = Date.now() + Math.min(expiresInMs, maxWaitMs);
  while (Date.now() < deadline) {
    await sleepFn(pollIntervalMs);
    const result = await postJson(`${base}/auth/device/poll`, { device_code: deviceCode });

    const error = result.error;
    if (error === undefined || error === null) {
      const credentials = {
        api_key: result.api_key,
        tenant_id: result.tenant_id,
        tier: result.tier,
        org_name: result.org_name,
        base_url: base,
      };
      saveCredentials(credentials);
      return credentials;
    }
    if (error === 'authorization_pending') continue;
    if (error === 'slow_down') {
      pollIntervalMs *= 2;
      continue;
    }
    throw new DeviceAuthError(`Sign-in failed: ${error}`);
  }

  throw new DeviceAuthError('Sign-in timed out before it was completed in the browser.');
}

function defaultOpenBrowser(url) {
  const { exec } = require('child_process');
  const command =
    process.platform === 'darwin' ? `open "${url}"` : process.platform === 'win32' ? `start "" "${url}"` : `xdg-open "${url}"`;
  exec(command, () => {});
  return true;
}

module.exports = {
  DEFAULT_BASE_URL,
  DeviceAuthError,
  credentialsPath,
  loadCredentials,
  saveCredentials,
  clearCredentials,
  login,
};
