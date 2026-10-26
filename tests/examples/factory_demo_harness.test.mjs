// Browser-free tests of the factory demo harness (run by test_factory_demo_harness.py).
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { after, describe, test } from 'node:test';
import { fileURLToPath } from 'node:url';

const HARNESS = join(
  dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
  'examples',
  'factory',
  'factory-pl',
  'agents',
  'demo',
  'skills',
  'factory-demo',
  'harness',
);
const lib = await import(join(HARNESS, 'lib.mjs'));
const { startFixtureServer } = await import(join(HARNESS, 'selftest.mjs'));

const SHA = 'a'.repeat(40);
const OTHER = 'b'.repeat(40);

describe('exit codes', () => {
  test('PASS and PARTIAL succeed, FAIL is 1, ERROR is 2, stale is 3', () => {
    assert.equal(lib.exitCodeFor('PASS'), 0);
    assert.equal(lib.exitCodeFor('PARTIAL'), 0);
    assert.equal(lib.exitCodeFor('FAIL'), 1);
    assert.equal(lib.exitCodeFor('ERROR'), 2);
    assert.equal(lib.exitCodeFor('BOGUS'), 2);
    assert.equal(lib.EXIT.STALE, 3);
  });
});

describe('deriveStatus', () => {
  const pass = { n: 1, title: 'Open', kind: 'ui', status: 'pass' };
  const skipped = { n: 2, title: 'Build', kind: 'ui', status: 'skipped', note: 'needs build tier' };
  const failed = { n: 2, title: 'Settings', kind: 'ui', status: 'fail' };

  test('a harness error or no test run is ERROR', () => {
    assert.equal(lib.deriveStatus({ steps: [pass], error: 'browser crashed' }).status, 'ERROR');
    assert.equal(lib.deriveStatus({ steps: [], testsRan: false }).status, 'ERROR');
    assert.equal(lib.deriveStatus({ steps: [], testFailed: true }).status, 'ERROR');
  });
  test('a failed step, or a failure outside the steps, is FAIL', () => {
    assert.deepEqual(lib.deriveStatus({ steps: [pass, failed], testFailed: true }), {
      status: 'FAIL',
      reason: 'step 2 failed: Settings',
    });
    assert.equal(lib.deriveStatus({ steps: [pass], testFailed: true }).status, 'FAIL');
  });
  test('skips make it PARTIAL, otherwise PASS', () => {
    assert.equal(lib.deriveStatus({ steps: [pass, skipped] }).status, 'PARTIAL');
    assert.equal(lib.deriveStatus({ steps: [pass] }).status, 'PASS');
  });
  test('the default summary names the failure or the skips', () => {
    assert.match(lib.defaultSummary([pass, { ...failed, note: 'Expected: "x"' }], 'FAIL'), /^Step 2 failed: Settings/);
    assert.equal(lib.defaultSummary([pass, skipped], 'PARTIAL'), 'Open. Skipped: Build — needs build tier');
    assert.match(lib.defaultSummary([], 'ERROR', 'no steps'), /could not run: no steps/);
  });
});

describe('buildManifest', () => {
  const base = {
    prUrl: 'https://github.com/caffeinelabs/app/pull/9630',
    pr: 9630,
    sha: SHA,
    summary: 'Landing renders',
    app: 'https://app-x-p9630.envs.sandbox.caffeinelabs.io',
    api: 'https://api-x-p9630.envs.sandbox.caffeinelabs.io',
    observedSha: SHA,
    startedAt: Date.parse('2026-10-26T10:00:00Z'),
    finishedAt: Date.parse('2026-10-26T10:00:41.25Z'),
  };

  test('writes the manifest the runner and the cockpit read', () => {
    const manifest = lib.buildManifest({
      ...base,
      status: 'PARTIAL',
      planSource: 'pr-body',
      consoleErrors: 2,
      steps: [
        { n: 1, title: 'Open landing', kind: 'ui', status: 'pass', screenshot: 'shots/01-open-landing.png', duration_ms: 5120 },
        { n: 2, title: 'GET /version', kind: 'api', status: 'pass', api_calls: [0, 'x'] },
        { title: 'Odd', kind: 'video', status: 'maybe', note: 'n'.repeat(400) },
      ],
      artifacts: { video: 'video/demo.webm', poster: 'shots/01-open-landing.png', trace: 'trace.zip' },
      exists: (rel) => rel !== 'trace.zip',
    });
    assert.equal(manifest.version, 1);
    assert.equal(manifest.status, 'PARTIAL');
    assert.equal(manifest.pr_url, base.prUrl);
    assert.equal(manifest.sha, SHA);
    assert.equal(manifest.plan_source, 'pr-body');
    assert.deepEqual(manifest.preview, { app: base.app, api: base.api, observed_sha: SHA });
    assert.equal(manifest.duration_s, 41.3);
    assert.equal(manifest.console_errors, 2);
    assert.equal(manifest.started_at, '2026-10-26T10:00:00.000Z');
    assert.deepEqual(manifest.artifacts, { video: 'video/demo.webm', poster: 'shots/01-open-landing.png' });
    assert.deepEqual(manifest.steps[0], {
      n: 1,
      title: 'Open landing',
      kind: 'ui',
      status: 'pass',
      screenshot: 'shots/01-open-landing.png',
      duration_ms: 5120,
    });
    assert.deepEqual(manifest.steps[1].api_calls, [0]);
    assert.equal(manifest.steps[2].n, 3);
    assert.equal(manifest.steps[2].kind, 'check');
    assert.equal(manifest.steps[2].status, 'fail');
    assert.equal(manifest.steps[2].note.length, 300);
  });
  test('refuses a status the server does not know', () => {
    assert.throws(() => lib.buildManifest({ ...base, status: 'BLOCKED' }), /status must be one of/);
  });
});

describe('redaction', () => {
  const har = {
    log: {
      entries: [
        {
          request: {
            method: 'POST',
            url: 'https://api.example/auth/synthetic-session?token=abc&x-amz-signature=sig&page=2',
            headers: [
              { name: 'Authorization', value: 'Bearer s3cret' },
              { name: 'Cookie', value: 'access_token=t' },
              { name: 'Accept', value: 'application/json' },
            ],
            cookies: [{ name: 'access_token', value: 't' }],
            queryString: [
              { name: 'token', value: 'abc' },
              { name: 'page', value: '2' },
            ],
            postData: {
              mimeType: 'application/json',
              text: '{"user":{"password":"pw","name":"n"},"refreshToken":"r"}',
              params: [{ name: 'api_key', value: 'k' }],
            },
          },
          response: {
            status: 200,
            headers: [
              { name: 'set-cookie', value: 'refresh_token=r' },
              { name: 'X-Factory-Commit', value: SHA },
            ],
            cookies: [{ name: 'refresh_token', value: 'r' }],
            redirectURL: 'https://app.example/cb?code=xyz',
          },
        },
      ],
    },
  };

  test('redactHar strips credentials and keeps the rest', () => {
    const out = lib.redactHar(har);
    const [entry] = out.log.entries;
    const text = JSON.stringify(out);
    for (const secret of ['s3cret', 'access_token=t', '"pw"', 'refresh_token=r', '"abc"', 'xyz', '"sig"', '"k"']) {
      assert.ok(!text.includes(secret), `${secret} leaked`);
    }
    assert.equal(entry.request.headers[2].value, 'application/json');
    assert.equal(entry.response.headers[1].value, SHA);
    assert.equal(entry.request.queryString[1].value, '2');
    assert.match(entry.request.url, /page=2/);
    assert.deepEqual(JSON.parse(entry.request.postData.text).user, { password: '[redacted]', name: 'n' });
    assert.equal(entry.response.cookies[0].value, '[redacted]');
    assert.equal(har.log.entries[0].request.headers[0].value, 'Bearer s3cret', 'the input is not mutated');
  });
  test('redactHar tolerates a HAR without entries', () => {
    assert.deepEqual(lib.redactHar({}), {});
    assert.deepEqual(lib.redactHar(null), {});
  });
  test('redactHeaders handles maps and lists, case-insensitively', () => {
    assert.deepEqual(lib.redactHeaders({ AUTHORIZATION: 'x', 'content-type': 'json' }), {
      AUTHORIZATION: '[redacted]',
      'content-type': 'json',
    });
    assert.deepEqual(lib.redactHeaders([{ name: 'Set-Cookie', value: 'a=b' }]), [
      { name: 'Set-Cookie', value: '[redacted]' },
    ]);
  });
  test('redactJsonText leaves non-JSON alone', () => {
    assert.equal(lib.redactJsonText('plain text token=1'), 'plain text token=1');
    assert.equal(lib.redactJsonText('[{"apiKey":"k"}]'), '[{"apiKey":"[redacted]"}]');
  });
  test('truncate cuts at the byte limit', () => {
    assert.deepEqual(lib.truncate('abc', 8), { text: 'abc', bytes: 3, truncated: false });
    const cut = lib.truncate('x'.repeat(9000));
    assert.equal(cut.text.length, 8192);
    assert.equal(cut.bytes, 9000);
    assert.equal(cut.truncated, true);
  });
});

describe('helpers', () => {
  test('checkPreviewStamp compares the served sha with the head', () => {
    assert.equal(lib.checkPreviewStamp({ sha: SHA }, SHA).ok, true);
    assert.equal(lib.checkPreviewStamp({ sha: SHA.slice(0, 7) }, SHA).ok, true);
    const stale = lib.checkPreviewStamp({ sha: OTHER }, SHA);
    assert.equal(stale.ok, false);
    assert.equal(stale.observed, OTHER);
    assert.match(stale.reason, /serves bbbbbbb, not aaaaaaa/);
    assert.equal(lib.checkPreviewStamp(null, SHA).ok, false);
    assert.equal(lib.checkPreviewStamp({ repo: 'x' }, SHA).ok, false);
  });
  test('names, numbers and notes', () => {
    assert.equal(lib.screenshotName(3, 'Settings → About shows the API version!'), 'shots/03-settings-about-shows-the-api-version.png');
    assert.equal(lib.screenshotName(12, '', 'failed'), 'shots/12-step-failed.png');
    assert.equal(lib.prNumber('https://github.com/caffeinelabs/app/pull/9630'), 9630);
    assert.equal(lib.prNumber('#42'), 42);
    assert.equal(lib.prNumber('nope'), null);
    assert.equal(lib.watermark(9630, SHA), 'PR #9630 · aaaaaaa · preview');
    assert.equal(
      lib.errorNote('\u001b[31mexpect(locator).toHaveText(expected) failed\u001b[39m\n\nLocator: x\nExpected: "0000000"\nReceived: "7767f0d"\n'),
      'expect(locator).toHaveText(expected) failed · Expected: "0000000" · Received: "7767f0d"',
    );
  });
  test('selectBrowsersPath prefers env, then the baked revision, then the default cache', () => {
    const browsersJson = JSON.stringify({
      browsers: [
        { name: 'chromium', revision: '1194' },
        { name: 'chromium-headless-shell', revision: '1194' },
        { name: 'ffmpeg', revision: '1011' },
      ],
    });
    const readFile = () => browsersJson;
    assert.deepEqual(lib.requiredBrowserDirs(JSON.parse(browsersJson)), ['chromium_headless_shell-1194', 'ffmpeg-1011']);
    const baked = (dirs) => (path) => dirs.some((d) => path === join('/opt/ms-playwright', d));
    assert.equal(
      lib.selectBrowsersPath({ harnessDir: '/h', env: { PLAYWRIGHT_BROWSERS_PATH: '/x' }, readFile }).source,
      'env',
    );
    const hit = lib.selectBrowsersPath({
      harnessDir: '/h',
      env: {},
      readFile,
      exists: baked(['chromium_headless_shell-1194', 'ffmpeg-1011']),
    });
    assert.deepEqual(hit, { path: '/opt/ms-playwright', source: 'baked', missing: [] });
    const miss = lib.selectBrowsersPath({
      harnessDir: '/h',
      env: {},
      readFile,
      exists: baked(['chromium_headless_shell-1180']),
    });
    assert.equal(miss.path, null);
    assert.deepEqual(miss.missing, ['chromium_headless_shell-1194', 'ffmpeg-1011']);
  });
});

function runCli(args) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [join(HARNESS, 'run.mjs'), ...args], { stdio: ['ignore', 'pipe', 'pipe'] });
    let output = '';
    child.stdout.on('data', (chunk) => (output += chunk));
    child.stderr.on('data', (chunk) => (output += chunk));
    child.on('close', (code) => resolve({ code, output }));
  });
}

describe('run.mjs before the browser starts', () => {
  const root = mkdtempSync(join(tmpdir(), 'factory-demo-harness-test-'));
  const spec = join(root, 'demo.spec.ts');
  writeFileSync(spec, "import { demo } from 'factory-demo';\n");
  after(() => rmSync(root, { recursive: true, force: true }));

  test('bad arguments exit 2 (ERROR)', async () => {
    const { code, output } = await runCli(['--spec', spec, '--out', join(root, 'x'), '--app', 'http://127.0.0.1:9', '--pr', '1', '--sha', 'abc']);
    assert.equal(code, 2);
    assert.match(output, /--sha must be the full 40-character head sha/);
  });
  test('a preview that serves another commit exits 3 and writes no demo.json', async () => {
    const server = await startFixtureServer({ sha: OTHER, secret: 'unused' });
    try {
      const out = join(root, 'stale');
      const { code, output } = await runCli([
        '--spec', spec, '--out', out, '--app', server.url, '--api', server.url,
        '--pr', 'https://github.com/caffeinelabs/app/pull/9630', '--sha', SHA,
      ]);
      assert.equal(code, 3, output);
      assert.match(output, /DEMO BLOCKED stale-preview pr=9630 sha=aaaaaaa · the preview serves bbbbbbb/);
      assert.ok(!existsSync(join(out, 'demo.json')));
      assert.ok(existsSync(join(out, 'run.log')));
    } finally {
      await server.close();
    }
  });
});
