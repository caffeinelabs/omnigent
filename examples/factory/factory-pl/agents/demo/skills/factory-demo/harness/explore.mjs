#!/usr/bin/env node
// Look at a page before writing a demo spec: prints its accessibility snapshot
// (roles and names for getByRole selectors) and saves a screenshot.
//
//   node explore.mjs <url> [--out <png>] [--login --api <url> --synthetic-secret-file <path>]

import { readFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { firstLine, sessionCookies, syntheticSession } from './lib.mjs';
import { browserEnv, ensureDeps } from './setup.mjs';

const USAGE =
  'usage: node explore.mjs <url> [--out <png>] [--login --api <api url> --synthetic-secret-file <path>] [--wait <ms>]';

async function login(context, page, appUrl, apiUrl, secretFile) {
  const session = await syntheticSession(apiUrl, readFileSync(secretFile, 'utf8').trim());
  await context.addCookies(sessionCookies(appUrl, apiUrl, session));
  await page.addInitScript((id) => {
    try {
      window.localStorage.removeItem('jwt');
      if (id) window.localStorage.setItem('userId', id);
      window.localStorage.setItem('caffeineAuthSessionTransport', 'cookie');
    } catch {
      // Storage is unavailable on opaque origins.
    }
  }, session.userId);
}

async function main(argv) {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      out: { type: 'string' },
      login: { type: 'boolean', default: false },
      api: { type: 'string' },
      'synthetic-secret-file': { type: 'string' },
      wait: { type: 'string', default: '1500' },
    },
    strict: true,
  });
  const url = positionals[0];
  if (!url || (values.login && (!values.api || !values['synthetic-secret-file']))) {
    console.error(USAGE);
    return 2;
  }
  const log = (line) => console.error(`[explore] ${line}`);
  await ensureDeps(log);
  const env = browserEnv(process.env, log);
  if (env.PLAYWRIGHT_BROWSERS_PATH) process.env.PLAYWRIGHT_BROWSERS_PATH = env.PLAYWRIGHT_BROWSERS_PATH;
  // Imported after PLAYWRIGHT_BROWSERS_PATH is set.
  const { chromium } = await import('@playwright/test');
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    if (values.login) {
      const secretFile = resolve(values['synthetic-secret-file'].replace(/^~\//, `${homedir()}/`));
      await login(context, page, new URL(url).origin, values.api.replace(/\/+$/, ''), secretFile);
    }
    const res = await page.goto(url, { waitUntil: 'load', timeout: 60_000 });
    await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => undefined);
    await page.waitForTimeout(Number(values.wait) || 0);
    const out = resolve(values.out || join(tmpdir(), `factory-explore-${Date.now()}.png`));
    await page.screenshot({ path: out });
    console.log(`url: ${page.url()} (HTTP ${res?.status() ?? '?'})`);
    console.log(`title: ${await page.title()}`);
    console.log(`screenshot: ${out}`);
    console.log('aria snapshot of <body>:');
    console.log(await page.locator('body').ariaSnapshot());
    return 0;
  } catch (error) {
    console.error(`explore failed: ${firstLine(error.message)}`);
    return 1;
  } finally {
    await browser.close();
  }
}

process.exitCode = await main(process.argv.slice(2));
