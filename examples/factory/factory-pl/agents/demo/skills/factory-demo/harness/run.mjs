#!/usr/bin/env node
// Record one demo of a PR head against its factory preview and write demo.json.
//
//   node run.mjs --spec <dir>/demo.spec.ts --out <dir> --app <url> --api <url> \
//     --pr <pr url> --sha <40-char head> [--synthetic-secret-file <path>] [--summary <text>]
//
// Exit codes: 0 PASS or PARTIAL, 1 FAIL, 2 ERROR (harness, browser or spec), 3 stale preview.
// `node run.mjs --selftest` records demos of a local fixture page (no network needed but npm ci).

import { spawn, spawnSync } from 'node:child_process';
import {
  appendFileSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { join, relative, resolve } from 'node:path';
import { parseArgs } from 'node:util';
import {
  EXIT,
  buildManifest,
  checkPreviewStamp,
  consoleErrorCount,
  defaultSummary,
  deriveStatus,
  exitCodeFor,
  firstLine,
  isFullSha,
  prNumber,
  redactHar,
  sha7,
} from './lib.mjs';
import { HARNESS, PLAYWRIGHT_CLI, browserEnv, ensureDeps, runLogged } from './setup.mjs';
const USAGE =
  'usage: node run.mjs --spec <demo.spec.ts> --out <dir> --app <url> --api <url> --pr <pr url> ' +
  '--sha <40-char sha> [--synthetic-secret-file <path>] [--summary <text>] [--plan-source pr-body|derived] ' +
  '[--tilt-snapshot <path>] [--skip-stale-check] [--mp4]  |  node run.mjs --selftest [--keep]';
// Outputs of an earlier run in the same directory; the spec itself is kept.
const OUTPUTS = [
  'demo.json',
  'api.json',
  'console.json',
  'network.har',
  'trace.zip',
  'run.log',
  'pr-comment.md',
  'poster.png',
  'video',
  'shots',
  '.harness',
];

class UsageError extends Error {}

function parse(argv) {
  const { values } = parseArgs({
    args: argv,
    options: {
      spec: { type: 'string' },
      out: { type: 'string' },
      app: { type: 'string' },
      api: { type: 'string' },
      pr: { type: 'string' },
      sha: { type: 'string' },
      'synthetic-secret-file': { type: 'string' },
      summary: { type: 'string' },
      'plan-source': { type: 'string' },
      'tilt-snapshot': { type: 'string' },
      'skip-stale-check': { type: 'boolean', default: false },
      mp4: { type: 'boolean', default: false },
      selftest: { type: 'boolean', default: false },
      keep: { type: 'boolean', default: false },
      help: { type: 'boolean', default: false },
    },
    strict: true,
  });
  return values;
}

function expandHome(path) {
  return path?.startsWith('~/') ? join(homedir(), path.slice(2)) : path;
}

function httpUrl(value, name) {
  try {
    const url = new URL(value);
    if (url.protocol === 'http:' || url.protocol === 'https:') return url.toString().replace(/\/+$/, '');
  } catch {
    // reported below
  }
  throw new UsageError(`--${name} must be an http(s) URL, got ${JSON.stringify(value ?? '')}`);
}

function validate(values) {
  for (const name of ['spec', 'out', 'app', 'pr', 'sha']) {
    if (!values[name]) throw new UsageError(`--${name} is required`);
  }
  const sha = values.sha.trim().toLowerCase();
  if (!isFullSha(sha)) throw new UsageError('--sha must be the full 40-character head sha');
  const pr = prNumber(values.pr);
  if (!pr) throw new UsageError('--pr must be a GitHub PR URL (or number)');
  const spec = resolve(expandHome(values.spec));
  if (!existsSync(spec)) throw new UsageError(`--spec ${spec} does not exist`);
  const out = resolve(expandHome(values.out));
  if (out === HARNESS || out === resolve('/') || out === homedir()) {
    throw new UsageError(`--out ${out} must be a directory of its own`);
  }
  const secretFile = values['synthetic-secret-file'] ? resolve(expandHome(values['synthetic-secret-file'])) : '';
  if (secretFile && !existsSync(secretFile))
    throw new UsageError(`--synthetic-secret-file ${secretFile} does not exist`);
  const planSource = values['plan-source'] || null;
  if (planSource && !['pr-body', 'derived'].includes(planSource)) {
    throw new UsageError('--plan-source is pr-body or derived');
  }
  const prUrl = /^https?:\/\//.test(values.pr) ? values.pr.trim() : `https://github.com/caffeinelabs/app/pull/${pr}`;
  const tiltDefault = join(homedir(), 'factory', 'previews', `p${pr}`, 'tilt-snapshot.json');
  return {
    spec,
    out,
    app: httpUrl(values.app, 'app'),
    api: values.api ? httpUrl(values.api, 'api') : '',
    pr,
    prUrl,
    sha,
    secretFile,
    summary: values.summary?.trim() || '',
    planSource,
    tiltSnapshot: values['tilt-snapshot'] ? resolve(expandHome(values['tilt-snapshot'])) : tiltDefault,
    skipStaleCheck: values['skip-stale-check'],
    mp4: values.mp4,
  };
}

function prepareOut(out) {
  mkdirSync(out, { recursive: true });
  for (const name of OUTPUTS) rmSync(join(out, name), { recursive: true, force: true });
  mkdirSync(join(out, '.harness'), { recursive: true });
}

function makeLog(path) {
  return (line) => {
    const text = `[${new Date().toISOString()}] ${line}\n`;
    appendFileSync(path, text);
  };
}

async function fetchJson(url, { tries = 3, timeoutMs = 5000 } = {}) {
  for (let attempt = 1; attempt <= tries; attempt += 1) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs), headers: { accept: 'application/json' } });
      if (res.ok) return await res.json();
    } catch {
      // retried below
    }
    if (attempt < tries) await new Promise((r) => setTimeout(r, 2000));
  }
  return null;
}

// The preview's dev server compiles on the first page load; do it before the camera rolls.
async function warmUp(app, log) {
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const started = Date.now();
    try {
      const res = await fetch(`${app}/`, { signal: AbortSignal.timeout(60_000), headers: { accept: 'text/html' } });
      await res.text();
      log(`warm-up GET ${app}/ → ${res.status} in ${Date.now() - started} ms`);
      return;
    } catch (error) {
      log(`warm-up GET ${app}/ failed (${firstLine(error.message)})`);
    }
  }
}

function runPlaywright(env, logPath) {
  return new Promise((resolvePromise) => {
    const child = spawn(process.execPath, [PLAYWRIGHT_CLI, 'test', '--config', join(HARNESS, 'playwright.config.ts')], {
      cwd: HARNESS,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const sink = (chunk) => appendFileSync(logPath, chunk);
    child.stdout.on('data', sink);
    child.stderr.on('data', sink);
    child.on('error', (error) => {
      appendFileSync(logPath, `could not start Playwright: ${error.message}\n`);
      resolvePromise(-1);
    });
    child.on('close', (code) => resolvePromise(code ?? -1));
  });
}

function findFiles(dir, predicate) {
  if (!existsSync(dir)) return [];
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) found.push(...findFiles(path, predicate));
    else if (predicate(entry.name)) found.push(path);
  }
  return found;
}

function readJson(path, fallback) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return fallback;
  }
}

function hasFfmpeg() {
  return spawnSync('ffmpeg', ['-hide_banner', '-version'], { stdio: 'ignore' }).status === 0;
}

function testOutcome(report) {
  const stats = report?.stats ?? {};
  const ran = (stats.expected ?? 0) + (stats.unexpected ?? 0) + (stats.flaky ?? 0);
  const globalError = Array.isArray(report?.errors) && report.errors.length ? report.errors[0]?.message : null;
  return { testsRan: ran > 0, testFailed: (stats.unexpected ?? 0) > 0, globalError };
}

/** Move Playwright's outputs into the demo layout; returns the artifact map. */
function collect(args, steps, log) {
  const { out } = args;
  const harness = join(out, '.harness');
  const artifacts = {};
  const video = findFiles(join(harness, 'results'), (name) => name.endsWith('.webm'))[0];
  if (video) {
    mkdirSync(join(out, 'video'), { recursive: true });
    renameSync(video, join(out, 'video', 'demo.webm'));
    artifacts.video = 'video/demo.webm';
  }
  const trace = findFiles(join(harness, 'results'), (name) => name === 'trace.zip')[0];
  if (trace) {
    renameSync(trace, join(out, 'trace.zip'));
    artifacts.trace = 'trace.zip';
  }
  const har = join(harness, 'network.har');
  if (existsSync(har)) {
    writeFileSync(join(out, 'network.har'), `${JSON.stringify(redactHar(readJson(har, {})), null, 1)}\n`);
    rmSync(har, { force: true });
    artifacts.har = 'network.har';
  }
  artifacts.api = 'api.json';
  artifacts.console = 'console.json';
  const script = join(out, 'demo.spec.ts');
  if (resolve(args.spec) !== script) copyFileSync(args.spec, script);
  artifacts.script = 'demo.spec.ts';
  artifacts.log = 'run.log';
  const shot = steps.find((s) => s.kind === 'ui' && s.status === 'pass' && s.screenshot)?.screenshot;
  const ffmpeg = artifacts.video && hasFfmpeg();
  if (shot) {
    artifacts.poster = shot;
  } else if (ffmpeg) {
    const res = runLogged(
      'ffmpeg',
      [
        '-y',
        '-loglevel',
        'error',
        '-ss',
        '1',
        '-i',
        join(out, artifacts.video),
        '-frames:v',
        '1',
        join(out, 'poster.png'),
      ],
      {},
      log,
    );
    if (res.status === 0) artifacts.poster = 'poster.png';
  }
  if (args.mp4 && ffmpeg) {
    const res = runLogged(
      'ffmpeg',
      [
        '-y',
        '-loglevel',
        'error',
        '-i',
        join(out, artifacts.video),
        '-c:v',
        'libx264',
        '-pix_fmt',
        'yuv420p',
        '-movflags',
        '+faststart',
        join(out, 'video', 'demo.mp4'),
      ],
      {},
      log,
    );
    if (res.status === 0) artifacts.video_mp4 = 'video/demo.mp4';
  }
  if (existsSync(args.tiltSnapshot)) artifacts.tilt_snapshot = relative(out, args.tiltSnapshot);
  return artifacts;
}

function writeManifest(args, fields, log) {
  const exists = (rel) => existsSync(resolve(args.out, rel)) && statSync(resolve(args.out, rel)).isFile();
  const manifest = buildManifest({
    prUrl: args.prUrl,
    pr: args.pr,
    sha: args.sha,
    planSource: args.planSource,
    app: args.app,
    api: args.api,
    exists,
    ...fields,
  });
  const path = join(args.out, 'demo.json');
  writeFileSync(path, `${JSON.stringify(manifest, null, 2)}\n`);
  log(`wrote ${path}: ${manifest.status} · ${manifest.summary}`);
  return manifest;
}

async function record(args) {
  const startedAt = Date.now();
  prepareOut(args.out);
  const logPath = join(args.out, 'run.log');
  const log = makeLog(logPath);
  log(`demo of PR #${args.pr} at ${args.sha} against ${args.app}`);

  let observedSha = null;
  if (!args.skipStaleCheck) {
    const stamp = await fetchJson(`${args.app}/__factory/preview`);
    const check = checkPreviewStamp(stamp, args.sha);
    log(`/__factory/preview: ${check.reason}`);
    if (!check.ok) {
      console.log(`DEMO BLOCKED stale-preview pr=${args.pr} sha=${sha7(args.sha)} · ${check.reason}`);
      return EXIT.STALE;
    }
    observedSha = check.observed;
  }

  let steps = [];
  let consoleEntries = [];
  let outcome;
  try {
    ensureDeps(log);
    const env = browserEnv(
      {
        ...process.env,
        FACTORY_DEMO_SPEC: args.spec,
        FACTORY_DEMO_OUT: args.out,
        FACTORY_DEMO_APP: args.app,
        FACTORY_DEMO_API: args.api,
        FACTORY_DEMO_PR: String(args.pr),
        FACTORY_DEMO_PR_URL: args.prUrl,
        FACTORY_DEMO_SHA: args.sha,
        FACTORY_DEMO_SECRET_FILE: args.secretFile,
      },
      log,
    );
    await warmUp(args.app, log);
    const code = await runPlaywright(env, logPath);
    log(`playwright test exited with ${code}`);
    steps = readJson(join(args.out, '.harness', 'steps.json'), []);
    consoleEntries = readJson(join(args.out, 'console.json'), []);
    const result = testOutcome(readJson(join(args.out, '.harness', 'report.json'), null));
    outcome = deriveStatus({
      steps,
      testsRan: result.testsRan,
      testFailed: result.testFailed || code !== 0,
      error: !result.testsRan && result.globalError ? result.globalError : null,
    });
  } catch (error) {
    log(`harness error: ${error.stack || error.message}`);
    outcome = { status: 'ERROR', reason: firstLine(error.message) };
  }

  let artifacts = { log: 'run.log' };
  try {
    artifacts = collect(args, steps, log);
  } catch (error) {
    log(`collecting outputs failed: ${error.stack || error.message}`);
  }
  const manifest = writeManifest(
    args,
    {
      status: outcome.status,
      summary: args.summary || defaultSummary(steps, outcome.status, outcome.reason),
      observedSha,
      startedAt,
      finishedAt: Date.now(),
      steps,
      consoleErrors: consoleErrorCount(consoleEntries),
      artifacts,
    },
    log,
  );
  const counts = ['pass', 'fail', 'skipped'].map((s) => `${steps.filter((x) => x.status === s).length} ${s}`);
  console.log(
    `DEMO ${manifest.status} pr=${args.pr} sha=${sha7(args.sha)} · ${counts.join(', ')} · ${manifest.summary}`,
  );
  console.log(`manifest: ${join(args.out, 'demo.json')}`);
  if (manifest.status === 'ERROR' || manifest.status === 'FAIL') console.log(`log: ${logPath}`);
  return exitCodeFor(manifest.status);
}

async function main(argv) {
  let values;
  try {
    values = parse(argv);
  } catch (error) {
    console.error(`${error.message}\n${USAGE}`);
    return EXIT.ERROR;
  }
  if (values.help) {
    console.log(USAGE);
    return 0;
  }
  if (values.selftest) {
    const { selftest } = await import('./selftest.mjs');
    return selftest({ harnessDir: HARNESS, keep: values.keep });
  }
  let args;
  try {
    args = validate(values);
  } catch (error) {
    if (!(error instanceof UsageError)) throw error;
    console.error(`${error.message}\n${USAGE}`);
    return EXIT.ERROR;
  }
  return record(args);
}

process.exitCode = await main(process.argv.slice(2));
