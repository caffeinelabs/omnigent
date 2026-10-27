---
name: factory-demo
description: Demo agent reference for recording reviewable PR demos in a factory preview - the Playwright harness shipped with this skill (setup, the demo.spec.ts format and fixtures, synthetic login, run.mjs exit codes, explore.mjs), the ~/factory/demos layout and demo.json manifest, publishing with sys_factory_demo, the PR comment, caps, statuses and the failure table.
---

# factory-demo

You record what a PR does, at its exact head, in its factory preview, so a
human can review it from the cockpit without checking it out. The harness
records a video (1280×800 webm with a caption and a `PR #<n> · <sha7> ·
preview` watermark), one screenshot per UI step, every API call, console
errors, a redacted HAR and, when the run fails, a Playwright trace, then
writes `demo.json`. Every file it publishes is scrubbed of the synthetic
secret and the session tokens.

## Harness setup (once per session; cheap to repeat)

The harness ships in this skill's `harness/` directory, inside the agent
bundle the runner unpacked under the temp directory. Copy it to
`~/factory/demo-harness` (on the PVC):

```bash
SRC=$(dirname "$(find "${TMPDIR:-/tmp}" /tmp -path '*/skills/factory-demo/harness/run.mjs' \
  -exec ls -t {} + 2>/dev/null | head -n1)")
test -f "$SRC/run.mjs" || { echo "factory-demo harness not found"; exit 1; }
H=~/factory/demo-harness
mkdir -p "$H"
diff -rq -x node_modules -x .install.lock "$SRC" "$H" >/dev/null 2>&1 || cp -R "$SRC"/. "$H"/
```

If Claude Code showed the skill's base directory, set `SRC=<base>/harness`
instead of the `find`.

`run.mjs` runs `npm ci` (pinned `@playwright/test@1.56.1`; one demo agent
at a time, under `.install.lock`) and picks the
browsers itself: the sandbox image's `/opt/ms-playwright` when it holds this
Playwright's Chromium, else a one-time `playwright install chromium` into
`~/.cache/ms-playwright`. To check the toolchain on its own, run
`node ~/factory/demo-harness/run.mjs --selftest` (about 15 s; it records three
demos of a local fixture page and prints `SELFTEST OK`).

## Inputs

The PL's DEMO command gives `pr`, `sha` (40 chars), `app`, `api`,
`secret_file` (`~/factory/previews/p<n>/synthetic-secret`) and `focus`.
Read the PR: `gh pr view <url> --json headRefOid,title,body,files` and
`gh pr diff <url>`. The PR body's `## Demo plan` lists tagged steps:

```
## Demo plan
- [api] GET /version → 200, body.gitSha is the deployed sha
- [ui] Open / logged out → landing renders · screenshot "landing"
- [ui:login] Open /settings → "API version" row shows the short sha
- [skip] Draft build flow — needs the build tier (not in factory envs)
```

Each line becomes one step. Without a Demo plan, derive one from the
Verification plan, the diff and the focus, and pass `--plan-source derived`
(the default is `pr-body`).

## Explore before you write selectors

```bash
node ~/factory/demo-harness/explore.mjs <app url>/settings --out /tmp/settings.png \
  [--login --api <api url> --synthetic-secret-file <secret_file>]
```

It prints the page's accessibility snapshot (roles and names for
`getByRole`) and saves a screenshot you can look at.

## The spec: `~/factory/demos/<n>/<sha7>/demo.spec.ts`

```ts
import { demo, expect, preview, step } from 'factory-demo';

demo('Settings shows the API version', async ({ ui, api, login }) => {
  await step.check('Sign in with a synthetic session', login);
  await step('Open the landing page', async () => {
    await ui.goto('/');
    await ui.see(ui.page.getByRole('heading', { name: /caffeine/i }));
  });
  await step.api('GET /version returns the deployed sha', async () => {
    const res = await api.get('/version');
    expect(res.status()).toBe(200);
    expect((await res.json()).gitSha).toBe(preview.sha);
  });
  await step('Settings shows the API version', async () => {
    await ui.goto('/settings');
    await ui.see(ui.page.getByText(`API version ${preview.sha7}`));
  });
  await step.skip('Draft build flow', 'needs the build tier (not in factory envs)');
});
```

| Fixture | Use |
|---|---|
| `step(title, fn)` | A UI step: caption, timing, a screenshot after it (`shots/NN-<title>.png`), or `…-failed.png` and the error when it fails. |
| `step.api(title, fn)` / `step.check(title, fn)` | An API step (its calls are attached) / a check without UI of its own. No screenshot. |
| `step.skip(title, reason)` | A planned step this env cannot run. The reason is shown to reviewers and makes the demo PARTIAL. |
| `ui.goto(path)`, `ui.click(t)`, `ui.fill(t, v)`, `ui.see(t)`, `ui.page` | Paths resolve against the preview app. `t` is a locator or selector; the element is outlined in the video before the action. `see` asserts visibility. |
| `api.get/post/put/patch/delete(path, {data?, headers?, params?})` | Requests to the preview API with the browser's cookies. Each call goes to `api.json` (bodies up to 8 KB, credentials redacted). |
| `login()` | `POST <api>/auth/synthetic-session` with the secret from the file (sent by Node, outside the browser, so it never reaches the trace or HAR), then the session cookies on the app and API hosts. Call it before the first page that needs a user. |
| `preview.{app, api, pr, prUrl, sha, sha7}` | The run's preview and head. |

Rules:
- Import only from `'factory-demo'` (it re-exports `expect`).
- One `demo()` per spec, 3-10 steps, under about 90 s. Every UI step asserts
  something visible; an assertion is what makes PASS mean something.
- Prefer `getByRole` and `getByText` to CSS. No sleeps over 2 s.
- The spec is published next to the demo: no secrets, tokens or personal data.
- Never change the product to make a step pass.

## Run

```bash
D=~/factory/demos/<n>/<sha7>
node ~/factory/demo-harness/run.mjs --spec "$D/demo.spec.ts" --out "$D" \
  --app <app url> --api <api url> --pr <pr url> --sha <40-char sha> \
  --synthetic-secret-file ~/factory/previews/p<n>/synthetic-secret \
  [--plan-source derived] [--summary "<one line for reviewers>"]
```

It checks that `<app>/__factory/preview` serves `--sha`, warms the app up,
records, and prints `DEMO <STATUS> pr=<n> sha=<sha7> · … ` and the manifest
path. Use the longest Bash timeout; a run takes 1-3 minutes.

| Exit | Status | Meaning |
|---|---|---|
| 0 | PASS | Every step ran and passed. |
| 0 | PARTIAL | Every step that ran passed; some were skipped for env reasons (build tier, AI tier, Internet Identity). |
| 1 | FAIL | A product assertion failed: the failed step has a screenshot and the error. |
| 2 | ERROR | The harness, browser or spec broke (also bad arguments). Read `run.log`. |
| 3 | stale | The preview serves another commit. Nothing is recorded: report `DEMO BLOCKED stale-preview`. |

## Output (`~/factory/demos/<n>/<sha7>/`)

| File | What |
|---|---|
| `demo.json` | The manifest: `version, pr_url, pr, sha, status, summary, plan_source, preview{app, api, observed_sha}, duration_s, console_errors, steps[{n, title, kind: ui/api/check, status: pass/fail/skipped, screenshot?, duration_ms?, note?, api_calls?}], artifacts{video, gif?, poster, api, har, trace? (FAIL and ERROR), console, script, log, tilt_snapshot?}` |
| `video/demo.webm` | The whole run; `poster` is the first UI screenshot. |
| `video/demo.gif` | With ffmpeg: the first 20 s at 800 px and 8 fps, at most 5 MB. The server mirrors it and the poster to GitHub and embeds them in `comment_markdown`. |
| `shots/NN-<title>.png` | One per UI step. |
| `api.json` | `[{i, step, method, url, status, ms, request{headers, body}, response{headers, content_type, body, bytes, truncated}}]` |
| `console.json` | Console errors, warnings and page errors. |
| `network.har` | Redacted (auth and cookie headers, cookie values, credential query parameters). |
| `trace.zip` | FAIL and ERROR runs only: the Playwright trace, credentials scrubbed. |
| `run.log`, `demo.spec.ts` | Forensics and the exact script that ran. |

`run.mjs` clears its old outputs in the directory on every run and keeps the
spec. Each head gets its own `<sha7>` directory.

## Publish

```
sys_factory_demo(pr_url=<url>, head_sha=<40-char sha>, title=<what it shows>, manifest_path=~/factory/demos/<n>/<sha7>/demo.json)
```

The runner uploads the manifest's files, and the demo is recorded with the
manifest's status and summary. It returns `{demo_id, demo_url, status, files,
comment_markdown, comment_path, next}`. Unless the status is ERROR, post the
comment exactly once per head with `gh pr comment <url> --body-file
<comment_path>`. It carries the `<!-- factory:demo sha=… status=… demo=… -->`
marker the server's demo gate reads, the step table and the links.

Caps (checked by the runner and the server): video ≤ 50 MB, image ≤ 5 MB,
text and JSON ≤ 2 MB, at most 30 files and 120 MB per demo. Files over a cap
are skipped and listed under `skipped`. Errors: `sha_mismatch` (the manifest
is for another head: run again at the head), `file_not_allowed`,
`demo_too_large`.

## Failure table

| Symptom | Fix |
|---|---|
| Exit 3, `DEMO BLOCKED stale-preview` | Do not record. Report it; the PL has env_keeper redeploy the preview. |
| `synthetic session: HTTP 404` | The preview has no `SYNTHETIC_SESSION_SECRET`. Demo the public pages and the API; skip logged-in steps with `step.skip(title, 'synthetic login unavailable in this preview')`. |
| `synthetic session: HTTP 401` | Wrong secret file: use the preview's `~/factory/previews/p<n>/synthetic-secret`, not the dev env's. |
| A selector times out | `explore.mjs` the page, fix the locator, run again (at most 3 attempts in all). |
| The first page load is slow | The harness warms the app up; split long flows into more steps. |
| `Cannot find module 'factory-demo'` | Run the spec through `run.mjs`, which loads the harness's tsconfig. |
| `npm ci` or `playwright install chromium failed` | Network or registry trouble: retry once, then report ERROR with the log line. |
| The browser does not start (missing libraries) | An old sandbox image. Report ERROR with the message. |
| ERROR you cannot explain | `node ~/factory/demo-harness/run.mjs --selftest`. If it passes, the spec is at fault. |

Never print the synthetic secret; pass only its file path.
