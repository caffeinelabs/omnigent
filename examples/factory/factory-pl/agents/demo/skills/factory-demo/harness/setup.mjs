// Installs what a run needs: the pinned @playwright/test and a matching Chromium.

import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
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

/** `npm ci` unless node_modules already holds the pinned @playwright/test. */
export function ensureDeps(log) {
  if (installedVersion() === pinnedVersion()) return;
  const res = runLogged('npm', ['ci', '--no-audit', '--no-fund'], { cwd: HARNESS }, log);
  if (res.status !== 0 || installedVersion() !== pinnedVersion()) {
    throw new Error(`npm ci in ${HARNESS} failed; see the log`);
  }
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
