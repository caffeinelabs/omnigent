// Installs what a run needs: the pinned @playwright/test and a matching Chromium.

import { spawnSync } from 'node:child_process';
import { mkdirSync, readFileSync, rmSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { BAKED_BROWSERS_PATH, selectBrowsersPath } from './lib.mjs';

export const HARNESS = dirname(fileURLToPath(import.meta.url));
export const PLAYWRIGHT_CLI = join(HARNESS, 'node_modules', '@playwright', 'test', 'cli.js');

function pinnedVersion() {
  const pkg = JSON.parse(readFileSync(join(HARNESS, 'package.json'), 'utf8'));
  return pkg.devDependencies['@playwright/test'];
}

function installedVersion() {
  try {
    const pkg = join(HARNESS, 'node_modules', '@playwright', 'test', 'package.json');
    return JSON.parse(readFileSync(pkg, 'utf8')).version;
  } catch {
    return null;
  }
}

export function runLogged(command, args, options, log) {
  const res = spawnSync(command, args, { ...options, encoding: 'utf8' });
  log(`$ ${command} ${args.join(' ')} → exit ${res.status}`);
  if (res.stdout?.trim()) log(res.stdout.trim().slice(-4000));
  if (res.stderr?.trim()) log(res.stderr.trim().slice(-4000));
  return res;
}

// Demo agents share ~/factory/demo-harness: one `npm ci` at a time.
const INSTALL_LOCK = join(HARNESS, '.install.lock');
const LOCK_STALE_MS = 15 * 60_000;
const LOCK_WAIT_MS = 10 * 60_000;

async function withInstallLock(log, fn) {
  const deadline = Date.now() + LOCK_WAIT_MS;
  for (;;) {
    try {
      mkdirSync(INSTALL_LOCK);
      break;
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      const since = statSync(INSTALL_LOCK, { throwIfNoEntry: false })?.mtimeMs ?? Date.now();
      if (Date.now() - since > LOCK_STALE_MS) {
        log(`removing a stale ${INSTALL_LOCK}`);
        rmSync(INSTALL_LOCK, { recursive: true, force: true });
      } else if (Date.now() > deadline) {
        throw new Error(`another demo agent has held ${INSTALL_LOCK} for too long`);
      } else {
        await sleep(2000);
      }
    }
  }
  try {
    return fn();
  } finally {
    rmSync(INSTALL_LOCK, { recursive: true, force: true });
  }
}

/** `npm ci` unless node_modules already holds the pinned @playwright/test. */
export async function ensureDeps(log) {
  if (installedVersion() === pinnedVersion()) return;
  await withInstallLock(log, () => {
    if (installedVersion() === pinnedVersion()) return;
    const res = runLogged('npm', ['ci', '--no-audit', '--no-fund'], { cwd: HARNESS }, log);
    if (res.status !== 0 || installedVersion() !== pinnedVersion()) {
      throw new Error(`npm ci in ${HARNESS} failed; see the log`);
    }
  });
}

/**
 * The environment Playwright runs with: the sandbox image's baked browsers when
 * they match this Playwright, else the default cache (installed once, on the PVC).
 */
export function browserEnv(baseEnv, log) {
  const env = { ...baseEnv };
  // FACTORY_DEMO_BAKED_BROWSERS points tests at another baked directory.
  const baked = baseEnv.FACTORY_DEMO_BAKED_BROWSERS || BAKED_BROWSERS_PATH;
  const browsers = selectBrowsersPath({ harnessDir: HARNESS, env, baked });
  log(`browsers: ${browsers.source}${browsers.path ? ` (${browsers.path})` : ''}`);
  if (browsers.path) {
    env.PLAYWRIGHT_BROWSERS_PATH = browsers.path;
    return env;
  }
  const res = runLogged(process.execPath, [PLAYWRIGHT_CLI, 'install', 'chromium'], { cwd: HARNESS, env }, log);
  if (res.status !== 0) throw new Error('playwright install chromium failed; see the log');
  return env;
}
