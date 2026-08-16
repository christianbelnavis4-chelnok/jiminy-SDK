/**
 * Deterministic JSON canonicalisation matching Python's
 * `json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)` —
 * the exact serialisation api/attestation.py and the Python
 * jiminy_sdk.TraceBuilder use to build HMAC payloads
 * (see docs/ATTESTATION_SPEC.md).
 *
 * Two things JS's built-in JSON.stringify does NOT do that this must:
 *   1. Sort object keys recursively (JSON.stringify preserves insertion order).
 *   2. Escape non-ASCII characters as \uXXXX (Python's default is
 *      ensure_ascii=True — JSON.stringify emits raw UTF-8 by default).
 * Getting either wrong produces a hash that silently disagrees with the
 * Python SDK and the server for any trace containing non-ASCII content —
 * this is exercised directly by attestation_vectors/v03.json ("Unicode in
 * step fields (French, German)"). See test/traceBuilder.test.js.
 */

'use strict';

const ESCAPES = {
  '"': '\\"',
  '\\': '\\\\',
  '\b': '\\b',
  '\f': '\\f',
  '\n': '\\n',
  '\r': '\\r',
  '\t': '\\t',
};

function escapeString(str) {
  let out = '"';
  for (const ch of str) {
    if (Object.prototype.hasOwnProperty.call(ESCAPES, ch)) {
      out += ESCAPES[ch];
      continue;
    }
    const code = ch.codePointAt(0);
    if (code < 0x20) {
      out += '\\u' + code.toString(16).padStart(4, '0');
    } else if (code < 0x7f) {
      out += ch;
    } else if (code <= 0xffff) {
      out += '\\u' + code.toString(16).padStart(4, '0');
    } else {
      // Surrogate pair for code points outside the BMP, matching how
      // Python's ensure_ascii encodes astral characters.
      const adjusted = code - 0x10000;
      const high = 0xd800 + (adjusted >> 10);
      const low = 0xdc00 + (adjusted & 0x3ff);
      out += '\\u' + high.toString(16).padStart(4, '0');
      out += '\\u' + low.toString(16).padStart(4, '0');
    }
  }
  return out + '"';
}

function stringifyValue(value) {
  if (value === null || value === undefined) return 'null';
  const t = typeof value;
  if (t === 'boolean') return value ? 'true' : 'false';
  if (t === 'number') {
    if (!Number.isFinite(value)) {
      throw new TypeError('Cannot canonicalise non-finite number: ' + value);
    }
    return String(value);
  }
  if (t === 'string') return escapeString(value);
  if (Array.isArray(value)) {
    return '[' + value.map(stringifyValue).join(',') + ']';
  }
  if (t === 'object') {
    const keys = Object.keys(value).sort();
    return (
      '{' +
      keys.map((k) => escapeString(k) + ':' + stringifyValue(value[k])).join(',') +
      '}'
    );
  }
  // Matches Python's default=str fallback for otherwise-unserialisable values.
  return escapeString(String(value));
}

/** Deterministic compact JSON string of obj, matching the Python SDK byte-for-byte. */
function canonical(obj) {
  return stringifyValue(obj);
}

module.exports = { canonical };
