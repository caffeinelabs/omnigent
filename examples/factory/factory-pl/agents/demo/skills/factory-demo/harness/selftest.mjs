// `node run.mjs --selftest`: record demos of a local fixture preview and check the outputs.
// Covers a PARTIAL run (exit 0), a FAIL run (exit 1) and a stale preview (exit 3).

import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const PAGE = `<!doctype html>
<html><head><title>Factory demo fixture</title></head>
<body style="font-family:system-ui;margin:40px">
<h1>Factory demo fixture</h1>
<button id="show">Show version</button>
<p id="version"></p><p id="user"></p>
<script>
  document.getElementById('show').onclick = async () => {
    const res = await fetch('/version');
    document.getElementById('version').textContent = (await res.json()).gitSha.slice(0, 7);
  };
  fetch('/me').then((r) => (r.ok ? r.json() : null)).then((me) => {
    if (me) document.getElementById('user').textContent = 'Signed in as ' + me.userId;
  });
  console.error('fixture: expected console error');
</script>
</body></html>`;

/**
 * A preview stand-in: the page, `/__factory/preview`, `/version`, `/ready` and
 * the synthetic session route, on 127.0.0.1.
 * @returns {Promise<{url: string, close: () => Promise<void>}>}
 */
export function startFixtureServer({ sha, pr = 1, secret }) {
  const stamp = {
    repo: 'caffeinelabs/app',
    pr,
    sha,
    short: sha.slice(0, 7),
    deployed_at: new Date().toISOString(),
    env_id: 'selftest',
  };
  const server = createServer((req, res) => {
    const json = (status, body, headers = {}) => {
      res.writeHead(status, { 'content-type': 'application/json', 'x-factory-commit': sha, ...headers });
      res.end(JSON.stringify(body));
    };
    const path = new URL(req.url, 'http://fixture').pathname;
    if (req.method === 'GET' && path === '/') {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'x-factory-commit': sha });
      res.end(PAGE);
    } else if (path === '/__factory/preview') {
      json(200, stamp);
    } else if (path === '/version') {
      json(200, { service: 'fixture', gitSha: sha });
    } else if (path === '/ready') {
      json(200, { ok: true });
    } else if (req.method === 'POST' && path === '/auth/synthetic-session') {
      if (req.headers.authorization !== `Bearer ${secret}`) {
        json(401, { error: 'unauthorized' });
        return;
      }
      json(
        200,
        { user: { userId: 'selftest-user' } },
        {
          'set-cookie': [
            'access_token=selftest-access; Path=/; HttpOnly; SameSite=Lax',
            'refresh_token=selftest-refresh; Path=/; HttpOnly; SameSite=Lax',
          ],
        },
      );
    } else if (path === '/me') {
      const signedIn = /(^|;\s*)access_token=selftest-access/.test(req.headers.cookie || '');
      json(signedIn ? 200 : 401, signedIn ? { userId: 'selftest-user' } : { error: 'signed out' });
    } else {
      json(404, { error: 'not found' });
    }
  });
  return new Promise((resolvePromise) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolvePromise({
        url: `http://127.0.0.1:${port}`,
        close: () => new Promise((done) => server.close(() => done())),
      });
    });
  });
}

const PASS_SPEC = `import { demo, expect, preview, step } from 'factory-demo';

demo('Selftest: the fixture shows the deployed version', async ({ ui, api, login }) => {
  await step.check('Sign in with a synthetic session', login);
  await step('Open the landing page', async () => {
    await ui.goto('/');
    await ui.see(ui.page.getByRole('heading', { name: 'Factory demo fixture' }));
  });
  await step.api('GET /version returns the deployed sha', async () => {
    const res = await api.get('/version');
    expect(res.status()).toBe(200);
    expect((await res.json()).gitSha).toBe(preview.sha);
  });
  await step('Show the version while signed in', async () => {
    await ui.click(ui.page.getByRole('button', { name: 'Show version' }));
    await expect(ui.page.locator('#version')).toHaveText(preview.sha7);
    await expect(ui.page.locator('#user')).toHaveText('Signed in as selftest-user');
  });
  await step.skip('Draft build flow', 'needs the build tier (not in factory envs)');
});
`;

const FAIL_SPEC = `import { demo, expect, step } from 'factory-demo';

demo('Selftest: a product assertion fails', async ({ ui }) => {
  await step('Open the landing page', async () => {
    await ui.goto('/');
  });
  await step('The version shows 0000000', async () => {
    await ui.click(ui.page.getByRole('button', { name: 'Show version' }));
    await expect(ui.page.locator('#version')).toHaveText('0000000', { timeout: 3000 });
  });
});
`;

// Async, so the fixture server in this process keeps answering while run.mjs works.
function run(harnessDir, args) {
  return new Promise((resolvePromise) => {
    const child = spawn(process.execPath, [join(harnessDir, 'run.mjs'), ...args], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let output = '';
    child.stdout.on('data', (chunk) => (output += chunk));
    child.stderr.on('data', (chunk) => (output += chunk));
    child.on('close', (code) => resolvePromise({ code, output }));
  });
}

function readManifest(dir) {
  return JSON.parse(readFileSync(join(dir, 'demo.json'), 'utf8'));
}

function check(results, name, ok, detail = '') {
  results.push({ name, ok, detail });
}

export async function selftest({ harnessDir, keep = false }) {
  const sha = randomBytes(20).toString('hex');
  const secret = randomBytes(32).toString('hex');
  const root = mkdtempSync(join(tmpdir(), 'factory-demo-selftest-'));
  const secretFile = join(root, 'synthetic-secret');
  writeFileSync(secretFile, `${secret}\n`, { mode: 0o600 });
  const server = await startFixtureServer({ sha, secret });
  const base = ['--app', server.url, '--api', server.url, '--pr', 'https://github.com/caffeinelabs/app/pull/1'];
  const results = [];
  try {
    const pass = join(root, 'pass');
    writeFileSync(join(root, 'pass.spec.ts'), PASS_SPEC);
    const passRun = await run(harnessDir, [
      ...base,
      '--spec',
      join(root, 'pass.spec.ts'),
      '--out',
      pass,
      '--sha',
      sha,
      '--synthetic-secret-file',
      secretFile,
    ]);
    check(results, 'PARTIAL run exits 0', passRun.code === 0, passRun.output);
    if (existsSync(join(pass, 'demo.json'))) {
      const manifest = readManifest(pass);
      check(results, 'PARTIAL status', manifest.status === 'PARTIAL', manifest.status);
      check(results, '5 steps (4 run, 1 skipped)', manifest.steps.length === 5, JSON.stringify(manifest.steps));
      check(results, 'video recorded', existsSync(join(pass, 'video', 'demo.webm')));
      const shots = existsSync(join(pass, 'shots')) ? readdirSync(join(pass, 'shots')) : [];
      check(results, 'two UI screenshots', shots.length === 2, shots.join(', '));
      check(
        results,
        'poster is the first screenshot',
        manifest.artifacts.poster === 'shots/02-open-the-landing-page.png',
        manifest.artifacts.poster,
      );
      const calls = JSON.parse(readFileSync(join(pass, 'api.json'), 'utf8'));
      check(results, 'api.json has GET /version', calls[0]?.method === 'GET' && calls[0]?.status === 200);
      const har = readFileSync(join(pass, 'network.har'), 'utf8');
      check(results, 'HAR has no secret or session cookie', !har.includes(secret) && !har.includes('selftest-access'));
      check(results, 'console error captured', manifest.console_errors >= 1, String(manifest.console_errors));
      check(results, 'observed sha recorded', manifest.preview.observed_sha === sha);
    } else {
      check(results, 'PARTIAL run wrote demo.json', false);
    }

    const fail = join(root, 'fail');
    writeFileSync(join(root, 'fail.spec.ts'), FAIL_SPEC);
    const failRun = await run(harnessDir, [...base, '--spec', join(root, 'fail.spec.ts'), '--out', fail, '--sha', sha]);
    check(results, 'FAIL run exits 1', failRun.code === 1, failRun.output);
    if (existsSync(join(fail, 'demo.json'))) {
      const manifest = readManifest(fail);
      check(results, 'FAIL status', manifest.status === 'FAIL', manifest.status);
      check(
        results,
        'failure screenshot',
        manifest.steps[1]?.screenshot === 'shots/02-the-version-shows-0000000-failed.png',
        manifest.steps[1]?.screenshot,
      );
      check(results, 'trace recorded', existsSync(join(fail, 'trace.zip')));
    } else {
      check(results, 'FAIL run wrote demo.json', false);
    }

    const stale = join(root, 'stale');
    const staleRun = await run(harnessDir, [
      ...base,
      '--spec',
      join(root, 'pass.spec.ts'),
      '--out',
      stale,
      '--sha',
      randomBytes(20).toString('hex'),
    ]);
    check(results, 'stale preview exits 3', staleRun.code === 3, staleRun.output);
    check(results, 'stale preview writes no demo.json', !existsSync(join(stale, 'demo.json')));
  } finally {
    await server.close();
  }
  for (const r of results) {
    console.log(
      `${r.ok ? 'ok  ' : 'FAIL'} ${r.name}${r.ok || !r.detail ? '' : `\n     ${r.detail.trim().split('\n').slice(-15).join('\n     ')}`}`,
    );
  }
  const ok = results.every((r) => r.ok);
  console.log(ok ? `SELFTEST OK (${results.length} checks)` : 'SELFTEST FAILED');
  if (keep || !ok) console.log(`outputs: ${root}`);
  else rmSync(root, { recursive: true, force: true });
  return ok ? 0 : 1;
}
