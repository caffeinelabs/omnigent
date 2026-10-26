// Fixtures for factory demo specs: `import { demo, step, expect, preview } from 'factory-demo'`.
// run.mjs sets FACTORY_DEMO_* for the Playwright run; see ../SKILL.md for the spec format.

import { test as base, expect, type BrowserContext, type Locator, type Page } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import {
  errorNote,
  redactHeaders,
  redactJsonText,
  redactUrl,
  screenshotName,
  sha7,
  truncate,
  watermark,
} from './lib.mjs';

export { expect };
export type { Locator, Page };

type Kind = 'ui' | 'api' | 'check';
type StepFn = () => unknown | Promise<unknown>;
type Target = Locator | string;

type StepRecord = {
  n: number;
  title: string;
  kind: Kind;
  status: 'pass' | 'fail' | 'skipped';
  screenshot?: string;
  duration_ms?: number;
  note?: string;
  api_calls?: number[];
};

type ApiCall = {
  i: number;
  step: number | null;
  method: string;
  url: string;
  status: number;
  ms: number;
  request: { headers: Record<string, string>; body: string | null };
  response: {
    headers: Record<string, string>;
    content_type: string | null;
    body: string;
    bytes: number;
    truncated: boolean;
  };
};

type ConsoleEntry = { type: string; text: string; url: string | null; at_ms: number };

type RequestOptions = {
  headers?: Record<string, string>;
  data?: unknown;
  params?: Record<string, string | number | boolean>;
  timeout?: number;
};

const OUT = process.env.FACTORY_DEMO_OUT || join(process.cwd(), 'demo-out');
const HARNESS_DIR = join(OUT, '.harness');
const STARTED = Date.now();

/** The preview under demo (set by run.mjs). */
export const preview = Object.freeze({
  app: (process.env.FACTORY_DEMO_APP || '').replace(/\/+$/, ''),
  api: (process.env.FACTORY_DEMO_API || '').replace(/\/+$/, ''),
  pr: Number(process.env.FACTORY_DEMO_PR || 0),
  prUrl: process.env.FACTORY_DEMO_PR_URL || '',
  sha: process.env.FACTORY_DEMO_SHA || '',
  sha7: sha7(process.env.FACTORY_DEMO_SHA),
});

const steps: StepRecord[] = [];
const apiCalls: ApiCall[] = [];
const consoleEntries: ConsoleEntry[] = [];
let activeStep: StepRecord | null = null;
let currentPage: Page | null = null;

function writeJson(path: string, value: unknown): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`);
}

function flush(): void {
  writeJson(join(HARNESS_DIR, 'steps.json'), steps);
  if (apiCalls.length) writeJson(join(OUT, 'api.json'), apiCalls);
  writeJson(join(OUT, 'console.json'), consoleEntries);
}

// Runs in the page: a watermark (bottom right) and the current step's caption (bottom left).
function installOverlay(label: string): void {
  const w = window as unknown as { __factoryDemoCaption?: (text: string) => void };
  const style =
    'position:fixed;z-index:2147483647;pointer-events:none;bottom:12px;padding:4px 10px;' +
    'border-radius:6px;color:#fff;font:600 13px/1.4 system-ui,-apple-system,sans-serif;';
  const ensure = (): HTMLElement | null => {
    const root = document.body || document.documentElement;
    if (!root) return null;
    let caption = document.getElementById('__factory-demo-caption');
    if (!caption) {
      const mark = document.createElement('div');
      mark.id = '__factory-demo-watermark';
      mark.textContent = label;
      mark.setAttribute('style', `${style}right:12px;background:rgba(17,24,39,.75);`);
      caption = document.createElement('div');
      caption.id = '__factory-demo-caption';
      caption.setAttribute('style', `${style}left:12px;max-width:65%;background:rgba(29,78,216,.88);`);
      root.append(mark, caption);
    }
    return caption;
  };
  const show = (text: string): void => {
    try {
      sessionStorage.setItem('__factory_demo_caption', text);
    } catch {
      // Opaque origins (about:blank) have no storage.
    }
    const caption = ensure();
    if (caption) {
      caption.textContent = text;
      caption.style.display = text ? 'block' : 'none';
    }
  };
  w.__factoryDemoCaption = show;
  const restore = (): void => {
    let text = '';
    try {
      text = sessionStorage.getItem('__factory_demo_caption') || '';
    } catch {
      text = '';
    }
    show(text);
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', restore, { once: true });
  } else {
    restore();
  }
}

async function setCaption(text: string): Promise<void> {
  if (!currentPage || currentPage.isClosed()) return;
  await currentPage
    .evaluate((value) => {
      const w = window as unknown as { __factoryDemoCaption?: (text: string) => void };
      w.__factoryDemoCaption?.(value);
    }, text)
    .catch(() => undefined);
}

async function screenshot(record: StepRecord, suffix?: string): Promise<string | undefined> {
  if (!currentPage || currentPage.isClosed()) return undefined;
  const rel = screenshotName(record.n, record.title, suffix);
  try {
    await currentPage.waitForTimeout(200);
    await currentPage.screenshot({ path: join(OUT, rel) });
    return rel;
  } catch {
    return undefined;
  }
}

async function runStep(kind: Kind, title: string, fn: StepFn): Promise<void> {
  // Recorded as failed until it finishes, so a timeout or crash leaves an honest record.
  const record: StepRecord = {
    n: steps.length + 1,
    title,
    kind,
    status: 'fail',
    note: 'the step did not finish (timeout or crash)',
  };
  steps.push(record);
  activeStep = record;
  flush();
  await setCaption(`Step ${record.n} · ${title}`);
  const started = Date.now();
  const firstCall = apiCalls.length;
  try {
    await base.step(title, async () => {
      await fn();
    });
    record.status = 'pass';
    delete record.note;
    if (kind === 'ui') record.screenshot = await screenshot(record);
  } catch (error) {
    record.status = 'fail';
    record.note = errorNote((error as Error)?.message ?? String(error)) || 'failed';
    record.screenshot = await screenshot(record, 'failed');
    throw error;
  } finally {
    record.duration_ms = Date.now() - started;
    const calls = apiCalls.slice(firstCall).map((call) => call.i);
    if (calls.length) record.api_calls = calls;
    activeStep = null;
    flush();
  }
}

type Step = ((title: string, fn: StepFn) => Promise<void>) & {
  ui: (title: string, fn: StepFn) => Promise<void>;
  api: (title: string, fn: StepFn) => Promise<void>;
  check: (title: string, fn: StepFn) => Promise<void>;
  skip: (title: string, reason: string, kind?: Kind) => void;
};

/** A UI step: caption, timing, and a screenshot after it (or of the failure). */
export const step: Step = Object.assign((title: string, fn: StepFn) => runStep('ui', title, fn), {
  ui: (title: string, fn: StepFn) => runStep('ui', title, fn),
  /** An API step: its calls are attached; no screenshot. */
  api: (title: string, fn: StepFn) => runStep('api', title, fn),
  /** A check with no UI of its own (e.g. sign-in); no screenshot. */
  check: (title: string, fn: StepFn) => runStep('check', title, fn),
  /** A planned step the env cannot run; the reason is shown to reviewers (PARTIAL). */
  skip: (title: string, reason: string, kind: Kind = 'ui') => {
    steps.push({ n: steps.length + 1, title, kind, status: 'skipped', note: reason });
    flush();
  },
});

function locate(page: Page, target: Target): Locator {
  return typeof target === 'string' ? page.locator(target) : target;
}

// Outline the element for a moment so the action is visible in the video.
async function flash(locator: Locator): Promise<void> {
  await locator.evaluate((el) => {
    const node = el as HTMLElement;
    const previous = node.style.outline;
    node.style.outline = '3px solid #f59e0b';
    setTimeout(() => {
      node.style.outline = previous;
    }, 900);
  });
  await locator.page().waitForTimeout(350);
}

function makeUi(page: Page) {
  return {
    page,
    /** Open a path of the preview app (or an absolute URL). */
    async goto(path = '/'): Promise<void> {
      await page.goto(new URL(path, `${preview.app}/`).toString(), { waitUntil: 'load' });
      await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => undefined);
    },
    async click(target: Target): Promise<void> {
      const locator = locate(page, target);
      await flash(locator);
      await locator.click();
    },
    async fill(target: Target, value: string): Promise<void> {
      const locator = locate(page, target);
      await flash(locator);
      await locator.fill(value);
    },
    /** Assert the element is visible and point at it. */
    async see(target: Target): Promise<void> {
      const locator = locate(page, target);
      await expect(locator).toBeVisible();
      await flash(locator);
    },
  };
}

function requestBody(options: RequestOptions | undefined): string | null {
  if (options?.data === undefined || options.data === null) return null;
  const text = typeof options.data === 'string' ? options.data : JSON.stringify(options.data);
  return truncate(redactJsonText(text)).text;
}

function makeApi(context: BrowserContext) {
  const call = async (method: string, path: string, options?: RequestOptions) => {
    const url = new URL(path, `${preview.api || preview.app}/`).toString();
    const started = Date.now();
    const response = await context.request.fetch(url, { method, ...options });
    const ms = Date.now() - started;
    const raw = await response.text().catch(() => '');
    const body = truncate(redactJsonText(raw));
    const headers = response.headers();
    apiCalls.push({
      i: apiCalls.length,
      step: activeStep?.n ?? null,
      method,
      url: redactUrl(url),
      status: response.status(),
      ms,
      request: { headers: redactHeaders(options?.headers ?? {}) as Record<string, string>, body: requestBody(options) },
      response: {
        headers: redactHeaders(headers) as Record<string, string>,
        content_type: headers['content-type'] ?? null,
        body: body.text,
        bytes: body.bytes,
        truncated: body.truncated,
      },
    });
    flush();
    return response;
  };
  return {
    get: (path: string, options?: RequestOptions) => call('GET', path, options),
    post: (path: string, options?: RequestOptions) => call('POST', path, options),
    put: (path: string, options?: RequestOptions) => call('PUT', path, options),
    patch: (path: string, options?: RequestOptions) => call('PATCH', path, options),
    delete: (path: string, options?: RequestOptions) => call('DELETE', path, options),
    fetch: (method: string, path: string, options?: RequestOptions) => call(method.toUpperCase(), path, options),
  };
}

function cookieValue(setCookies: string[], name: string): string | undefined {
  for (const header of setCookies) {
    if (header.startsWith(`${name}=`)) {
      const value = header.slice(name.length + 1).split(';')[0];
      if (value) return value;
    }
  }
  return undefined;
}

/** Sign in through the app's synthetic session (POST <api>/auth/synthetic-session). */
async function syntheticLogin(page: Page): Promise<{ userId: string }> {
  const file = process.env.FACTORY_DEMO_SECRET_FILE;
  if (!file) throw new Error('login() needs run.mjs --synthetic-secret-file <path>');
  const secret = readFileSync(file, 'utf8').trim();
  const endpoint = `${preview.api}/auth/synthetic-session`;
  const response = await page.context().request.post(endpoint, {
    headers: { Authorization: `Bearer ${secret}` },
    data: {},
  });
  if (!response.ok()) {
    const hint = response.status() === 404 ? ' (404: the env has no SYNTHETIC_SESSION_SECRET)' : '';
    throw new Error(`synthetic session: HTTP ${response.status()} from ${endpoint}${hint}`);
  }
  const body = (await response.json().catch(() => ({}))) as { user?: { userId?: string } };
  const setCookies = response
    .headersArray()
    .filter((h) => h.name.toLowerCase() === 'set-cookie')
    .map((h) => h.value);
  const accessToken = cookieValue(setCookies, 'access_token');
  const refreshToken = cookieValue(setCookies, 'refresh_token');
  if (!accessToken || !refreshToken) {
    throw new Error('synthetic session set no access_token / refresh_token cookie');
  }
  const app = new URL(preview.app);
  const api = new URL(preview.api);
  const crossSite = api.hostname !== app.hostname && api.protocol === 'https:';
  const hosts = [
    { domain: app.hostname, secure: app.protocol === 'https:', sameSite: 'Lax' as const },
    {
      domain: api.hostname,
      secure: api.protocol === 'https:',
      sameSite: crossSite ? ('None' as const) : ('Lax' as const),
    },
  ];
  await page.context().addCookies(
    hosts.flatMap((host) =>
      [
        { name: 'access_token', value: accessToken },
        { name: 'refresh_token', value: refreshToken },
      ].map((cookie) => ({ ...cookie, ...host, path: '/', httpOnly: true })),
    ),
  );
  const userId = body.user?.userId ?? '';
  await page.addInitScript((id: string) => {
    try {
      window.localStorage.removeItem('jwt');
      if (id) window.localStorage.setItem('userId', id);
      window.localStorage.setItem('caffeineAuthSessionTransport', 'cookie');
    } catch {
      // Storage is unavailable on opaque origins.
    }
  }, userId);
  return { userId };
}

type DemoFixtures = {
  ui: ReturnType<typeof makeUi>;
  api: ReturnType<typeof makeApi>;
  step: Step;
  login: () => Promise<{ userId: string }>;
  recorder: void;
};

/** `demo(title, async ({ ui, api, step, login }) => …)`: one recorded demo. */
export const demo = base.extend<DemoFixtures>({
  recorder: [
    async ({}, use) => {
      await use();
      flush();
    },
    { auto: true },
  ],
  page: async ({ page }, use) => {
    currentPage = page;
    await page.addInitScript(installOverlay, watermark(preview.pr, preview.sha));
    page.on('console', (message) => {
      const type = message.type();
      if (type !== 'error' && type !== 'warning') return;
      consoleEntries.push({
        type,
        text: truncate(message.text(), 2000).text,
        url: message.location()?.url || null,
        at_ms: Date.now() - STARTED,
      });
    });
    page.on('pageerror', (error) => {
      consoleEntries.push({
        type: 'pageerror',
        text: truncate(`${error.name}: ${error.message}`, 2000).text,
        url: null,
        at_ms: Date.now() - STARTED,
      });
    });
    await use(page);
    // Hold the last frame so the video ends on the result, not mid-action.
    if (!page.isClosed()) await page.waitForTimeout(1200).catch(() => undefined);
    currentPage = null;
  },
  ui: async ({ page }, use) => {
    await use(makeUi(page));
  },
  api: async ({ context }, use) => {
    await use(makeApi(context));
  },
  step: async ({}, use) => {
    await use(step);
  },
  login: async ({ page }, use) => {
    await use(() => syntheticLogin(page));
  },
});
