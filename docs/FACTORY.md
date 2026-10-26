# Software factory

The software factory runs many coding agents as one organization: two leads
that hold each other accountable, a project lead (PL) per project, and a team
of implementers per PL. It is not a separate service. Every member is an
ordinary Omnigent session, so the human can open, read and type into any of
them from the web UI.

The factory adds four things to plain sessions:

- **Membership.** `omnigent.factory.*` labels join top-level sessions into one
  factory, across sandboxes.
- **Messaging.** `sys_session_post` sends a message to any member. The server
  delivers it and wakes or relaunches the target if needed.
- **Supervision.** A deterministic server loop (not an LLM) keeps members
  alive, nudges idle ones and raises alerts.
- **Gates.** A DENY-only policy and a merge gate decide in code what agents
  may push or merge.

## Org chart

```
HUMAN ──► Lead A "Atlas" (factory-lead, factory root)  ◄── supervise each other ──►  Lead B "Sentinel" (factory-lead)
              │ sys_factory_spawn
              ├──► PL · <project> (factory-pl, own sandbox, supervisor = Sentinel)
              │        ├─ env_keeper  (claude-native)  owns the dev env and one factory preview per PR
              │        ├─ demo        (claude-native)  records a Playwright demo of each PR head
              │        ├─ ic          (claude-native)  one task card, one worktree, one PR
              │        ├─ ic_codex    (codex-native)   second-vendor implementer
              │        └─ reviewer    (codex-native)   cross-vendor review marker
              └──► PL · <project> …
SERVER: FactorySupervisor — keepalive, heartbeats, alerts, reply notices
```

- Leads and PLs are **top-level managed sessions**, each in its own sandbox.
  They are joined by labels, not by `parent_session_id`, because a parent-linked
  child must share its parent's runner.
- The PL's team are **named sub-agents** of the PL, co-located in its sandbox.
  They inherit membership from their root (the PL), and their role comes from
  their spec's `executor.config.factory_role`.
- The human talks mostly to Atlas. Atlas's **Agents** panel lists the members
  it spawned (`include_factory` on `GET /v1/sessions/{id}/child_sessions`),
  with role chips and beacon badges; the Graph view shows the tree.

## Turning it on

| Variable | Default | Effect |
|---|---|---|
| `OMNIGENT_FACTORY_ENABLED` | unset | `1` seeds the `factory-lead` and `factory-pl` built-ins and starts the supervisor and the status-edge notifier. Any other value leaves both off. The `/v1/factory` routes and the mailer are always mounted. |
| `OMNIGENT_FACTORY_TICK_S` | `60` | Seconds between supervisor passes. The first pass waits one interval. |
| `OMNIGENT_FACTORY_KEEPALIVE_S` | `600` | Minimum seconds between keepalives of one member. |
| `OMNIGENT_FACTORY_STUCK_S` | `5400` | A session `running` with no progress this long raises one stuck alert. |
| `OMNIGENT_FACTORY_BLOCKED_ESCALATE_S` | `1800` | A session parked on a human prompt this long is escalated as `[NEEDS HUMAN]`. |
| `OMNIGENT_FACTORY_POST_RATE_PER_HOUR` | `60,20` | Posts per hour per sender, and per (sender, target) pair. A single number sets the sender limit and a pair limit of a third of it. |
| `OMNIGENT_FACTORY_MERGE_REPOS` | empty | `owner/repo` list (comma or space separated) the merge gate may merge in. Empty means the gate merges nowhere. Read by the gate inside the sandbox. |
| `OMNIGENT_BUILTIN_AGENT_DIRS` | unset | Extra built-in bundles, seeded after the defaults. Used as the prompt hotfix lane (below). |

Invalid timing values fall back to their defaults with a warning.

## Agents

The bundles live in [`examples/factory/`](../examples/factory) and ship in the
wheel under `omnigent.resources.examples` (the `omnigent/resources/examples/factory`
symlink). Each directory is seeded as a built-in named after it.

| Agent | Harness | `factory_role` | Notes |
|---|---|---|---|
| `factory-lead` | claude-native | `lead` | Atlas or Sentinel; the brief decides which. `factory_max_pods: 12`. Skills: `factory-protocol`, `factory-lead-playbook`, `factory-heartbeat`, `factory-recovery`. |
| `factory-pl` | claude-native | `project_lead` | Sub-agents `env_keeper`, `demo`, `ic`, `ic_codex`, `reviewer`. Skills: `factory-protocol`, `factory-pl-playbook`, `factory-heartbeat`. |
| `env_keeper` | claude-native | `env_keeper` | `tilt` terminal; the dev env (UP / STATUS / DOWN) and factory previews (PREVIEW, alias VERIFY / PREVIEW_DOWN / PREVIEW_STATUS / MERGED / CLOSED). Skill `factory-env`. |
| `demo` | claude-native | `demo` | DEMO: records a PR head in its preview with the Playwright harness of its `factory-demo` skill, publishes it, posts the demo comment. |
| `ic` | claude-native | `ic` | One task card in its own worktree; `NOTES.md` checkpoints; a RESULT block every turn. |
| `ic_codex` | codex-native, `yolo: true` | `ic` | Everything in its prompt. |
| `reviewer` | codex-native, `yolo: true` | `reviewer` | Review only; posts the review marker. |

Every spec runs unsandboxed in the caller process (`os_env: caller_process`,
`sandbox: none`; the sandbox pod is the boundary), uses
`permission_mode: bypassPermissions` behind the DENY-only policy hooks (or
`yolo` for Codex) so nothing waits on an approval card, and sets
`ask_timeout: 86400`.

**Model routing.** The claude-native agents set
`executor.auth: {type: provider, name: bifrost}` and a family alias
(`model: opus` for leads and PLs, `model: sonnet` for `env_keeper`, `demo`
and `ic`).
The `bifrost` provider in the runner's `~/.omnigent/config.yaml` maps the alias
to a concrete model id (on the sandbox cluster, `claude-opus-5-5` and
`claude-sonnet-5-5`), so Claude Code reaches the keyless in-cluster Bifrost
gateway and never depends on one user's Databricks token. Ordinary Claude
sessions keep the provider default. The codex-native agents set no auth and use
the host's default openai-family provider (Bifrost in the sandbox).

**`factory_role`** in `executor.config` registers the factory tools for the
spec and makes the server treat a top-level session of that agent as a
member on its first factory call.

## Labels

Stored in `conversation_labels`; no schema change. Defined in
[`omnigent/factory.py`](../omnigent/factory.py).

| Key | On | Meaning |
|---|---|---|
| `omnigent.factory.id` | top-level members | Session id of the factory root (Lead A). |
| `omnigent.factory.role` | top-level | `lead` or `project_lead`. |
| `omnigent.factory.parent` | spawned members | The member that spawned this one. |
| `omnigent.factory.project` | PLs | Project slug, addressable as `project:<slug>`. |
| `omnigent.factory.supervisor` | top-level | Session that receives this member's alerts. |
| `omnigent.factory.state` | top-level | `active`, `paused` or `done`. Only `active` members get keepalives and heartbeats. |
| `omnigent.factory.heartbeat_s` | top-level | LLM heartbeat interval: `0` (off) or 120-86400. Defaults: lead 3600, PL 7200. |
| `omnigent.factory.last_beat` | top-level | ISO time of the last delivered heartbeat (written by the supervisor, so restart-safe). |
| `omnigent.factory.summary` | any | Beacon line, at most 120 characters (longer values are clamped). |
| `omnigent.factory.health` | any | `ok`, `degraded`, `blocked` or `dead`. |

**Lazy enrolment.** The first `/v1/factory` call from a top-level session whose
spec declares `lead` or `project_lead`, and that has no `omnigent.factory.id`,
stamps `id` (itself, or its spawner's factory when `parent` is set), `role`,
`state=active` and the role's default heartbeat. This is how Lead A, created
in the web UI, founds a factory.

## Tools

Runner tools in [`omnigent/tools/builtins/factory.py`](../omnigent/tools/builtins/factory.py),
dispatched in [`omnigent/runner/factory_dispatch.py`](../omnigent/runner/factory_dispatch.py).
They are registered only for specs with a `factory_role` and are on the native
relay surface (Claude Code sees `mcp__omnigent__<name>`).

- `sys_session_post(to, message, kind="info", restart=false)` — the factory
  SendMessage. `to` is a session id or an alias: `parent`, `lead` (the factory
  root), `peer_lead`, `supervisor` or `project:<slug>`. `kind` is `info`,
  `request`, `reply`, `brief` or `alert`. Returns `{mail_id, to_session_id,
  status: "queued"}` at once.
- `sys_factory_roster()` — every top-level member with role, project,
  supervisor, status, runner connectivity, last activity, heartbeat, state,
  health, summary, URL, and its sub-agents. The first call enrols the caller.
- `sys_factory_set(session_id?, state?, summary?, health?, heartbeat_s?,
  supervisor_session_id?, title?)` — the beacon and supervision labels. A lead
  may set any field on any member; a PL may set summary and health on its own
  sub-agents; anyone may set their own.
- `sys_factory_spawn(agent, title, message, role="project_lead", project?,
  workspaces?, heartbeat_s?, supervisor_session_id?)` — **leads only**. It
  checks the pod cap (`factory_max_pods`, counting members that are not
  `done`), refuses a project that already has a member that is not `done`,
  resolves a built-in agent through `GET /v1/agents`, creates a managed session
  (`POST /v1/sessions`, `host_type: managed`, factory labels, no parent link),
  posts `message` to it as a `brief`, and returns `{session_id, url, status:
  "provisioning", …}`. The supervisor defaults to the peer lead for a PL and to
  the caller for a lead; spawning a second lead while the caller has no
  supervisor makes the two leads supervise each other.

For factory roles, `sys_session_get_history`, `sys_session_get_info` and
`sys_session_list` are scoped to the caller's factory: reading another
factory's or a non-member's session returns `session_out_of_factory`, and
`sys_session_list` returns the caller's sub-agents plus the roster.

## `/v1/factory` API

Routes in [`omnigent/server/factory/routes.py`](../omnigent/server/factory/routes.py),
hidden from the public OpenAPI schema. Every call names `from_session_id`, and
the caller needs EDIT on it. When the runner tunnel-token header is present it
must bind to that session's runner (or its root's, for a sub-agent) or be on
the server's tunnel-token allow-list; otherwise `403 sender_not_bound`.

| Route | Body | Result |
|---|---|---|
| `GET /v1/factory/membership/{id}?from_session_id=` | — | The membership (`factory_id`, `role`, `project`, `parent_session_id`, `supervisor_session_id`, `state`, `heartbeat_s`, `health`, `summary`, `is_top_level`, `root_session_id`, `title`). Another session needs READ and the same factory (`403 session_out_of_factory`). |
| `GET /v1/factory/roster?from_session_id=` | — | `{factory_id, members: [...]}`, only members the caller can read. |
| `POST /v1/factory/messages` | `{from_session_id, to, message, kind, restart}` | `202 {mail_id, to_session_id, status: "queued"}`. Errors: `400 unknown_target`, `400 invalid_kind`, `400 empty_message`, `403 session_out_of_factory`, `403 restart_not_allowed`, `413 message_too_large`, `429 rate_limited`. Needs EDIT on the target (OWNER for a restart). |
| `PATCH /v1/factory/sessions/{id}` | `{from_session_id, state?, summary?, health?, heartbeat_s?, supervisor_session_id?, title?}` | The updated membership. `403 not_allowed`, `400 top_level_only` (sub-agents carry only summary and health), `400 invalid_heartbeat_s`. An empty `supervisor_session_id` clears it. |

Spawn uses the ordinary `GET /v1/agents` and `POST /v1/sessions`.

## Delivery

`POST /v1/factory/messages` only queues. The in-memory mailer
([`mailer.py`](../omnigent/server/factory/mailer.py)) delivers in the
background with [`deliver_message`](../omnigent/server/factory/delivery.py),
which injects the text as a user message the way a typed message is: it waits
out an in-flight managed launch, wakes or relaunches the target's runner,
heals a stale sub-agent binding, re-initializes the session after a relaunch,
arms the relay and dispatches.

- **Envelope.** The server prepends
  `[Factory message · from "<title>" (<id>, <role>[, project=<slug>]) · kind=<kind> · reply: sys_session_post(to="<id>")]`.
  Body lines that start like server text (`[Factory`, `[System`,
  `[NEEDS HUMAN`, case-insensitive, ignoring leading whitespace and invisible
  characters) are prefixed with `> `.
- **Order.** One message at a time per target, so a brief arrives before its
  follow-ups.
- **Retries.** A retryable failure (runner unavailable, wrong replica, 5xx,
  transport error) is retried after 60 s and 300 s. A final failure, or a
  permanent one (session gone or closed, 4xx), bounces
  `[Factory delivery failure · message to "<title>" (<id>) undeliverable: <reason>]`
  to the sender.
- **Restart.** `restart=true` is allowed from a lead, or from the target's
  supervisor, onto another top-level member. The server runs the UI's Stop
  (pane killed, host runner stopped, intentional-stop marker), waits up to 15 s
  for the old tunnel to drop, then delivers, which relaunches the session.
- **Reply notices.** A `request` or `brief` registers a pending reply. When the
  target's turn ends (an authoritative `idle` edge from the harness's Stop hook)
  and the target did not post back to the sender since, the sender receives
  `[Factory notice · "<title>" (<id>) finished the turn your request started (idle). Last output: «…».]`
  with up to 1200 characters of output. Heartbeat- and human-started turns
  never notify.
- **Rate limits.** Beyond the limits the post gets `429`, and the factory root
  gets one `[Factory alert · message storm …]` per hour.
- **Not durable.** Queued mail and notices live in one server process. A server
  restart drops them; the prompts tell agents to re-ask anything unanswered at
  their next heartbeat. `last_beat` survives in labels.
- **No input policy.** Factory messages skip the target's input guardrail; the
  factory's policies act on tool calls.

## Supervisor

[`supervisor.py`](../omnigent/server/factory/supervisor.py) and
[`notify.py`](../omnigent/server/factory/notify.py), enabled by
`OMNIGENT_FACTORY_ENABLED=1`.

| Rule | Trigger | Action |
|---|---|---|
| S1 keepalive | `active` top-level member with a live runner, keepalive older than `KEEPALIVE_S` | `GET /health` through the runner tunnel: resets the runner's idle watchdog for zero tokens. |
| S2 heartbeat | `active`, `heartbeat_s > 0`, not running, idle since `max(last_beat, updated_at)` for at least `heartbeat_s` | Delivers `[Factory heartbeat #<n> · <iso>] Run load_skill("factory-heartbeat") and follow it. Reply only if something changed.` (wakes or relaunches), then writes `last_beat`. A failed beat is retried no sooner than 300 s later. |
| S2b dead | 3 failed beats within an hour | `health=dead` and `[NEEDS HUMAN · relaunch of "<title>" failed 3× (<reason>)]` to the supervisor and the root, at most hourly. A later successful beat resets health to `ok`. |
| S3 stuck | any member or sub-agent `running` with no progress for `STUCK_S` | One `[Factory alert · "<title>" (<id>) running with no progress for <dur>. …]` per episode, to the parent (sub-agent) or supervisor. Never cancels. |
| S4 failed | a `failed` status edge | `[Factory alert · "<title>" (<id>) failed: <detail>]` to the parent or supervisor, at most once per 10 min per session. |
| S5 blocked | a turn parked on `blocked_on` (an approval only a human can answer) for `BLOCKED_ESCALATE_S` | `[NEEDS HUMAN · "<title>" waiting since <ts>: <what>]` to the factory root once; to the root's supervisor when the root itself is parked. |
| S6 storm | rate limit hit | One alert per hour to the factory root. |

Paused and done members get no keepalive and no heartbeats, so their runners
idle out and their sandboxes are reclaimed.

## Cockpit, previews and demos

The human runs the factory from the **cockpit** (`/factory`, one factory at
`/factory/<factory id>`; `?review=<pr number>` opens a PR's review drawer and
`&decision=D-<n>` a decision). Everything on it comes from the server: a
GitHub poller computes each PR's gates, a prober checks envs and previews, and
an event log records what agents did. Agents add detail through four tools;
if they stay silent the board is still right.

### Routes

| Route | Who | What |
|---|---|---|
| `GET /v1/factory/factories`, `GET /v1/factory/factories/{fid}` (ETag), `…/events` | human (READ on the root) | Factory list, the snapshot (projects, PRs with gates, previews, demos, decisions, members, health), the activity log. |
| `POST /v1/factory/factories`, `POST …/{fid}/projects`, `PATCH …/projects/{slug}` | human | Start a factory from the form (stores charters, queues `[Factory brief … via cockpit]`), add or edit a project. |
| `POST /v1/factory/factories/{fid}/control` | human (EDIT) | PAUSE, RESUME, STOP, RESTART, RELAUNCH, cadence, PREVIEW / PREVIEW_DOWN of a PR, ENV STATUS: a server-stamped `[Factory control …]` to the members. |
| `GET /v1/factory/decisions`, `POST …/decisions/{id}/answer`, `…/withdraw` | human | Open decisions (also in `/inbox`); the answer is delivered to the asker as `[Factory decision · D-<n> answered …]`. |
| `POST /v1/factory/prs/{id}/feedback`, `…/refresh` | human | Approve or request changes (`[Factory review …]` to the PL); re-poll GitHub now. |
| `GET /v1/factory/prs/{id}/preview` | human | Stable live-preview link: 302 to the current preview app URL, or back to the cockpit when there is none. |
| `GET /v1/factory/demos/{demo_id}/files/{file_id}` | human (READ on the PL) | Demo files with Range/206 and ETag; only images and video inline, never HTML or SVG. |
| `POST /v1/factory/agent/decisions`, `POST …/agent/prs`, `PUT …/agent/envs`, `POST …/agent/demos` (multipart), `GET …/agent/charter` | runner (the tools below) | Agent reports. |
| `GET /v1/factory/preflight` | human | GitHub connected, agents seeded, sandbox host, factory enabled. |

### Tools

| Tool | Roles | Use |
|---|---|---|
| `sys_factory_ask(question, options, context?, blocking=true, dedupe_key?)` | every role | Ask the human. Opens D-<n> in the cockpit and the inbox and returns at once; the answer arrives as a message. Replaces DECISIONS NEEDED lists. |
| `sys_factory_pr(url, task?, claim?, sentinel_verdict?, head_sha?, note?)` | every role (`sentinel_verdict`: leads) | Register a PR, claim `opened` / `ready_for_human_merge` / `abandoned`, or record Sentinel's verdict. Returns the server's gates `{ready, gates, reasons, claim_mismatch}`. |
| `sys_factory_env(state, env_id, urls, deployed{sha, ref, tooling_overlay?}, pr?, namespace?, resources?, note?)` | `project_lead`, `env_keeper` | Report the dev env (no `pr`) or the factory preview of PR `pr`. `state=deploying` beyond `OMNIGENT_FACTORY_MAX_PREVIEWS` live previews is refused with `409 preview_cap` and park candidates. |
| `sys_factory_demo(pr_url, head_sha, title, manifest_path?, files?, …)` | `project_lead`, `env_keeper`, `demo` | Upload a demo (with `manifest_path`, everything the harness's `demo.json` lists) and get back `comment_markdown` / `comment_path` to post on the PR. |
| `sys_factory_set(stage=…)` | `project_lead` on itself | Claim the project's stage; the cockpit shows it next to the stage it derives. |

### Factory previews

A **factory preview** is the app deployed at one PR head so humans (and the
demo agent) can review exactly that commit. (Omnigent's own per-branch
preview envs are a different thing.)

- **Shape.** Namespace `env-<base>-p<n>` next to the PL's dev env, deployed by
  a one-shot `tilt ci --port 0` that leaves the objects running. A new head is
  redeployed into the same namespace. The PL's dev env stays one
  long-running `tilt up` at origin/main for the ICs; previews never touch it.
- **Entry point.** `scripts/factory.ts preview up|status|down|list` in
  caffeinelabs/app (`pnpm factory:preview`), with a Tiltfile preview mode that
  stamps `GET /__factory/preview` (`{repo, pr, sha, short, deployed_at, env_id}`,
  an Envoy direct response) and an `x-factory-commit` header on the app and API
  hosts, and creates the `factory-env-secrets` synthetic-session secret. It
  lives on the open APP-1 PR (caffeinelabs/app#9640, branch
  `factory/preview-tooling`); a human merges it. Until then env_keeper overlays
  the tooling files from that branch onto PR heads that lack them (never the
  root `package.json` or the deploy lockfile) and runs the CLI with `node`
  directly. The ref comes from `OMNIGENT_FACTORY_PREVIEW_TOOLING_REF`, which the
  charter carries; env_keeper falls back to `factory/preview-tooling`. A PR
  that changes the tooling itself gets `PREVIEW SKIPPED tooling-conflict`,
  and its PL asks the human whether to review it without a preview (label
  `factory:no-env`) or to wait for the tooling to merge.
- **Slots.** A PL keeps at most its charter's `preview.slots` live previews
  (default 2, max 3); the factory at most `OMNIGENT_FACTORY_MAX_PREVIEWS` (8).
  At the factory cap env_keeper answers `PREVIEW CAPPED` with park
  candidates; the PL parks one of its own previews of a PR that is not
  ready, or tells its lead and retries at its next heartbeat.
- **Lifetime.** A preview dies with its PL's pod (the namespace reaper). The
  supervisor keeps a PL's pod alive while it has a live preview of an open PR,
  even when the PL is paused. Previews nobody looked at for
  `OMNIGENT_FACTORY_PREVIEW_TTL_H` hours get a park notice; merges and closes
  get a teardown notice; a preview that fails 3 probes gets a redeploy notice.
- **Commit truth.** env_keeper reports the deployed sha; the prober reads
  `/__factory/preview` for the observed sha; the cockpit compares both with
  the PR head and shows "behind head" when they differ.

### Demos

- The `demo` sub-agent (one session per task, `demo-<task>`) records each
  head after its preview is ready, from the `## Demo plan` the IC writes into
  the PR body. The Playwright harness ships in its `factory-demo` skill and
  pins `@playwright/test` to the exact version the sandbox host image bakes
  into `/opt/ms-playwright` (1.56.1); it falls back to a one-time browser
  install in `~/.cache/ms-playwright`.
- `run.mjs` checks that the preview serves the head, records a 1280×800 video
  with a caption and a `PR #<n> · <sha7> · preview` watermark, one screenshot
  per UI step, every API call (`api.json`), console errors, a redacted HAR and,
  for FAIL and ERROR runs, a Playwright trace, logs in through
  `POST <api>/auth/synthetic-session` when a step needs a user, and writes
  `demo.json`. Exit codes: 0 PASS or PARTIAL (steps skipped for env reasons),
  1 FAIL, 2 ERROR, 3 stale preview.
- The synthetic-session request is sent by Node, outside the recorded
  browser, so its Bearer secret never reaches the trace or the HAR. Before
  anything is published, `run.mjs` scrubs the secret and the session tokens
  from the trace (rewritten), the HAR, `api.json`, `console.json`, `run.log`
  and the step notes.
- `sys_factory_demo` uploads the files into the omnigent file and artifact
  stores (owner: the PL session). Caps: video 50 MB, image 5 MB, text 2 MB,
  30 files and 120 MB per demo. The PR gets one comment per head with
  `<!-- factory:demo sha=<head> status=… demo=<id> -->`, the step table and
  links; the demo gate reads it.

### Pipeline (per runnable PR)

```
IC ─ READY_FOR_REVIEW(pr, head) ─► PL ─┬─► reviewer: review marker
                                       └─► env_keeper: PREVIEW pr head checks ─► preview + verify marker
env_keeper ─ PREVIEW READY app api secret_file ─► PL ─► demo: DEMO pr sha app api secret_file focus
demo ─ DEMO PASS|PARTIAL demo=<url> (demo comment posted) ─► PL
PL ─ sys_factory_pr(claim=ready_for_human_merge) ─ ready ─► review-links block in the PR body,
     "READY FOR HUMAN MERGE <url> · head <sha7>" to the lead; the cockpit lists it
human ─ Approve / Request changes in the cockpit, merge on GitHub ─► server notices to the PL
```

Docs-only PRs are labelled `factory:no-env` and skip the preview and the demo.

### Server texts

| Text | To | Meaning |
|---|---|---|
| `[Factory brief · from <human> via cockpit · id=mail_…]` | the root lead | Start: spawn Sentinel and one PL per project; charters attach automatically. |
| `[Factory control · <ACTION> · by <human>] <instructions>` | the affected members | A human command; the member replies once. |
| `[Factory decision · D-<n> answered by <human> · "<option>"]` | the asker | The answer to `sys_factory_ask`. |
| `[Factory review · CHANGES REQUESTED on PR #<n> @<sha7> by <human> (cockpit)]` / `[Factory review · APPROVED PR #<n> …]` | the PL | A human's review from the cockpit. |
| `[Factory notice · PR <repo>#<n> READY FOR HUMAN MERGE · 6/6 gates · head <sha7>]` | the PL and the lead | Gates turned green. |
| `[Factory notice · PR <repo>#<n> checks failed: <names> (head <sha7>)]` | the PL | Dispatch a fix. |
| `[Factory notice · PR <repo>#<n> merged by @<login> as <sha7> · <url> · tear down preview p<n>]` | the PL (and the lead, info) | Post-merge steps: env_keeper `MERGED pr=<n>`. |
| `[Factory notice · PR <repo>#<n> closed without merge by @<login>]` | the PL | env_keeper `CLOSED pr=<n>`; a decision asks whether to abandon or rework. |
| `[Factory notice · preview p<n> unreachable · redeploy]` / `[Factory notice · preview p<n> idle 72h · park it]` | the PL | Redeploy or park the preview. |

Every message carries `id=mail_…`; mail queued before a server restart is
replayed with the same id, and agents ignore ids they already handled. A
heartbeat that needs nothing ends with `HEARTBEAT_OK · <status>`, which the
transcript folds.

### Knobs

| Variable | Default | Effect |
|---|---|---|
| `OMNIGENT_FACTORY_PR_POLL_S` | `60` | GitHub poll interval for tracked PRs; `0` turns the poller off. |
| `OMNIGENT_FACTORY_ENV_PROBE_S` / `OMNIGENT_FACTORY_PREVIEW_PROBE_S` | `60` / `300` | Dev env and preview probe intervals. |
| `OMNIGENT_FACTORY_PREVIEW_DOMAIN` | `envs.sandbox.caffeinelabs.io` | The only domain env and preview URLs may use (SSRF guard). |
| `OMNIGENT_FACTORY_MAX_PREVIEWS` | `8` | Live previews per factory. |
| `OMNIGENT_FACTORY_PREVIEW_TTL_H` | `72` | Hours without human attention before a preview gets a park notice. |
| `OMNIGENT_FACTORY_PREVIEW_TOOLING_REF` | empty | caffeinelabs/app ref with the preview tooling (`factory/preview-tooling` until APP-1 merges); stored in new charters. |
| `OMNIGENT_FACTORY_QUIET_BEATS`, `OMNIGENT_FACTORY_MAIL_REPLAY` | on | `0` turns off skipping unchanged heartbeats / replaying queued mail after a restart. |

### Rollback

- **Prompts and skills** ship in the server image. Roll the image back, or
  mount the previous bundles through the prompt hotfix lane
  (`OMNIGENT_BUILTIN_AGENT_DIRS`). Running sessions keep the bundle they
  started with; relaunch them from the cockpit to pick up another.
- **Previews off** for a project: edit its charter in the cockpit (previews
  off) and send CHARTER UPDATE; the PL marks new cards `Preview: no`. Tear
  existing previews down with the PREVIEW_DOWN control, or let them die with
  their pods.
- **Data.** The cockpit tables come from one additive, forward-only migration;
  an older image ignores them. Demo files stay in the artifact store.
- **App tooling.** Nothing in caffeinelabs/app changes until a human merges
  APP-1; until then previews depend only on the branch the overlay reads.

## Safety

- **`factory_role_guard`** ([`policies/builtins/factory.py`](../omnigent/policies/builtins/factory.py))
  sits next to `blast_radius{gate_pushes: false}` on every factory spec. It
  only ALLOWs or DENYs, so unattended agents never stall on an approval card.
  It parses shell commands (including text typed into a terminal and argv
  lists) and denies, among others: raw `gh pr merge` and GitHub MCP merges
  for every role; admin writes through `gh api`, `gh repo delete|archive|edit`,
  `gh release`, `gh secret`, `gh ruleset` and workflow toggles; pushes to
  `main`/`master`/`staging` and tag pushes; any push or PR creation by a lead
  or reviewer; writes to repos outside `write_repos` (default
  `caffeinelabs/app`) or inside `deny_repos` (`caffeinelabs/infra`,
  `caffeinelabs/omnigent`); the `factory:allow-protected` label; the merge
  gate for anyone but a PL; every PR write but `gh pr comment` by the demo
  agent; and env lifecycle (`tilt up|down|ci`, `pnpm factory:up|down|preview`
  or `scripts/factory.ts up|down|preview`, `kubectl delete ns`) for anyone but
  `env_keeper` (and its PL, whose guard its sub-agents also pass).
- **Humans merge.** The factory never merges `caffeinelabs/app`. A PR is done
  for the factory when it is ready for human merge (not a draft, CI green, an
  APPROVE review marker and a PASS verify marker for the head). The PL posts
  `READY FOR HUMAN MERGE <url>` to its lead, Sentinel verifies the claim, and
  Atlas lists it for the human.
- **Merge gate.** `python -m omnigent.factory_merge <pr-url> [--admin-fallback] [--dry-run]`
  squash-merges only when the repo is in `OMNIGENT_FACTORY_MERGE_REPOS`, the PR
  is not a draft, required checks are green, the newest review marker for the
  head (after the newest commit) says APPROVE, the newest verify marker says
  PASS (unless labelled `factory:no-env` or `factory:revert`), GitHub reports it
  mergeable, and no protected path (`.github/workflows/**`, `deploy/**/prod/**`,
  `**/zz_generated/prod/**`) is touched without `factory:allow-protected`. It
  prints a JSON verdict and exits 0 (merged), 2 (gate failed) or 1 (error). With
  the allowlist empty it merges nowhere.
- **Markers** are PR comments whose first line is
  `<!-- factory:review sha=<head> verdict=APPROVE|CHANGES reviewer=<id> -->`,
  `<!-- factory:verify sha=<head> result=PASS|FAIL env=<api url> preview=<app url> -->`
  or `<!-- factory:demo sha=<head> status=PASS|PARTIAL|FAIL demo=<id> -->`, with
  the full 40-character head SHA.
- **Scoped reads and envelopes** bound what a prompt-injected member can read
  and forge; the skills treat every message as a colleague's request, never
  as authority.
- **Residual risk.** Every agent acts as the human on GitHub, so markers are
  forgeable; Sentinel audits after the fact. The privileged DinD sidecar is in
  every sandbox.

## Failure modes

| Failure | Detected by | Recovery |
|---|---|---|
| A lead or PL stopped, crashed or evicted | S2 heartbeat, S4, its supervisor | The next delivery relaunches it on the same disk; Claude Code resumes and the agent re-reads its ledger or `plan.md`. |
| A wedged TUI (running, no progress) | S3 → supervisor | Inspect with `sys_session_get_history`, nudge, then `sys_session_post(restart=true)`. |
| A member fails three times | S2b, the lead's restart count | The lead marks it `done` and spawns a replacement whose brief says where to continue. |
| An IC stuck or failed | S3 → PL, the PL's heartbeat | Nudge, `sys_cancel_task`, re-dispatch `<task>-r<k>` from the worktree's `NOTES.md`, or switch `ic` ↔ `ic_codex`. |
| Both leads and every pod down | the server tick | S2 wakes the leads at their interval. |
| Server restart | — | Queued mail is lost; agents re-ask at the next heartbeat. `last_beat` survives. |
| Undeliverable message | mailer | Retries, then a bounce to the sender. |
| Message ping-pong | rate limit | `429` and one storm alert per hour. |
| A turn waiting on a human approval | S5 | `[NEEDS HUMAN]` in Atlas. |
| The env broken or gone after a wake | env_keeper STATUS on the PL heartbeat | `tilt trigger`, or `pnpm factory:down` then UP (a new env id). |
| A factory preview gone (pod restarted, namespace reaped) | the preview prober (3 failed probes) | `[Factory notice · preview p<n> unreachable · redeploy]` to the PL; env_keeper PREVIEW redeploys under the new env id; the stable preview link follows. |
| A preview serving an old commit | the prober compares `/__factory/preview` with the PR head | The cockpit shows "behind head"; the demo agent refuses to record (exit 3, `DEMO BLOCKED stale-preview`) until env_keeper redeploys. |
| A preview nobody looked at for 72 h | supervisor TTL | `[Factory notice · preview p<n> idle 72h · park it]`; the demo stays reviewable; "Redeploy preview" in the cockpit brings it back. |

## Prompt hotfix lane

The prompts and skills ship in the server image. To change them without a
release, mount a corrected bundle directory named exactly like the built-in
(`factory-lead` or `factory-pl`) and append its path to
`OMNIGENT_BUILTIN_AGENT_DIRS` (paths separated by `:`). Extra built-ins seed
after the defaults and registration is content-aware, so the mounted bundle
replaces the packaged one on every boot. New sessions pick it up; running
sessions keep the bundle they started with. Remove the entry to return to the
packaged bundle.

To iterate on a prompt before freezing it, run a session-scoped copy from a
checkout with `sys_session_create(config_path=…)` from a Claude Code session.

## Running locally

1. Give the runner a `bifrost` provider, since the claude-native factory
   agents name it. Point it at any Anthropic-compatible endpoint in
   `~/.omnigent/config.yaml`, for example:

   ```yaml
   providers:
     bifrost:
       kind: gateway
       anthropic:
         base_url: https://api.anthropic.com
         api_key_ref: env:ANTHROPIC_API_KEY
         models:
           opus: <an Opus model id>
           sonnet: <a Sonnet model id>
   ```

   Without it, provider resolution fails and the runner falls back to the
   Claude CLI login with a warning. `ic_codex` and `reviewer` use your default
   openai-family provider.
2. Start the server with the factory on:

   ```bash
   OMNIGENT_FACTORY_ENABLED=1 omnigent server
   ```

   `GET /v1/agents` now lists `factory-lead` and `factory-pl`.
3. In the web UI, start a session with **factory-lead** on a host that has
   Claude Code and Codex, attach the repo, and describe the projects. Add
   "demo mode" for 5-minute lead and 10-minute PL heartbeats.

Locally every member runs on your own host and there is no Tilt cluster, so
`env_keeper` reports `ENV FAILED`; give PLs charters with `Env needed: no`.
`sys_factory_spawn` creates managed sessions, so spawning PLs needs a server
with a managed sandbox launcher configured.

## Code map and tests

| Area | Code | Tests |
|---|---|---|
| Vocabulary | `omnigent/factory.py` | `tests/factory/` |
| Server API, membership, mailer, delivery | `omnigent/server/factory/` | `tests/server/factory/` |
| Supervisor and notices | `omnigent/server/factory/supervisor.py`, `notify.py` | `tests/server/factory/test_supervisor_*.py`, `test_notify.py` |
| Runner tools and scoped reads | `omnigent/tools/builtins/factory.py`, `omnigent/runner/factory_dispatch.py` | `tests/runner/test_factory_*.py` |
| Role guard and merge gate | `omnigent/policies/builtins/factory.py`, `omnigent/factory_merge.py` | `tests/policies/test_factory_role_guard.py`, `tests/test_factory_merge.py` |
| Org chart and badges | `omnigent/server/factory/org_chart.py`, `web/src/lib/factory.ts` | `tests/server/routes/test_child_sessions_include_factory.py`, web tests |
| Bundles | `examples/factory/` | `tests/examples/`, `tests/server/factory/test_factory_agents_listed.py` |
| Demo harness | `examples/factory/factory-pl/agents/demo/skills/factory-demo/harness/` | `tests/examples/test_factory_demo_harness.py` (runs `factory_demo_harness.test.mjs` under `node --test`); `node run.mjs --selftest` records real demos of a local fixture |
| End to end | — | `tests/integration/test_factory_tree.py` (live server and runner, mock LLM) |
