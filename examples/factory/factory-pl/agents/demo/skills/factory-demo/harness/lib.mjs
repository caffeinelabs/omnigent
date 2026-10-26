// Pure helpers shared by run.mjs, explore.mjs and fixtures.ts.
// Node built-ins only, so they are tested without a browser.

import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

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
