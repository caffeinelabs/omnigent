// Pure helpers shared by run.mjs, explore.mjs and fixtures.ts.
// Node built-ins only, so they are tested without a browser.

import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import zlib from 'node:zlib';

export const STATUSES = Object.freeze(['PASS', 'PARTIAL', 'FAIL', 'ERROR']);

/** Exit codes of run.mjs: PASS and PARTIAL succeed, a stale preview is 3. */
export const EXIT = Object.freeze({ PASS: 0, PARTIAL: 0, FAIL: 1, ERROR: 2, STALE: 3 });

export const REDACTED = '[redacted]';

// Same set the omnigent server strips from uploaded HARs.
const SECRET_HEADERS = new Set([
  'authorization',
  'proxy-authorization',
  'cookie',
  'set-cookie',
  'x-api-key',
  'x-auth-token',
]);
const SECRET_PARAM =
  /^(access_token|refresh_token|id_token|token|secret|password|passwd|api_?key|key|code|signature|sig|x-amz-signature|x-amz-credential|x-amz-security-token)$/i;
const SECRET_JSON_KEY = /(secret|token|password|passwd|authorization|cookie|api_?key)/i;
const ANSI = /\u001b\[[0-9;]*[A-Za-z]/g;
const FULL_SHA = /^[0-9a-f]{40}$/;

export const BODY_MAX_BYTES = 8 * 1024;
export const BAKED_BROWSERS_PATH = '/opt/ms-playwright';

/** @returns {number} The process exit code for a demo status (unknown → ERROR). */
export function exitCodeFor(status) {
  return Object.hasOwn(EXIT, status) ? EXIT[status] : EXIT.ERROR;
}

export function isFullSha(value) {
  return typeof value === 'string' && FULL_SHA.test(value);
}

export function sha7(sha) {
  return String(sha || '').slice(0, 7);
}

/** @returns {number|null} The PR number from a PR URL or a bare number. */
export function prNumber(pr) {
  const text = String(pr ?? '').trim();
  const match = /\/pull\/(\d+)/.exec(text) || /^#?(\d+)$/.exec(text);
  return match ? Number(match[1]) : null;
}

export function watermark(pr, sha) {
  return `PR #${pr} · ${sha7(sha)} · preview`;
}

export function stripAnsi(text) {
  return String(text ?? '').replace(ANSI, '');
}

/** The first non-empty line of an error message, without colour codes. */
export function firstLine(text, max = 300) {
  const line =
    stripAnsi(text)
      .split('\n')
      .map((l) => l.trim())
      .find(Boolean) || '';
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

/** A failure note: the first line plus Playwright's Expected / Received lines. */
export function errorNote(text, max = 300) {
  const lines = stripAnsi(text)
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
  const detail = lines.filter((l) => /^(Expected|Received)( string| value)?:/.test(l)).slice(0, 2);
  const note = [lines[0] || '', ...detail].join(' · ');
  return note.length > max ? `${note.slice(0, max - 1)}…` : note;
}

export function slug(title, max = 48) {
  const text = String(title ?? '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, max)
    .replace(/-+$/, '');
  return text || 'step';
}

/** @returns {string} e.g. ``shots/01-open-landing-page.png`` (relative to the out dir). */
export function screenshotName(n, title, suffix) {
  const tail = suffix ? `-${suffix}` : '';
  return `shots/${String(n).padStart(2, '0')}-${slug(title)}${tail}.png`;
}

/**
 * Cut a body to at most ``max`` UTF-8 bytes.
 * @returns {{text: string, bytes: number, truncated: boolean}}
 */
export function truncate(text, max = BODY_MAX_BYTES) {
  const value = String(text ?? '');
  const bytes = Buffer.byteLength(value);
  if (bytes <= max) {
    return { text: value, bytes, truncated: false };
  }
  return { text: Buffer.from(value).subarray(0, max).toString('utf8'), bytes, truncated: true };
}

function isSecretHeader(name) {
  return SECRET_HEADERS.has(String(name).toLowerCase());
}

/** Header map or ``[{name, value}]`` list with secret values replaced. */
export function redactHeaders(headers) {
  if (Array.isArray(headers)) {
    return headers.map((h) => (h && isSecretHeader(h.name) ? { ...h, value: REDACTED } : h));
  }
  const out = {};
  for (const [name, value] of Object.entries(headers || {})) {
    out[name] = isSecretHeader(name) ? REDACTED : value;
  }
  return out;
}

/** A URL with the values of credential-like query parameters replaced. */
export function redactUrl(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return url;
  }
  let changed = false;
  for (const name of [...new Set(parsed.searchParams.keys())]) {
    if (SECRET_PARAM.test(name)) {
      parsed.searchParams.set(name, REDACTED);
      changed = true;
    }
  }
  return changed ? parsed.toString() : url;
}

function redactJsonValue(value) {
  if (Array.isArray(value)) {
    return value.map(redactJsonValue);
  }
  if (value && typeof value === 'object') {
    const out = {};
    for (const [key, item] of Object.entries(value)) {
      out[key] = SECRET_JSON_KEY.test(key) ? REDACTED : redactJsonValue(item);
    }
    return out;
  }
  return value;
}

/** A JSON body with credential-like keys replaced; other text is returned as is. */
export function redactJsonText(text) {
  if (typeof text !== 'string' || !text.trim()) {
    return text;
  }
  try {
    return JSON.stringify(redactJsonValue(JSON.parse(text)));
  } catch {
    return text;
  }
}

/**
 * Strip credentials from a Playwright HAR: auth and cookie headers, cookie
 * values, credential query parameters and credential-like request body keys.
 * @returns {object} A redacted copy.
 */
export function redactHar(har) {
  const copy = JSON.parse(JSON.stringify(har ?? {}));
  const entries = copy?.log?.entries;
  for (const entry of Array.isArray(entries) ? entries : []) {
    for (const side of ['request', 'response']) {
      const message = entry?.[side];
      if (!message || typeof message !== 'object') {
        continue;
      }
      if (Array.isArray(message.headers)) {
        message.headers = redactHeaders(message.headers);
      }
      if (Array.isArray(message.cookies)) {
        message.cookies = message.cookies.map((c) => ({ ...c, value: REDACTED }));
      }
    }
    const request = entry?.request;
    if (request && typeof request.url === 'string') {
      request.url = redactUrl(request.url);
    }
    if (Array.isArray(request?.queryString)) {
      request.queryString = request.queryString.map((q) =>
        q && SECRET_PARAM.test(q.name) ? { ...q, value: REDACTED } : q,
      );
    }
    const post = request?.postData;
    if (post && typeof post === 'object') {
      if (typeof post.text === 'string') {
        post.text = redactJsonText(post.text);
      }
      if (Array.isArray(post.params)) {
        post.params = post.params.map((p) =>
          p && (SECRET_PARAM.test(p.name) || SECRET_JSON_KEY.test(p.name)) ? { ...p, value: REDACTED } : p,
        );
      }
    }
    if (typeof entry?.response?.redirectURL === 'string' && entry.response.redirectURL) {
      entry.response.redirectURL = redactUrl(entry.response.redirectURL);
    }
  }
  return copy;
}

// Credentials in free text: header lines (Playwright call logs), bearer tokens, token cookies.
const SECRET_HEADER_LINE = /\b(authorization|proxy-authorization|set-cookie|cookie|x-api-key|x-auth-token)(\s*:\s*)[^\r\n"\\]+/gi;
const BEARER = /\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}/g;
const TOKEN_PAIR = /\b(access_token|refresh_token|id_token)=[^;,&\s"'\\]+/gi;
const MIN_SECRET_LENGTH = 8;

function literalSecrets(secrets) {
  return [...new Set((secrets || []).filter((s) => typeof s === 'string' && s.length >= MIN_SECRET_LENGTH))];
}

/** Text with the known secrets, auth and cookie header values and token cookies replaced. */
export function scrubText(text, secrets = []) {
  let out = String(text ?? '');
  for (const secret of literalSecrets(secrets)) out = out.split(secret).join(REDACTED);
  return out
    .replace(SECRET_HEADER_LINE, `$1$2${REDACTED}`)
    .replace(BEARER, `$1 ${REDACTED}`)
    .replace(TOKEN_PAIR, `$1=${REDACTED}`);
}

function replaceBytes(data, needle, replacement) {
  const parts = [];
  let from = 0;
  for (let at = data.indexOf(needle); at !== -1; at = data.indexOf(needle, from)) {
    parts.push(data.subarray(from, at), replacement);
    from = at + needle.length;
  }
  if (!parts.length) return data;
  parts.push(data.subarray(from));
  return Buffer.concat(parts);
}

// A Playwright trace event or network snapshot: header and cookie values go, whatever their name.
function redactTraceValue(value, key) {
  if (Array.isArray(value)) {
    if (key === 'cookies') return value.map((c) => (c && typeof c === 'object' ? { ...c, value: REDACTED } : c));
    return value.map((item) => redactTraceValue(item));
  }
  if (!value || typeof value !== 'object') return value;
  const pair =
    typeof value.name === 'string' &&
    typeof value.value === 'string' &&
    (isSecretHeader(value.name) || SECRET_PARAM.test(value.name));
  const out = {};
  for (const [k, item] of Object.entries(value)) {
    if (pair && k === 'value') out[k] = REDACTED;
    else if (isSecretHeader(k) && typeof item === 'string') out[k] = REDACTED;
    else if (k === 'postData' && item && typeof item.text === 'string')
      out[k] = { ...item, text: redactJsonText(item.text) };
    else out[k] = redactTraceValue(item, k);
  }
  return out;
}

/**
 * One file of a Playwright trace with its credentials removed: trace and
 * network events structurally, other text by pattern, binaries by the known
 * secrets only.
 * @returns {Buffer}
 */
export function scrubTraceEntry(name, data, secrets = []) {
  const text = data.toString('utf8');
  const isText = Buffer.from(text, 'utf8').equals(data);
  if (isText && /\.(trace|network)$/.test(name)) {
    const lines = text.split('\n').map((line) => {
      if (!line.trim()) return line;
      try {
        return scrubText(JSON.stringify(redactTraceValue(JSON.parse(line))), secrets);
      } catch {
        return scrubText(line, secrets);
      }
    });
    return Buffer.from(lines.join('\n'), 'utf8');
  }
  if (isText && /\.(stacks|json|txt|html?|css|m?js|xml|md)$/i.test(name)) {
    return Buffer.from(scrubText(text, secrets), 'utf8');
  }
  let out = data;
  for (const secret of literalSecrets(secrets)) out = replaceBytes(out, Buffer.from(secret), Buffer.from(REDACTED));
  return out;
}

// ── Zip (Playwright traces): read stored/deflated entries, write deflated ones.

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

export function crc32(data) {
  if (typeof zlib.crc32 === 'function') return zlib.crc32(data);
  let c = 0xffffffff;
  for (let i = 0; i < data.length; i += 1) c = CRC_TABLE[(c ^ data[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** @returns {{name: string, data: Buffer}[]} The files of a zip archive (no zip64). */
export function readZip(buf) {
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 22 - 0xffff); i -= 1) {
    if (buf.readUInt32LE(i) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw new Error('not a zip archive');
  const count = buf.readUInt16LE(eocd + 10);
  let at = buf.readUInt32LE(eocd + 16);
  if (count === 0xffff || at === 0xffffffff) throw new Error('zip64 archives are not supported');
  const entries = [];
  for (let i = 0; i < count; i += 1) {
    if (buf.readUInt32LE(at) !== 0x02014b50) throw new Error('corrupt zip central directory');
    const method = buf.readUInt16LE(at + 10);
    const size = buf.readUInt32LE(at + 20);
    const nameLength = buf.readUInt16LE(at + 28);
    const name = buf.toString('utf8', at + 46, at + 46 + nameLength);
    const local = buf.readUInt32LE(at + 42);
    if (size === 0xffffffff || local === 0xffffffff) throw new Error('zip64 archives are not supported');
    if (buf.readUInt32LE(local) !== 0x04034b50) throw new Error(`corrupt zip entry ${name}`);
    const start = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28);
    const raw = buf.subarray(start, start + size);
    if (method !== 0 && method !== 8) throw new Error(`zip method ${method} of ${name} is not supported`);
    if (!name.endsWith('/')) entries.push({ name, data: method === 8 ? zlib.inflateRawSync(raw) : Buffer.from(raw) });
    at += 46 + nameLength + buf.readUInt16LE(at + 30) + buf.readUInt16LE(at + 32);
  }
  return entries;
}

/** @returns {Buffer} A zip archive of ``entries``, each deflated. */
export function writeZip(entries) {
  const parts = [];
  const central = [];
  let offset = 0;
  const now = new Date();
  const time = (now.getHours() << 11) | (now.getMinutes() << 5) | Math.floor(now.getSeconds() / 2);
  const date = ((Math.max(now.getFullYear(), 1980) - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
  for (const { name, data } of entries) {
    const nameBytes = Buffer.from(name, 'utf8');
    const packed = zlib.deflateRawSync(data);
    const crc = crc32(data);
    const header = Buffer.alloc(30);
    header.writeUInt32LE(0x04034b50, 0);
    header.writeUInt16LE(20, 4);
    header.writeUInt16LE(0x0800, 6); // UTF-8 names
    header.writeUInt16LE(8, 8);
    header.writeUInt16LE(time, 10);
    header.writeUInt16LE(date, 12);
    header.writeUInt32LE(crc, 14);
    header.writeUInt32LE(packed.length, 18);
    header.writeUInt32LE(data.length, 22);
    header.writeUInt16LE(nameBytes.length, 26);
    const record = Buffer.alloc(46);
    record.writeUInt32LE(0x02014b50, 0);
    record.writeUInt16LE(20, 4);
    record.writeUInt16LE(20, 6);
    record.writeUInt16LE(0x0800, 8);
    record.writeUInt16LE(8, 10);
    record.writeUInt16LE(time, 12);
    record.writeUInt16LE(date, 14);
    record.writeUInt32LE(crc, 16);
    record.writeUInt32LE(packed.length, 20);
    record.writeUInt32LE(data.length, 24);
    record.writeUInt16LE(nameBytes.length, 28);
    record.writeUInt32LE(offset, 42);
    parts.push(header, nameBytes, packed);
    central.push(record, nameBytes);
    offset += header.length + nameBytes.length + packed.length;
    if (offset > 0xffffffff) throw new Error('trace too large to rewrite');
  }
  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, directory, end]);
}

/** A Playwright trace.zip with every credential removed. */
export function scrubTraceZip(buf, secrets = []) {
  return writeZip(readZip(buf).map(({ name, data }) => ({ name, data: scrubTraceEntry(name, data, secrets) })));
}

// ── Synthetic session (the app's POST <api>/auth/synthetic-session)

/**
 * Open a synthetic session with Node's fetch, outside Playwright, so the
 * Bearer secret never reaches a trace, HAR or call log.
 * @returns {Promise<{userId: string, accessToken: string, refreshToken: string}>}
 */
export async function syntheticSession(api, secret, { fetchImpl = fetch, timeoutMs = 30_000 } = {}) {
  const endpoint = `${String(api).replace(/\/+$/, '')}/auth/synthetic-session`;
  const response = await fetchImpl(endpoint, {
    method: 'POST',
    headers: { Authorization: `Bearer ${secret}`, 'content-type': 'application/json', accept: 'application/json' },
    body: '{}',
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!response.ok) {
    const hint = response.status === 404 ? ' (404: the env has no SYNTHETIC_SESSION_SECRET)' : '';
    throw new Error(`synthetic session: HTTP ${response.status} from ${endpoint}${hint}`);
  }
  const body = await response.json().catch(() => ({}));
  const setCookies = typeof response.headers.getSetCookie === 'function' ? response.headers.getSetCookie() : [];
  const cookie = (name) =>
    setCookies.map((h) => (h.startsWith(`${name}=`) ? h.slice(name.length + 1).split(';')[0] : '')).find(Boolean);
  const accessToken = cookie('access_token');
  const refreshToken = cookie('refresh_token');
  if (!accessToken || !refreshToken) throw new Error('synthetic session set no access_token / refresh_token cookie');
  return { userId: body?.user?.userId ?? '', accessToken, refreshToken };
}

/**
 * The session cookies for the app and API hosts (SameSite=None when the API is cross-site over https).
 * @returns {{name: string, value: string, domain: string, path: string, httpOnly: boolean, secure: boolean, sameSite: 'Lax' | 'None'}[]}
 */
export function sessionCookies(app, api, { accessToken, refreshToken }) {
  const appUrl = new URL(app);
  const apiUrl = new URL(api);
  const crossSite = apiUrl.hostname !== appUrl.hostname && apiUrl.protocol === 'https:';
  const hosts = [
    { domain: appUrl.hostname, secure: appUrl.protocol === 'https:', sameSite: 'Lax' },
    { domain: apiUrl.hostname, secure: apiUrl.protocol === 'https:', sameSite: crossSite ? 'None' : 'Lax' },
  ];
  return hosts.flatMap((host) =>
    [
      { name: 'access_token', value: accessToken },
      { name: 'refresh_token', value: refreshToken },
    ].map((cookie) => ({ ...cookie, ...host, path: '/', httpOnly: true })),
  );
}

/**
 * The status of a run from its recorded steps and the test outcome.
 *
 * ERROR: the harness, browser or spec broke (nothing recorded). FAIL: a step
 * failed, or the test failed outside a step. PARTIAL: some steps were skipped.
 * @returns {{status: string, reason: string}}
 */
export function deriveStatus({ steps = [], testsRan = true, testFailed = false, error = null } = {}) {
  if (error) {
    return { status: 'ERROR', reason: firstLine(error) };
  }
  if (!testsRan) {
    return { status: 'ERROR', reason: 'Playwright ran no test; check run.log' };
  }
  if (steps.length === 0) {
    return {
      status: 'ERROR',
      reason: testFailed ? 'the spec failed before its first step; check run.log' : 'the spec recorded no steps',
    };
  }
  const failed = steps.find((s) => s.status === 'fail');
  if (failed) {
    return { status: 'FAIL', reason: `step ${failed.n} failed: ${failed.title}` };
  }
  if (testFailed) {
    return { status: 'FAIL', reason: 'the spec failed outside a step; check run.log' };
  }
  if (steps.some((s) => s.status === 'skipped')) {
    return { status: 'PARTIAL', reason: 'some steps were skipped' };
  }
  return { status: 'PASS', reason: 'every step passed' };
}

/** A one-line summary of the steps when the agent passes none. */
export function defaultSummary(steps, status, reason = '') {
  const passed = steps.filter((s) => s.status === 'pass');
  const skipped = steps.filter((s) => s.status === 'skipped');
  const failed = steps.find((s) => s.status === 'fail');
  let text;
  if (status === 'ERROR') {
    text = `Demo could not run: ${reason || 'harness error'}`;
  } else if (failed) {
    text = `Step ${failed.n} failed: ${failed.title}${failed.note ? ` (${failed.note})` : ''}; ${passed.length} of ${steps.length} passed`;
  } else {
    text = passed.map((s) => s.title).join('; ');
    if (skipped.length) {
      const why = skipped.map((s) => (s.note ? `${s.title} — ${s.note}` : s.title)).join('; ');
      text += `${text ? '. ' : ''}Skipped: ${why}`;
    }
  }
  return text.length > 1000 ? `${text.slice(0, 999)}…` : text;
}

function intOrNull(value) {
  return Number.isInteger(value) ? value : null;
}

/** One step as demo.json records it. */
export function normalizeStep(raw, index) {
  const kind = ['ui', 'api', 'check'].includes(raw?.kind) ? raw.kind : 'check';
  const status = ['pass', 'fail', 'skipped'].includes(raw?.status) ? raw.status : 'fail';
  const step = {
    n: intOrNull(raw?.n) ?? index + 1,
    title: String(raw?.title || `Step ${index + 1}`).slice(0, 200),
    kind,
    status,
  };
  if (typeof raw?.screenshot === 'string' && raw.screenshot) step.screenshot = raw.screenshot;
  if (intOrNull(raw?.duration_ms) !== null) step.duration_ms = raw.duration_ms;
  if (raw?.note) step.note = String(raw.note).slice(0, 300);
  if (Array.isArray(raw?.api_calls) && raw.api_calls.length) step.api_calls = raw.api_calls.filter(Number.isInteger);
  return step;
}

export function consoleErrorCount(entries) {
  return (Array.isArray(entries) ? entries : []).filter((e) => e?.type === 'error' || e?.type === 'pageerror').length;
}

/**
 * The ``demo.json`` manifest omnigent's ``sys_factory_demo(manifest_path=…)`` reads.
 *
 * ``artifacts`` keys map to file kinds on the server (video, poster, api, har,
 * trace, console, script, log; others are downloads). Only files that exist
 * are listed; paths are relative to the manifest.
 */
export function buildManifest({
  prUrl,
  pr,
  sha,
  status,
  summary,
  planSource = null,
  app,
  api,
  observedSha = null,
  startedAt,
  finishedAt,
  steps = [],
  consoleErrors = 0,
  artifacts = {},
  exists = () => true,
}) {
  if (!STATUSES.includes(status)) {
    throw new Error(`status must be one of ${STATUSES.join('|')}, not ${status}`);
  }
  const normalized = steps.map(normalizeStep);
  const listed = {};
  for (const [key, rel] of Object.entries(artifacts)) {
    if (typeof rel === 'string' && rel && exists(rel)) {
      listed[key] = rel;
    }
  }
  const manifest = {
    version: 1,
    pr_url: prUrl,
    pr,
    sha,
    status,
    summary,
    plan_source: planSource,
    preview: { app, api: api || null, observed_sha: observedSha },
    started_at: new Date(startedAt).toISOString(),
    finished_at: new Date(finishedAt).toISOString(),
    duration_s: Math.round((finishedAt - startedAt) / 100) / 10,
    console_errors: consoleErrors,
    steps: normalized,
    artifacts: listed,
  };
  return manifest;
}

/**
 * Compare the sha a preview serves at ``/__factory/preview`` with the expected head.
 * @returns {{ok: boolean, observed: string|null, reason: string}}
 */
export function checkPreviewStamp(stamp, sha) {
  if (!stamp || typeof stamp !== 'object') {
    return { ok: false, observed: null, reason: 'the preview serves no /__factory/preview stamp' };
  }
  const observed = typeof stamp.sha === 'string' ? stamp.sha.trim().toLowerCase() : '';
  if (!observed) {
    return { ok: false, observed: null, reason: '/__factory/preview has no sha' };
  }
  const wanted = String(sha).toLowerCase();
  const ok = observed.length >= 7 && (observed.startsWith(wanted) || wanted.startsWith(observed));
  return {
    ok,
    observed,
    reason: ok ? 'the preview serves the head' : `the preview serves ${sha7(observed)}, not ${sha7(wanted)}`,
  };
}

/** Browser directories (Playwright's naming) a headless run with video needs. */
export function requiredBrowserDirs(browsersJson) {
  const wanted = ['chromium-headless-shell', 'ffmpeg'];
  const browsers = Array.isArray(browsersJson?.browsers) ? browsersJson.browsers : [];
  return wanted.map((name) => {
    const entry = browsers.find((b) => b?.name === name);
    if (!entry?.revision) {
      throw new Error(`playwright-core browsers.json has no ${name} revision`);
    }
    return `${name.replace(/-/g, '_')}-${entry.revision}`;
  });
}

/**
 * Where the harness's Playwright finds its browsers.
 *
 * An explicit ``PLAYWRIGHT_BROWSERS_PATH`` wins; else the sandbox image's baked
 * directory when it holds this Playwright's revisions; else Playwright's
 * default cache (``path: null``), which ``playwright install chromium`` fills.
 * @returns {{path: string|null, source: 'env'|'baked'|'default', missing: string[]}}
 */
export function selectBrowsersPath({
  harnessDir,
  env = process.env,
  baked = BAKED_BROWSERS_PATH,
  exists = existsSync,
  readFile = (p) => readFileSync(p, 'utf8'),
} = {}) {
  if (env.PLAYWRIGHT_BROWSERS_PATH) {
    return { path: env.PLAYWRIGHT_BROWSERS_PATH, source: 'env', missing: [] };
  }
  const browsersJson = JSON.parse(readFile(join(harnessDir, 'node_modules', 'playwright-core', 'browsers.json')));
  const missing = requiredBrowserDirs(browsersJson).filter((dir) => !exists(join(baked, dir)));
  if (missing.length === 0) {
    return { path: baked, source: 'baked', missing };
  }
  return { path: null, source: 'default', missing };
}
