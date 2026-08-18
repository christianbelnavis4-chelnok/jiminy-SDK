#!/usr/bin/env node
/**
 * `jiminy` CLI: `jiminy auth login|status|logout`.
 *
 * Registered as a bin entry in package.json. Shares
 * ~/.jiminy/credentials.json with the Python SDK's `jiminy` CLI, so
 * signing in with either package works for both.
 */

'use strict';

const { DEFAULT_BASE_URL, DeviceAuthError, clearCredentials, loadCredentials, login } = require('../src/auth');

async function authLogin(args) {
  const baseUrlIndex = args.indexOf('--base-url');
  const baseUrl = baseUrlIndex >= 0 ? args[baseUrlIndex + 1] : DEFAULT_BASE_URL;
  const orgNameIndex = args.indexOf('--org-name');
  const orgName = orgNameIndex >= 0 ? args[orgNameIndex + 1] : null;

  try {
    const credentials = await login({ baseUrl, orgName });
    console.log(`Signed in. Tenant: ${credentials.tenant_id}  Tier: ${credentials.tier}`);
    console.log('Credentials saved — new Client() will pick them up automatically.');
    return 0;
  } catch (err) {
    if (err instanceof DeviceAuthError) {
      console.error(`Login failed: ${err.message}`);
      return 1;
    }
    throw err;
  }
}

function authStatus() {
  const credentials = loadCredentials();
  if (!credentials) {
    console.log('Not signed in. Run `jiminy auth login`.');
    return 1;
  }
  console.log(`Signed in. Tenant: ${credentials.tenant_id}  Tier: ${credentials.tier}`);
  console.log(`Base URL: ${credentials.base_url || DEFAULT_BASE_URL}`);
  return 0;
}

function authLogout() {
  const removed = clearCredentials();
  console.log(removed ? 'Signed out.' : 'Not signed in.');
  return 0;
}

async function main(argv) {
  const [command, subcommand, ...rest] = argv;

  if (command === 'auth' && subcommand === 'login') return authLogin(rest);
  if (command === 'auth' && subcommand === 'status') return authStatus();
  if (command === 'auth' && subcommand === 'logout') return authLogout();

  console.error('Usage: jiminy auth <login|status|logout> [--base-url URL] [--org-name NAME]');
  return 1;
}

main(process.argv.slice(2)).then((code) => process.exit(code));
