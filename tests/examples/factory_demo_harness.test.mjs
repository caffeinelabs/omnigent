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

describe('scrubbing published files', () => {
  const SECRET = 'f'.repeat(64);
  const TOKENS = ['selftest-access', 'selftest-refresh'];
  const KNOWN = [SECRET, ...TOKENS];
  const leaks = (text) => KNOWN.filter((k) => text.includes(k));

  test('scrubText removes known secrets, auth header lines, bearer tokens and token cookies', () => {
    const log = [
      `apiRequestContext.get: Timeout 1000ms exceeded.`,
      `  - → GET http://127.0.0.1:1/me`,
      `  -   cookie: access_token=selftest-access; refresh_token=selftest-refresh`,
      `  -   Authorization: Bearer ${SECRET}`,
      `  - ← 200 OK set-cookie: access_token=rotated123; Path=/`,
      `a URL https://x/cb?access_token=zzzzzzzz&page=2 and the secret ${SECRET} in prose`,
    ].join('\n');
    const out = lib.scrubText(log, KNOWN);
    assert.deepEqual(leaks(out), []);
    assert.ok(!out.includes('rotated123') && !out.includes('zzzzzzzz'));
    assert.match(out, /cookie: \[redacted\]/);
    assert.match(out, /page=2/);
    assert.match(out, /GET http:\/\/127\.0\.0\.1:1\/me/);
    assert.equal(lib.scrubText('short secret abc stays', ['abc']), 'short secret abc stays');
    assert.equal(lib.scrubText('{"cookie": "x"}'), '{"cookie": "x"}', 'JSON keys are left to the structural pass');
  });

  test('a trace keeps its events and loses every credential', () => {
    const traceLines = [
      { type: 'before', class: 'APIRequestContext', method: 'fetch', params: { url: 'http://h/auth/synthetic-session', headers: [{ name: 'Authorization', value: `Bearer ${SECRET}` }] } },
      { type: 'log', message: '  cookie: access_token=selftest-access; refresh_token=selftest-refresh' },
      { type: 'before', class: 'BrowserContext', method: 'addCookies', params: { cookies: [{ name: 'access_token', value: 'selftest-access', domain: 'h' }] } },
      { type: 'after', result: { response: { status: 200, headers: [{ name: 'set-cookie', value: 'access_token=rotated123; Path=/' }, { name: 'x-factory-commit', value: SHA }] } } },
    ];
    const network = {
      type: 'resource-snapshot',
      snapshot: {
        request: {
          url: 'http://h/me',
          cookies: [{ name: 'refresh_token', value: 'selftest-refresh' }],
          headers: [{ name: 'Cookie', value: 'access_token=selftest-access' }, { name: 'Accept', value: '*/*' }],
          postData: { text: '{"password":"hunter22","name":"n"}' },
        },
        response: { status: 200, headers: { 'set-cookie': 'refresh_token=rotated456', 'content-type': 'application/json' } },
      },
    };
    const trace = Buffer.from(`${traceLines.map((l) => JSON.stringify(l)).join('\n')}\nnot json Bearer ${SECRET}\n`);
    const scrubbed = lib.scrubTraceEntry('0-trace.trace', trace, KNOWN).toString('utf8');
    assert.deepEqual(leaks(scrubbed), []);
    assert.ok(!scrubbed.includes('rotated123'));
    const events = scrubbed.trim().split('\n').slice(0, 4).map((l) => JSON.parse(l));
    assert.equal(events[0].params.url, 'http://h/auth/synthetic-session');
    assert.equal(events[2].params.cookies[0].domain, 'h');
    assert.equal(events[3].result.response.headers[1].value, SHA);
    const net = JSON.parse(lib.scrubTraceEntry('0-trace.network', Buffer.from(JSON.stringify(network)), KNOWN).toString());
    assert.equal(net.snapshot.request.headers[1].value, '*/*');
    assert.equal(net.snapshot.request.cookies[0].value, '[redacted]');
    assert.equal(net.snapshot.response.headers['set-cookie'], '[redacted]');
    assert.equal(net.snapshot.response.headers['content-type'], 'application/json');
    assert.deepEqual(JSON.parse(net.snapshot.request.postData.text), { password: '[redacted]', name: 'n' });
    const binary = Buffer.concat([Buffer.from([0xff, 0xd8, 0x00]), Buffer.from(SECRET), Buffer.from([0x00, 0xfe])]);
    const cleaned = lib.scrubTraceEntry('resources/abc', binary, KNOWN);
    assert.ok(!cleaned.includes(SECRET));
    assert.deepEqual([...cleaned.subarray(0, 3)], [0xff, 0xd8, 0x00]);
  });

  test('zip round trip, and scrubTraceZip rewrites every entry', () => {
    assert.equal(lib.crc32(Buffer.from('123456789')), 0xcbf43926);
    const entries = [
      { name: 'test.trace', data: Buffer.from(`{"type":"log","message":"Authorization: Bearer ${SECRET}"}\n`) },
      { name: 'resources/page.jpeg', data: Buffer.from([1, 2, 3, 255]) },
      { name: 'resources/x.json', data: Buffer.from('{"session":"selftest-access"}') },
    ];
    const roundTrip = lib.readZip(lib.writeZip(entries));
    assert.deepEqual(roundTrip.map((e) => e.name), entries.map((e) => e.name));
    assert.ok(roundTrip.every((e, i) => e.data.equals(entries[i].data)));
    const scrubbed = lib.readZip(lib.scrubTraceZip(lib.writeZip(entries), KNOWN));
    assert.deepEqual(scrubbed.map((e) => e.name), entries.map((e) => e.name));
    assert.deepEqual(leaks(scrubbed.map((e) => e.data.toString('latin1')).join('\n')), []);
    assert.ok(scrubbed[1].data.equals(entries[1].data));
    assert.throws(() => lib.readZip(Buffer.from('not a zip at all, definitely not')), /not a zip archive/);
  });

  test('syntheticSession posts the secret with fetch and reads the session cookies', async () => {
    const calls = [];
    const fake = (headers, status = 200) => async (url, init) => {
      calls.push({ url, init });
      return new Response(JSON.stringify({ user: { userId: 'u1' } }), { status, headers });
    };
    const cookies = new Headers();
    cookies.append('set-cookie', 'access_token=A1234567; Path=/; HttpOnly');
    cookies.append('set-cookie', 'refresh_token=R1234567; Path=/; HttpOnly');
    const session = await lib.syntheticSession('https://api.x/', SECRET, { fetchImpl: fake(cookies) });
    assert.deepEqual(session, { userId: 'u1', accessToken: 'A1234567', refreshToken: 'R1234567' });
    assert.equal(calls[0].url, 'https://api.x/auth/synthetic-session');
    assert.equal(calls[0].init.method, 'POST');
    assert.equal(calls[0].init.headers.Authorization, `Bearer ${SECRET}`);
    await assert.rejects(lib.syntheticSession('https://api.x', SECRET, { fetchImpl: fake(new Headers(), 404) }), /404: the env has no SYNTHETIC_SESSION_SECRET/);
    await assert.rejects(lib.syntheticSession('https://api.x', SECRET, { fetchImpl: fake(new Headers()) }), /no access_token/);
  });

  test('sessionCookies covers the app and API hosts', () => {
    const tokens = { accessToken: 'a', refreshToken: 'r' };
    const cross = lib.sessionCookies('https://app-x.envs.example', 'https://api-x.envs.example', tokens);
    assert.equal(cross.length, 4);
    assert.deepEqual(new Set(cross.map((c) => `${c.domain}:${c.name}:${c.sameSite}`)), new Set([
      'app-x.envs.example:access_token:Lax',
      'app-x.envs.example:refresh_token:Lax',
      'api-x.envs.example:access_token:None',
      'api-x.envs.example:refresh_token:None',
    ]));
    assert.ok(cross.every((c) => c.secure && c.httpOnly && c.path === '/'));
    const local = lib.sessionCookies('http://127.0.0.1:1', 'http://127.0.0.1:1', tokens);
    assert.ok(local.every((c) => c.sameSite === 'Lax' && !c.secure));
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
