// One headless Chromium run of one demo spec; run.mjs sets FACTORY_DEMO_SPEC and FACTORY_DEMO_OUT.
import { defineConfig } from '@playwright/test';
import { basename, dirname, join, resolve } from 'node:path';

const spec = process.env.FACTORY_DEMO_SPEC ? resolve(process.env.FACTORY_DEMO_SPEC) : '';
const out = resolve(process.env.FACTORY_DEMO_OUT || 'demo-out');
const harness = join(out, '.harness');
const size = { width: 1280, height: 800 };

export default defineConfig({
  testDir: spec ? dirname(spec) : '.',
  testMatch: spec ? basename(spec) : '*.spec.ts',
  // Lets a spec anywhere import the fixtures as 'factory-demo'.
  tsconfig: './tsconfig.json',
  outputDir: join(harness, 'results'),
  preserveOutput: 'always',
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 5 * 60_000,
  globalTimeout: 10 * 60_000,
  reporter: [['list'], ['json', { outputFile: join(harness, 'report.json') }]],
  use: {
    browserName: 'chromium',
    headless: true,
    viewport: size,
    video: { mode: 'on', size },
    trace: 'on',
    screenshot: 'off',
    actionTimeout: 15_000,
    navigationTimeout: 60_000,
    launchOptions: { slowMo: 150 },
    contextOptions: { recordHar: { path: join(harness, 'network.har'), content: 'omit' } },
  },
});
