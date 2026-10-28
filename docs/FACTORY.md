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
| `OMNIGENT_FACTORY_ARTIFACT_LOCATION` | unset | Where demo files (video, screenshots, `api.json`, HAR, traces) are written: `s3://<bucket>[/<prefix>]` (credentials from the standard AWS chain, IRSA included; `AWS_REGION` must be set for AWS S3; `AWS_ENDPOINT_URL_S3` for MinIO or R2) or a directory. Unset keeps them session files of the PL in the server's artifact store, shown in its Files panel. Each demo file records the location it was written to and is always read from there, so demos stored before a change keep playing. Files are still served only through `/v1/factory/demos/…` behind omnigent auth (a Range is a ranged read of the store, at most 4 MiB per response; no presigned links). A store that fails (unreachable, throttled, denied) answers 503 `artifact_store_unavailable` with `Retry-After`. A malformed location fails server start. |
| `OMNIGENT_BUILTIN_AGENT_DIRS` | unset | Extra built-in bundles, seeded after the defaults. Used as the prompt hotfix lane (below). |
| `OMNIGENT_FEATURES` | unset | `factory` in the list shows the cockpit (`/factory`, the sidebar entry, the Inbox's factory decisions). It is also on wherever `OMNIGENT_FACTORY_ENABLED=1`; list it explicitly to document it, drop it (with the factory off) to hide the UI. |

Invalid timing values fall back to their defaults with a warning. On start the
server logs one line naming what runs, e.g. `factory services started:
supervisor, env_prober, pr_poller, prune, reconcile`.

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
| `omnigent.factory.default_repo` / `default_base` | root | The factory's default repo and base (start form); a project's blank base inherits it. |
| `omnigent.factory.base_head.<key>` | root | `<sha> <moved_at> <repo>#<base>` (the pair as `sha256:<hex>` when too long): the head the base watcher last saw (server-written). |

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

- `sys_factory_rules()` (every role), `sys_factory_insights(kind, …)` (leads,
  PLs, the planner), `sys_factory_retro(…)` and `sys_factory_propose(kind, …)`
  (leads and PLs) — see Self-modification.

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
| S10 decision escalation | an open blocking decision whose person has not been seen for `decisions.escalate_after_m` (30 min) / any still open after `decisions.owners_after_m` (120 min) | Its assignee widens to `operators`, then to `owners`; `decision.escalated` each step. |

Paused and done members get no keepalive and no heartbeats, so their runners
idle out and their sandboxes are reclaimed.

## Cockpit, previews and demos

The human runs the factory from the **cockpit** (`/factory`, one factory at
`/factory/<factory id>`). Every primary object has a page under it: the
Overview (`/`), Projects (`/projects`), a project (`/projects/<slug>`, its
sections as anchors such as `#demos`), a PR (`/prs/<pr id>`; a PR number
resolves too), Activity (`/activity`), the Room (`/room`) and Settings ▸
People (`/settings/people`); `?decision=D-<n>` highlights a decision and ⌘K
jumps or runs a slash command. Old links still land: `?review=<n>` and
`?pr=<id>&preview=unavailable` (PR bodies, the stable preview link) open the
PR page, `?project=<slug>` the project page, `?tab=activity` Activity.
Everything on it comes from the server: a
GitHub poller computes each PR's gates, a prober checks envs and previews, and
an event log records what agents did. Agents add detail through four tools;
if they stay silent the board is still right.

### Routes

| Route | Who | What |
|---|---|---|
| `GET /v1/factory/factories`, `GET /v1/factory/factories/{fid}` (ETag), `…/events` | human (READ on the root) | Factory list (with the viewer's role, who shared it and what is theirs), the snapshot (projects, PRs with gates, previews, demos, decisions, members, health), the activity log. |
| `GET /v1/factory/factories/{fid}/projects/{slug}/demos` | human (READ on the root and the project's PL) | Every recorded demo of the project's PRs, grouped by PR (groups and runs newest first): the project page's Demos section. |
| `POST /v1/factory/factories`, `POST …/{fid}/projects`, `PATCH …/projects/{slug}` | human | Start a factory from the form (stores charters, queues `[Factory brief … via cockpit]`; `projects` may be empty with a `brief`), add or edit a project. |
| `POST …/{fid}/projects/{slug}/cancel`, `…/split`, `…/merge` | human (EDIT) | Project lifecycle (see Projects and proposals). |
| `GET …/{fid}/proposals?kind=&status=`, `GET …/proposals/{ref}`, `POST …/proposals/{ref}/apply` (`{changes?}`), `…/reject` (`{note?}`), `…/undo` | human (EDIT; MANAGE for an owner-class proposal) | Proposals `C-<n>`; apply and reject answer the mirrored decision, so one of two clicks wins. |
| `POST /v1/factory/factories/{fid}/control` | human (EDIT; MANAGE for STOP, RESTART, RELAUNCH) | PAUSE, RESUME, STOP, RESTART, RELAUNCH, cadence, PREVIEW / PREVIEW_DOWN of a PR, ENV STATUS: a server-stamped `[Factory control …]` to the members. |
| `GET /v1/factory/decisions`, `POST …/decisions/{id}/answer`, `…/withdraw` | human | Open decisions (also in `/inbox`); the answer is delivered to the asker as `[Factory decision · D-<n> answered …]`. |
| `POST /v1/factory/prs/{id}/feedback`, `…/refresh` | human | Approve or request changes (`[Factory review …]` to the PL); re-poll GitHub now. |
| `GET /v1/factory/prs/{id}/preview` | human | Stable live-preview link: 302 to the current preview app URL, or back to the cockpit when there is none. |
| `GET /v1/factory/demos/{demo_id}/files/{file_id}` | human (READ on the PL) | Demo files with Range/206 (a ranged read of the store) and ETag; only images and video inline, never HTML or SVG. |
| `POST /v1/factory/agent/decisions`, `POST …/agent/prs`, `PUT …/agent/envs`, `POST …/agent/demos` (multipart), `GET …/agent/charter` | runner (the tools below) | Agent reports. |
| `POST /v1/factory/agent/proposals`, `GET …/agent/projects` | runner (`sys_factory_project`) | Propose project changes; leads for every project, a PL for its own. |
| `GET /v1/factory/factories/{fid}/tasks?project=`, `POST …/{fid}/tasks`, `PATCH …/{fid}/tasks/{id or T-n}`, `GET …/{fid}/graph?kind=work\|bus\|flow\|org&project=&window=` | human (READ; EDIT and human origin to write) | The work graph (see below); adds and edits mail the PL `[Factory control · TASK ADD\|TASK EDIT …]`. |
| `PUT /v1/factory/agent/tasks`, `GET …/agent/tasks?view=list\|ready\|get`, `POST …/agent/tasks/{key}/start\|done\|fail\|cancel\|report` | runner (`sys_factory_task`) | The PL writes its project's tasks; leads read; the bound worker reports. |
| `GET /v1/factory/preflight` | human | GitHub connected, agents seeded, sandbox host, factory enabled; `default_repo`, `default_base` and `max_projects` for the start form. |
| `GET /v1/factory/factories/{fid}/spans?kind=&project=&pr_id=&task_id=&since=`, `GET /v1/factory/prs/{id}/timeline`, `GET …/{fid}/cost?group_by=day,project,role,agent,model`, `GET …/{fid}/export?tables=&format=ndjson\|csv` | human (READ on the root; hidden projects dropped) | Telemetry (see below). The export streams and never carries transcript content. |

### Tools

| Tool | Roles | Use |
|---|---|---|
| `sys_factory_ask(question, options, context?, blocking=true, dedupe_key?, for?, subject?)` | every role | Ask the human. Opens D-<n> in the cockpit and the inbox and returns at once; the answer arrives as a message. Replaces DECISIONS NEEDED lists. `for` routes it to `owners`, `operators` or a person on the roster (default: the project's human owner, else operators). |
| `sys_factory_pr(url, task?, claim?, sentinel_verdict?, head_sha?, note?)` | every role (`sentinel_verdict`: leads) | Register a PR, claim `opened` / `ready_for_human_merge` / `abandoned`, or record Sentinel's verdict. Returns the server's gates `{ready, gates, reasons, claim_mismatch}` and the PR body's review links (`review_url`, `preview_url`). |
| `sys_factory_env(state, env_id, urls, deployed{sha, ref, tooling_overlay?}, pr?, namespace?, resources?, note?)` | `project_lead`, `env_keeper` | Report the dev env (no `pr`) or the factory preview of PR `pr`. `state=deploying` beyond `OMNIGENT_FACTORY_MAX_PREVIEWS` live previews is refused with `409 preview_cap` and park candidates. |
| `sys_factory_demo(pr_url, head_sha, title, manifest_path?, files?, …)` | `project_lead`, `env_keeper`, `demo` | Upload a demo (with `manifest_path`, everything the harness's `demo.json` lists) and get back `comment_markdown` / `comment_path` to post on the PR. |
| `sys_factory_task(op, tasks?, key?, project?, harness?, result?, note?)` | `project_lead` (own project), `lead` (`get`, `list`), `ic` (`report` on its own task) | The work graph: `upsert`, `ready` (the wave with `title_to_use`, `card_text`, `cost_budget`), `start`, `done`, `fail`, `cancel`, `get`, `list`, `report`. |
| `sys_factory_set(stage=…)` | `project_lead` on itself | Claim the project's stage; the cockpit shows it next to the stage it derives. |
| `sys_factory_project(changes, rationale, reason?, reply_to?, analysis_file?, dedupe_key?, withdraw?)` | `lead` (every op), `project_lead` (update, split or cancel of its own project) | Propose 1-12 project changes as one `C-<n>`; no arguments lists the projects. |

### Projects and proposals

Projects change through one write path, `server/factory/projects.py`: create,
update, cancel, split and merge cores that the forms, the human routes and
applied proposals call with the acting person, agent or the server.

- **Lifecycle.** `factory_projects.status` is `active`, `cancelled`, `merged`
  (`parent_slug` names the survivor) or `done`. Cancel stops the PL (or
  pauses it with previews kept) and leaves its PRs open. Split and merge move
  PRs only through `pr_map`; an open PR left on a closing project refuses
  the plan with `unmapped_prs`, and the receiving PL's CHARTER UPDATE names
  the inherited PRs to preview again. The snapshot overlays `cancelled`,
  `merged → <slug>` and `waiting on …`, and folds closed projects last.
- **Proposals** (`server/factory/proposals.py`). `sys_factory_project` is
  validated with the cockpit's models (errors carry `changes[1].project.slug`),
  against slugs (also those of pending proposals), the project and pod caps,
  the factory's repos and default base, `depends_on` cycles (409 `cycle` with
  its path) and scope overlaps (a warning). The autonomy policy
  (`server/factory/autonomy.py`, root label `omnigent.factory.autonomy_projects`:
  `confirm`, `requested` by default, or `grace`) decides what applies at once
  (a create a person asked for, `reply_to` a Room message or the start
  brief's mail id) with a 10-minute Undo; the rest waits on a `proposal`
  decision (Apply, Edit & apply, Reject) that re-checks `based_on` (409
  `stale_proposal`). Setting, raising or removing a USD `budgets` cap is the
  owners' (owner class; the cockpit add and charter PATCH need MANAGE for it).
  An apply that fails part-way takes back the projects it created before
  their PLs started. Pending proposals expire after 72 h; the proposer gets
  `[Factory proposal · C-<n> applied|rejected|expired|undone …]`.
- **Dependencies are checked by every core**: an unknown `depends_on` is 400
  `unknown_dependency`, a cycle 409 `cycle` with its path, from the cockpit
  forms as from proposals.
- **Server spawn** (`server/factory/spawn.py`). An applied project's PL is
  created by the server with `sys_factory_spawn`'s exact body, in process,
  as the factory's creator (a 300 s runner token), then briefed (`[Factory
  brief · from <who> via C-<n>`), logged (`member.spawned`) and announced to
  Atlas (`[Factory notice · PL·<slug> spawned by the server · C-<n>]`). Over
  `factory_max_pods` it opens a decision for owners (asked once per project)
  and spawns once a pod is free. After two failed creates, without a runner token (header auth), or
  with the root label `omnigent.factory.engine_spawn=lead`, Atlas is mailed
  the spawn call instead. `sys_factory_spawn` answers `server_owns_spawn` for
  a PL the server runs; a lead recovering a server-owned project without a PL
  takes its spawn over.
- **Dependencies** (`server/factory/gating.py`). A project waits on its
  `depends_on` (`project.held`) until each dependency's PR merged
  (`depends_mode=merged`) or is ready for human merge (`ready`); release runs
  on `stage.changed`, `pr.merged`, `pr.ready`, `base.moved` and a minute sweep
  (`project.released`, then the spawn). The sweep also finishes a spawn a
  restart cut off, once.

### People

A factory is shared through its roster (`factory_people`): **viewers** see
everything, **operators** steer (answer decisions, review, pause, resume,
preview, add projects), **owners** also stop, restart, relaunch and manage
people; the **creator** is an owner whose GitHub and Databricks identity the
agents run as. Each role is copied as an ordinary session grant (READ, EDIT,
MANAGE) onto every top-level member the creator owns, including PLs spawned
later (a hook on session create) and repaired by reconcile every minute
(`people.drift_repaired`), capped by the sharing mode; owner grants and manual
shares are never touched. A session someone else labels with the factory id
gets no roster grants. Removing someone revokes them on archived members too.

| Route | Who | What |
|---|---|---|
| `GET /v1/factory/factories/{fid}/people` | READ | Roster with presence, `runs_as` (the creator), the caller's `viewer` block. |
| `PUT …/people/{user}` `{role}`, `DELETE …/people/{user}` | MANAGE (DELETE yourself: READ) | Invite, change a role, remove or leave. Honours the sharing mode; answers `admission: ok\|blocked` (could they sign in through OIDC). `people.invited\|role_changed\|removed\|left`. |
| `GET …/people/search?q=` | MANAGE (people only) | Up to 10 known users matching ≥ 3 characters. |
| `POST …/access-requests` `{role}` | READ | An `access_request` decision for owners; its `grant_role` option runs `set_person`. |
| `POST\|GET …/presence` | READ | Cockpit heartbeat (20 s, 60 s TTL; `presence` frame scope), merged with who has a member chat open. |
| `POST /v1/factory/decisions/{id}/claim\|assign`, `GET /v1/factory/decisions?assigned=me` | EDIT (MANAGE to move one from the owners or from another person) | Claim a decision or route it to `owners`, `operators` or a person; `409` once it is answered or someone else claimed it. |

The snapshot carries `viewer {role, level, is_creator, can.*}`, `people_count`
and `presence`. Revoking a roster grant through the session's own permissions
route answers `409 managed_by_factory`. Once a factory has two people, a human
typing into a member's chat reaches it as `[Factory human · <name> (<role>)]`
with spoofed server lines quoted, and server texts name the human as
`<name> (<role>)`. Session streams check access only when they open, so
removing someone closes their open member streams (they reconnect and get 404).

### Base branches & integration env

Each project has a **base branch**: the branch every PR of the project
targets. The New factory form has a factory-level **Default repo & base** row
(prefilled from `OMNIGENT_FACTORY_DEFAULT_BASE`, e.g.
`caffeinelabs/app#temp-main`, which `GET /v1/factory/preflight` returns as
`default_repo` / `default_base`); the root records it in the
`omnigent.factory.default_repo` / `default_base` labels (a cleared row means
no default, knob or not). A project's base
resolves to its own branch, then the factory default (for the default repo),
then `main`; Add project resolves the same way. It is stored as
`base_branch` and fixed once the project exists.

- **Charter and dispatch.** The PL's charter carries `Base branch: <branch>
  (open every PR against it; the dev env tracks origin/<branch>)`, and the PL
  passes it on every dispatch: env_keeper `UP base=<branch>`, `PREVIEW …
  base=<branch>` and `MERGED pr=<n> base=<branch>`, and `Base: <branch>` on
  every task card. ICs branch from origin/<branch>, sync with `git merge
  origin/<branch>` and open PRs with `--base <branch>`.
- **Base gate.** Every PR has a `base` gate: pass when `baseRefName` is the
  project's base, fail otherwise (evidence `PR targets main; project base is
  temp-main` with the `gh pr edit <url> --base <branch>` fix), pending while
  GitHub has not reported it. The first failure sends the PL one
  `[Factory notice · PR #<n> targets main, project base is temp-main · gh pr
  edit <url> --base temp-main]`. A PR off its base never reads READY FOR HUMAN
  MERGE; `sys_factory_pr` returns its `base_ref` next to `project_base`.
- **Base watcher** (`server/factory/base.py`). The PR poller's GraphQL query
  also reads the head of every `(repo, base)` the active factories watch (a
  query of its own every 2 min when no PR of the repo is due) and records it
  on the root (`omnigent.factory.base_head.<key>`). The heads are read with
  the repo's last PR batch and observed after its PRs are applied, so a
  merge and the move it caused in one poll carry the merged PR. When the head
  moves it emits `base.moved {repo, base, from, to, pr}`, mails the integration PL to
  redeploy and every work PL with an open PR on that base `[Factory notice ·
  base temp-main moved to <sha7> · sync open branches: git merge
  origin/temp-main]` (at most every 30 min per PL), and runs the
  `on_base_moved` hooks. Merges nobody registered are caught the same way.
- **Integration env.** With "Keep a live env at `<base>`" on (the default
  when the base is not `main`), the start also creates project `base-env`
  with `charter.kind = "integration"`: an ordinary PL plus env_keeper keeping
  Tilt at the head of origin/<base> for humans to try. Atlas spawns its PL
  with `heartbeat_s=0`; it opens no tasks, ICs or PRs, sends env_keeper `UP
  base=<base>` at KICKOFF and on every `[Factory notice · base <base> moved
  <old7> → <new7>]`, smoke-checks `<app>/__factory/preview` (or
  `<api>/version`) and reports with `sys_factory_env`. The snapshot shows it
  as `factory.base = {repo, branch, head_sha, moved_at, env, env_project}`;
  it is left out of the stage strip.
- **temp-main.** To trial the factory without touching `main`, cut a test
  branch such as `temp-main` from main and start the factory on
  `caffeinelabs/app#temp-main`; every factory PR lands there and humans merge
  them. The preview tooling ref (`OMNIGENT_FACTORY_PREVIEW_TOOLING_REF`,
  default `main`) is separate: it only names where heads that predate the
  tooling get it from.

### Factory previews

A **factory preview** is the app deployed at one PR head so humans (and the
demo agent) can review exactly that commit. (Omnigent's own per-branch
preview envs are a different thing.)

- **Shape.** Namespace `env-<base>-p<n>` next to the PL's dev env, deployed by
  a one-shot `tilt ci --port 0` that leaves the objects running. A new head is
  redeployed into the same namespace. The PL's dev env stays one
  long-running `tilt up` at origin/<base> (the project's base branch) for the
  ICs; previews never touch it.
- **Entry point.** `scripts/factory.ts preview up|status|down|list` in
  caffeinelabs/app (`pnpm factory:preview`), with a Tiltfile preview mode that
  stamps `GET /__factory/preview` (`{repo, pr, sha, short, deployed_at, env_id}`,
  an Envoy direct response) and an `x-factory-commit` header on the app and API
  hosts, and creates the `factory-env-secrets` synthetic-session secret. It
  is on caffeinelabs/app main since caffeinelabs/app#9640, so a PR head that
  includes it (current main, or a base branch cut from it) runs it natively
  (`pnpm -s factory:preview up …`). A head that predates it has no `preview`
  subcommand: env_keeper fetches the tooling ref (`main`) and overlays the
  tooling files from it (never the
  root `package.json` or the deploy lockfile), then runs the CLI with `node`
  directly. The tooling ref is `main`; `OMNIGENT_FACTORY_PREVIEW_TOOLING_REF`,
  which the charter carries, can point the overlay at another ref; a charter
  that still names the deleted tooling branch reads as `main`. An old-base
  PR whose diff against origin/<base> changes the tooling itself gets
  `PREVIEW SKIPPED tooling-conflict`; its PL asks the author to merge
  origin/<base> (the new head has the tooling
  natively), or the human whether to review it without a preview (label
  `factory:no-env`).
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
  stores (owner: the PL session), or, with
  `OMNIGENT_FACTORY_ARTIFACT_LOCATION` set, into that store under
  `demos/<factory id>/<demo id>/<file id>-<name>` (the demo row is then their
  only record; they are not in the PL's Files panel). Caps: video 50 MB,
  image 5 MB, text 2 MB, 30 files and 120 MB per demo. The PR gets one comment per head with
  `<!-- factory:demo sha=<head> status=… demo=<id> -->`, the step table and
  links; the demo gate reads it.
- With ffmpeg, `run.mjs` also renders `video/demo.gif` (the first 20 s at
  800 px and 8 fps, at most 5 MB) for the demo mirror below.

### Demo mirror

GitHub reviewers see the demo in the PR itself through a reviewer-facing copy
in a private repo (`OMNIGENT_FACTORY_DEMO_MIRROR_REPO`, e.g.
`caffeinelabs/factory-demos`; unset, nothing is mirrored).

- **Writer.** The server, inside `POST /v1/factory/agent/demos` after the
  files are stored, with a 10 s timeout, through the Git Data API as the
  factory creator (the connection the PR poller uses). Agents gain no write
  repo.
- **Layout.** Each demo gets its own branch `d/<factory8>/pr-<n>-<sha7>-<demo8>`
  with one parentless commit holding `<project>/pr-<n>/<sha7>/{poster.png,
  demo.gif, steps.md}`; a demo re-recorded at the same head gets a new branch,
  so older comments keep their embeds.
- **Budget.** At most 6 REST calls per demo: `GET /rate_limit` (skipped when a
  reading from the last 5 min exists), a blob each for the poster (≤ 2 MB) and
  the GIF (≤ 5 MB), a tree with `steps.md` inline, the commit and the ref. It
  is skipped while fewer than 500 core calls remain.
- **Comment.** On success `comment_markdown` embeds
  `https://github.com/<repo>/blob/<commit>/<path>/demo.gif?raw=true` and the
  poster (they render for org members who can read the repo); on a failure or
  timeout it keeps the cockpit links only. Events: `demo.mirrored`,
  `demo.mirror_failed`, `demo.mirror_skipped`.
- **Never read back.** The factory artifact store stays the store of record;
  the cockpit plays demos from it and never reads GitHub. The repo needs a
  first commit (the Git Data API refuses an empty repository); old `d/*`
  branches may be deleted by a human.

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
| `[Factory notice · PR <repo>#<n> READY FOR HUMAN MERGE · 7/7 gates · head <sha7>]` | the PL and the lead | Gates turned green (8/8 with the demo gate). |
| `[Factory notice · PR #<n> targets <branch>, project base is <base> · gh pr edit <url> --base <base>]` | the PL | The base gate failed: retarget the PR. |
| `[Factory notice · base <base> moved to <sha7> · sync open branches: git merge origin/<base>]` | work PLs with an open PR on the base | Sync open branches. |
| `[Factory notice · base <base> moved <old7> → <new7>]` | the integration PL | Redeploy the live env at the new head. |
| `[Factory notice · PR <repo>#<n> checks failed: <names> (head <sha7>)]` | the PL | Dispatch a fix. |
| `[Factory notice · PR <repo>#<n> merged by @<login> as <sha7> · <url> · tear down preview p<n>]` | the PL (and the lead, info) | Post-merge steps: env_keeper `MERGED pr=<n> base=<base>`; sibling ICs merge origin/<base> (the PR's base). |
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
| `OMNIGENT_FACTORY_PREVIEW_DOMAIN` | `envs.sandbox.caffeinelabs.io` | The only domain env and preview URLs may use (SSRF guard). `OMNIGENT_ENV_DOMAIN` (the sandboxes' env domain) is read when it is unset. |
| `OMNIGENT_DOMAIN` | unset | Public host of this server, e.g. `omnigent.caffeinelabs.io`. Demo PR comments and `demo_url` use it for absolute links; unset, they use the address the runner called. |
| `OMNIGENT_FACTORY_MAX_PREVIEWS` | `8` | Live previews per factory. |
| `OMNIGENT_FACTORY_DEFAULT_BASE` | unset | `owner/repo#branch` new factories default to (the start form's Default repo & base); a bare `owner/repo` means its `main`. |
| `OMNIGENT_FACTORY_DEMO_MIRROR_REPO` | unset | Private `owner/repo` demo posters and GIFs are mirrored to for the PR comment; unset, nothing is mirrored. |
| `OMNIGENT_FACTORY_PREVIEW_TTL_H` | `72` | Hours without human attention before a preview gets a park notice. |
| `OMNIGENT_FACTORY_PREVIEW_TOOLING_REF` | empty (recommended: `main`) | caffeinelabs/app ref the preview tooling is overlaid from onto PR heads that predate it; stored in new charters. Not the project's base branch. Empty: env_keeper uses `main`. |
| `OMNIGENT_FACTORY_QUIET_BEATS`, `OMNIGENT_FACTORY_MAIL_REPLAY` | on | `0` turns off skipping unchanged heartbeats / replaying queued mail after a restart. |
| `OMNIGENT_FACTORY_INVARIANTS` | on | `0` leaves the `factory_invariants` admin default policy unseeded (see Invariants). |
| `OMNIGENT_IMAGE_TAG`, `OMNIGENT_SERVER_SHA` | unset | Image tag and commit recorded in the platform version (`factory_versions`). |
| `OMNIGENT_FACTORY_RETRO_HOUR` | `6` | UTC hour after which Sentinel gets its daily retro beat. |
| `OMNIGENT_FACTORY_SMITH`, `…_SMITH_GITHUB_LOGIN`, `…_SMITH_GITHUB_TOKEN`, `…_SMITH_REPO` | off | Tier 2 prerequisites (see Self-modification); the repo defaults to `caffeinelabs/omnigent#staging`. |

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
- **App tooling.** The preview tooling is part of caffeinelabs/app main
  (caffeinelabs/app#9640). PR heads that include it use their own copy; only
  older heads read it from the overlay ref (`main`).

## Telemetry

Durable facts every metric builds on (`omnigent/server/factory/telemetry/`):

- **Retention.** The nightly prune drops only `message.*`, `notice.*` and
  `preview.health` after 14 days and `heartbeat.*` after 2; every lifecycle
  event is kept. `pr.claim` stores `claim_mismatch`; PRs record GitHub's
  `opened_at` and `first_ready_at` (`ready_at` stays the latest).
- **Versions.** After the bundle seed the server upserts its platform version
  (`factory_versions`: image tag, server sha, each factory bundle's content
  sha, the `OMNIGENT_FACTORY_*` knobs with secrets redacted), deduped by
  content hash. Members carry `omnigent.factory.version` and
  `omnigent.factory.bundle=<agent_id>/<sha12>` from factory start (the root
  lead), spawn, restart (cockpit or peer) and relaunch.
- **Spans** (`factory_spans`, never pruned, idempotent on `dedupe_key`). An
  event-log recorder writes `project`, `stage`, `ready_wait`, `decision`,
  `task`, `env`, `preview`, `demo`, `incident`, `intervention` and `proposal`
  spans; every GitHub refresh writes `pr`, one `pr_round` per head and one
  `gate` per head and gate with `attrs.first_pass`; the supervisor puts a
  sub-agent's cost on its `task` span. Spans carry the version id and the
  root's `def_version`. Only a sha seen as the PR's head (`headRefOid`, a
  `gate.changed` event) opens a round; marker shas other than 7-40 hex are
  ignored, and a verdict on a sha without a round adds nothing.
- **Cost ledger** (`factory_cost_daily`). Every turn's display cost and
  tokens, booked by factory, project (`""` for the leads), role, agent, model
  and UTC day from the session's labels or its root's.
- **Heal.** The nightly loop rebuilds and closes spans from the PR, decision,
  demo and project tables, the sessions and the remaining events before it
  prunes; a second run changes nothing, and the first one backfills. Each
  section, PR and replayed event heals on its own (a failure skips only it),
  and a span a live writer closed after heal read the log keeps that close.
- **Export.** Rows the cockpit hides are dropped (sessions outside the
  factory need READ, as in the activity feed); summaries that quote agent
  text (mail, reasons, blockers, free-text labels) and free-text data keys
  are left out; CSV cells that start with `= + - @` are prefixed with `'`.

## Self-modification

Agents may change the factory, never silently and never its guardrails
(`omnigent/server/factory/definition.py`, `tuning.py`, `evaluate.py`).

**Tier 0** stays in code: policies, gates, the merge gate, the guard, marker
formats, setting bounds, the approval classes, metric and benchmark
definitions and secrets. A change naming any of them, or any path outside the
schema, is refused with 422 `tier0`.

**Tier 1: the versioned definition.** Each factory has immutable versions in
`factory_definitions`; the root label `omnigent.factory.def_version` pins the
one in force. `definition_for(fid)` (5 s cache) reads each value from the
doc, then today's env default, then the code default, and clamps it to the
code bounds (an env value outside them widens them) and to the env ceilings
of `mail.*`, `previews.ttl_h|max` and `caps.max_projects`. v1 is seeded on the
first read from today's env, and the root's `engine_mode`,
`autonomy_projects` and `engine_spawn` labels move into it. The doc holds
`engine` (mode, spawn, next_hints), `cadence` (lead, project_lead,
demo_project_lead), `supervisor` (stuck_s, blocked_escalate_s, quiet_beats),
`mail`, `previews` (TTL, max, probe intervals), `caps`, `loops.*` budgets,
`budgets`, `autonomy`, `decisions` (escalation minutes), `alerts` (detector
thresholds), `models` (aliases) and `rules`. The supervisor
(`SupervisorConfig.for_factory`), the mailer, previews, the env prober, the
loop engine, the detectors, decision escalation, PL spawns, task cards and
briefs all read it.

| Paths | Class |
|---|---|
| `cadence.*` | A when slower, else B (0 = off is B) |
| `loops.*.attempts\|rounds\|per_head\|usd` | A when lower, else B |
| other `loops.*` | B |
| `mail.*` | A when lower, else B |
| `supervisor.*`, `alerts.*`, `decisions.*` | A |
| `previews.*`, `caps.*`, `rules` | B |
| `engine.*`, `autonomy.*`, `budgets.*`, `models.*` | B (owners) |

**Proposals** (`C-n`, `sys_factory_propose`): leads propose tuning, rules and
code; a PL proposes rules for its own project. Each cites evidence ids (422
without) and one expected metric effect; at most 2 per agent per day.

- **A** goes to the other lead, who co-signs
  (`sys_factory_propose(op="cosign", kind=…, id="C-n")`; never their own).
  It applies at once as a new version on **trial** (default 48 h).
- **B** opens an owners `proposal` decision; Approve applies it as a trial.
- **People** edit with `PUT /v1/factory/factories/{fid}/definition`
  (`{changes, note?, trial?, base_version?}`, MANAGE, human origin): a human
  `C-n` applied at once.
- **Delivery.** Each version change mails the members
  `[Factory definition · vN · C-n by … · trial 48h]` with the diff; rule
  changes send `[Factory control · RULES UPDATE · C-n · by …]` to the roles they
  concern. A cadence change moves members still on the old default. Rules
  reach agents through `sys_factory_rules()` (`GET /v1/factory/agent/rules`),
  task cards and PL briefs; they never widen a policy or a gate.
- **Evaluation** (`evaluate.py`, on the supervisor tick): at the end of the
  trial window it is compared with the same-length baseline before it. A
  regressed guard (merge acceptance −10 pp, $ per merged PR +10 %, incidents
  +1, human prompts per day +20 %, loop exhaustion +10 pp) reverts
  automatically; target met with n ≥ 3 merged PRs (24 h of beats for a
  cadence change) keeps it; anything else asks the owners "Keep or revert
  C-n?" (recommended: revert). Never auto-kept. A trial nobody decides reverts
  at 2× its window.
- **Revert** (`POST …/proposals/{ref}/revert`, owners, one click) re-pins the
  parent version when it is still in force, else undoes only that change as a
  `revert` version. `…/keep` ends a trial early; `…/definition/pin {version}`
  puts any stored version back. `GET …/definition`, `…/definition/versions`
  and `…/definition/diff?from=&to=` show them.

**Tier 2: the Factory Smith lane** (code and bundle PRs to omnigent) ships
off. It turns on only when `OMNIGENT_FACTORY_SMITH=1`, rulesets requiring
review, CODEOWNERS review and status checks protect
`caffeinelabs/omnigent#staging` (read through the GitHub API at server start)
and a dedicated factory identity
(`OMNIGENT_FACTORY_SMITH_GITHUB_LOGIN` and `…_TOKEN`) has neither admin nor
maintain rights. `GET /v1/factory/preflight` returns `tier2 {enabled,
reason}`. While it is off, `kind=code` proposals are recorded with their
evidence and never run.

### Learning

- **Metrics** (`telemetry/metrics.py`): the catalog (flow, loops, quality,
  human load, cost, reliability) with an explicit definition per metric,
  always with n, grouped by day, project, role, agent, person (owners only),
  version or def_version; 5-minute cache. `GET …/insights` (KPI tiles with the
  delta against the previous window, findings, incidents, retros,
  experiments, per-person numbers for owners), `GET …/metrics`, and the fleet
  `GET /v1/factory/insights`. Projects whose PL the viewer cannot read are left
  out. A nightly scan counts human-authored items on members
  (`metrics.prompts`), including messages stamped `[Factory human · …]` in a
  shared factory.
- **Feedback** (`POST /v1/factory/feedback`): PR ratings (also on a cockpit
  review), demo 👍/👎, unnecessary questions, calibration of retro labels and
  root causes. A `pr_closed` decision's answer may carry a closure reason
  (`reason`, from the seeded list); one without it records no closure.
- **Retros** (`sys_factory_retro`): PLs file one after a merge, a close and at
  done; Sentinel files findings. Without evidence: 422.
- **Sentinel's retro** (supervisor rule S12): daily after
  `OMNIGENT_FACTORY_RETRO_HOUR` (UTC, default 6; not on the factory's first
  day) and at each project done, Sentinel gets
  `[Factory heartbeat · retro · window 24h · budget $3]` and runs the
  `factory-retro` skill (open and axial coding, at most 2 proposals, a digest
  in `#improvements`). Owners can run it now: `POST …/retro`.
- **Eval mode** (root label `omnigent.factory.mode=eval`): decisions are
  answered with the recommended option after 2 minutes (`answered_by
  eval-auto`) and each project's PL is paused at READY. Decisions only an
  owner may approve stay open: access requests, tuning, rules and code
  proposals, owner-approval projects proposals and budget raises past the
  operator cap. The benchmark charters
  are in `examples/factory/benchmarks/temp-main/`.

## Work graph

Tasks are a server entity (`factory_tasks`, `T-<n>`), not rows in a PL's
`plan.md` (`omnigent/server/factory/tasks.py`, `graph.py`, `binder.py`):

- **Tasks.** A PL registers its project's tasks at kickoff
  (`sys_factory_task(op="upsert")`): `key` (the branch suffix of
  `factory/<slug>/<key>`), kind, card (do, acceptance, env checks, preview,
  demo focus, harness), `deps` (task keys, `project:<slug>`,
  `decision:D-<n>`), scope globs, priority and estimate. A cycle is refused
  with 409 `cycle` and its path, an unknown dependency with 422 `unknown_dep`.
  States: planned → ready → running → review → done, or blocked, failed,
  cancelled.
- **Computations** (pure, `graph.py`): the ready set; waves (a layered
  topological order capped by free IC and preview slots, where ad-hoc work
  holds an IC slot only while its worker runs, with implicit
  serialization edges between same-wave tasks whose scope globs overlap);
  the critical path (weights: the observed p50 of the task kind once 5 tasks
  ran, else the estimate, else 60 min); `scope_overlap` and `hub_file`
  (from a planner analysis) warnings.
- **Dispatch.** `op="ready"` returns the wave to start now with the
  server-rendered `card_text` (base, acceptance, rules, budget left) and
  `title_to_use` (`<key>`, then `<key>-r<k>`); `op="start"` counts the
  attempt (once, even when the binder saw its dispatch first). `cost_budget`
  rides along only in `enforce` engine mode, never for `ic_codex` and never at
  $0. Budgets are read here; the loop engine enforces them.
- **Binder.** Every dispatched sub-agent of a PL binds by title on
  `dispatch`, `task.done`/`task.failed` and each supervisor tick: `<key>`,
  `<key>-r<k>` (implement attempt k+1), `review-<key>`, `demo-<key>`; team
  names (`env`, env_keeper) bind nothing; anything else becomes an ad-hoc task
  plus a `task.unplanned` warning. PRs bind by branch into
  `factory_prs.task_id`; a draft PR keeps its task running (its IC holds a
  slot), a PR out of draft moves it to review and a merge (a human's
  included) closes it. Bound sub-agent `task` spans carry the task.
- **People** add and edit tasks in the cockpit (`POST|PATCH
  /factories/{fid}/tasks`, EDIT, human origin); the PL gets
  `[Factory control · TASK ADD|TASK EDIT · by …]` with the diff.
- **Graphs.** `GET /factories/{fid}/graph?kind=work|bus|flow|org`: the work
  graph; the bus (message edges with counts, p50 reply latency, bounces,
  unanswered requests; spawn, supervise, alert and waits-on edges); the flow
  (`plan → build → review → verify → demo → ready → merged` with counts now,
  p50 dwell, dollars and loop back-edges from spans); the org (every depth).

## Loop engine

Every loop is explicit, bounded, observed and escalated
(`omnigent/server/factory/loops.py`, `loop_escalation.py`, `pipeline.py`,
`budgets.py`). `loops.CATALOGUE` holds L1–L9 (implement, review, verify, demo,
ci, claim, plan, intake, improve); `factory_loops` keeps one row per
`(kind, subject)` with its budget snapshot (`spec`) and `spent`.

- **Facts, not narration.** An event listener maps `task.state`/`task.bound`
  (attempts, IC `PASS|FAIL|BLOCKED` reports), `gate.changed` (CI per head with
  flakes, review markers with `blocking=`/`findings=`, verify markers with
  `reason=code:|env:`), `preview.reported` failed and `preview.sha_mismatch`
  (env), `demo.published` (per head), `pr.claim` and `pr.human_review` onto
  iterations, `loop.*` events and `loop`/`loop_iter` spans. Implement counts
  tries: a worker, or a rework a verifier asked for; env failures do not use
  one up. Review is *dry* when a round adds no new fingerprint and fixes none,
  *thrashing* when a fixed one returns. The supervisor tick adds wall and
  no-progress stalls, the plan loop and the `done_check` decision.
- **Engine mode** (root label `omnigent.factory.engine_mode`, default
  `assist`; stamped on a new PL and mirrored by the tick, where the guard
  reads it): `observe` records (detectors open incidents); `assist` also opens
  a `loop` decision and mails the PL `[Factory loop · EXHAUSTED|STALLED|THRASHING · …]`,
  plus NEXT and READY hints; `enforce` also refuses `sys_factory_task(op=start)`
  past the budget (409 `budget_exhausted`) and the guard denies a new PL
  dispatch with no title or one that is not task-shaped, one that sends `ic`/`demo`
  work without `args.cost_budget` (fixed team titles included; codex-native
  workers are unpriced and exempt), and any PL `sys_session_create`.
- **Answers.** Raise (+1 attempt by operators; money by owners), switch
  vendor, fresh restart, abandon, mark done, pause; the PL gets `BUDGET
  RAISED`, `SWITCH VENDOR` or `FRESH RESTART` with exactly what to do. A
  `Pause project` answer leaves the loop stopped; once the project (and the
  factory) run again the tick opens a new decision. A stop that ends before
  anyone answers (progress after a no-progress stall, a person's changes
  request or task budget edit, the task closing) expires its decision, and a
  loop running again (`loop.resumed` for a stall) mails the PL
  `[Factory loop · RESUMED · …]`; progress does not lift a wall-budget stall.
  `start` cites a decision only while it is open. A paused or done PL, or a
  paused factory, gets no NEXT hints.
- **NEXT hints.** `pipeline.next_step` (pure) walks `ci → review → verify →
  demo → sentinel → ready`; a changed `REVIEW`, `PREVIEW` or `DEMO` step is
  mailed after a 30 s grace, re-checked (a step already running is skipped),
  coalesced to one mail per PL per 120 s, never twice per head
  (`engine.next`). `FIX_CI` and `HANDOVER` ride on the poller's existing
  `checks failed` and `READY FOR HUMAN MERGE` notices. READY waves (`engine.ready_wave`) tell a PL with startable
  tasks and free IC slots to fetch its cards.
- **Budgets.** Project $ (`charter.budgets.project_usd`, 150) and factory $
  per UTC day (400) from the cost ledger: 80 % `budget.near`; 100 %
  `budget.exceeded`, plus an owners' decision (assist) or a pause with the
  note `budget` (enforce). Budgets resolve through `loops.budget(fid, kind,
  key)`, the seam the factory definition plugs into.
- **Spike S2.** A claude-native PL's `sys_session_send` is an Omnigent MCP
  tool, policy-checked server-side by `_evaluate_tool_call_policy` with a
  fresh engine whose `event.context.labels` are the PL's labels, so the guard
  sees `agent`, `title` and `args.cost_budget`; no after-the-fact check is needed.
- `GET /factories/{fid}/loops?state=&project=&kind=&pr=` lists them; the
  snapshot carries `engine_mode`, `projects[].loops_summary` and `prs[].loops`.

## Safety

- **`factory_role_guard`** ([`policies/builtins/factory.py`](../omnigent/policies/builtins/factory.py))
  sits next to `blast_radius{gate_pushes: false}` on every factory spec. It
  only ALLOWs or DENYs, so unattended agents never stall on an approval card.
  It parses shell commands (including text typed into a terminal and argv
  lists) and denies, among others: every merge for every role (raw
  `gh pr merge`, GitHub MCP merges, and the merge gate without `--dry-run`);
  any `OMNIGENT_FACTORY_*` assignment, `env` or `export` in a command; admin
  writes through `gh api`, `gh repo delete|archive|edit`, `gh release`,
  `gh secret`, `gh ruleset` and workflow toggles; every push but an explicit
  branch under `push_ref_prefixes` (`factory/`), so bare pushes,
  `git push origin HEAD`, deletes, tags and `main`/`master`/`staging`/`temp-main`
  are denied, and so is git config that could redirect an explicit push (see
  Invariants); any push or PR creation by a lead, reviewer, planner or the demo
  agent; writes to repos outside `write_repos` (default `caffeinelabs/app`) or
  inside `deny_repos` (`caffeinelabs/infra`, always, and `caffeinelabs/omnigent`);
  the `factory:allow-protected` label; every PR write but `gh pr comment` by
  the demo agent and every PR or issue write by the planner; and env lifecycle
  (`tilt up|down|ci`, `pnpm factory:up|down|preview` or
  `scripts/factory.ts up|down|preview`, `kubectl delete ns`) for anyone but
  `env_keeper` (and its PL, whose guard its sub-agents also pass).
- **Humans merge.** The factory never merges `caffeinelabs/app`. A PR is done
  for the factory when it is ready for human merge (not a draft, CI green, an
  APPROVE review marker and a PASS verify marker for the head). The PL posts
  `READY FOR HUMAN MERGE <url>` to its lead, Sentinel verifies the claim, and
  Atlas lists it for the human.
- **Merge gate.** `python -m omnigent.factory_merge <pr-url> [--admin-fallback] [--dry-run]`
  is a verdict tool for agents: the guard admits it only with `--dry-run`
  (see Invariants). Run by a human, it
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

## Invariants (Tier 0)

These rules hold for every factory and are never reachable through the
factory itself: no prompt, rule, proposal or agent tool changes them. Each is
enforced in code.

| Invariant | Enforced by |
|---|---|
| The factory never merges (any repo, `temp-main` included). | The guard denies raw `gh pr merge`, MCP merges, merge-capable `gh api`/GraphQL calls and every `omnigent.factory_merge` run without `--dry-run` (`python -m`, a script path, `python -c` code, a Python heredoc), for every role. It also denies any `OMNIGENT_FACTORY_*=` assignment, `env OMNIGENT_FACTORY_…` or `export OMNIGENT_FACTORY_…`, which closes the inline `OMNIGENT_FACTORY_MERGE_REPOS=… python -m omnigent.factory_merge` override. `OMNIGENT_FACTORY_MERGE_REPOS` stays empty on the server. |
| Agents push only `factory/**` (the `smith` profile: `factory/self/**`). | `push_ref_prefixes` in every bundle's `factory_guard`: each explicit refspec destination must be `refs/heads/<prefix>**`. A bare `git push` and `git push <remote> HEAD` are denied (their destination depends on config and the checked-out branch, which the guard cannot see), so the IC prompts always push `git push -u origin factory/<slug>/<task>`. An explicit `<src>` still follows config: with `push.default=upstream` it lands on the branch's upstream, and `remote.<r>.push`, `pushurl` or `url.<x>.pushInsteadOf` repoint it. So the guard also denies `-c`/`--config-env` on a push, any `GIT_CONFIG_*` assignment, `git config` writes to `push.*`, `remote.<r>.url\|pushurl\|push\|mirror`, `url.*`, `branch.*`, `alias.*` and `include.*`, and `git remote add\|set-url\|rename`; the IC worktree is created with `--no-track`, so its branch has no upstream to fall back to. Deletes (`:<b>`, `--delete`, `--prune`), tags, `--all`/`--mirror` and API ref writes outside the prefix are denied too. `temp-main` is in the default `protected_branches`. |
| No writes to `caffeinelabs/infra`; none to `caffeinelabs/omnigent` outside the `smith` profile. | `deny_repos`; `caffeinelabs/infra` is denied whatever a spec passes. The `smith` profile (`write_repos: [caffeinelabs/omnigent]`, `push_ref_prefixes: [factory/self/]`, never merges) is registered for Tier 2 and unused while Tier 2 is off. |
| Factory agents always run fenced, and never run a bundle of their own making. | `server/bundles.validate_agent_bundle` rejects any (sub-)agent spec with `executor.config.factory_role` that lacks an unconditional `factory_role_guard` with the same role plus `blast_radius`. `sys_session_create(config_path=…)` from a session whose root carries `omnigent.factory.id` gets 403 `factory_custom_bundle_denied` (`server/factory/custom_bundle.py`). |
| Human levers come from humans. | `server/factory/origin.py:require_human_origin` on every cockpit write route (start, add project, charter, control, decision answer and withdraw, PR feedback and refresh; new human routes use `HUMAN_ORIGIN` too). It answers 403 `human_only` when the request carries the runner tunnel token header, a runner bearer (`mint_runner_token` stamps `"org": "runner"`), or an untrusted `Origin`. A person's cookie and their own CLI bearer pass. In accounts mode, `/auth/magic`, `/auth/users/me/password`, `/auth/invite` and `/auth/users/{id}/reset` refuse runners too, so a runner bearer never becomes a browser cookie, a password or an account. |
| Agents' label writes are audited; server-only labels stay out of agents' reach. | `PATCH /v1/factory/sessions/{id}` emits `label.changed {field, label, from, to, by, by_kind, by_role, on_behalf_of}` for each label it changes, and refuses `def_version`, `default_repo`, `default_base`, `mode`, `engine_mode`, `autonomy_projects` and `engine_spawn` with 422 `server_only_label`. The generic `PATCH /v1/sessions/{id}` refuses every `omnigent.factory.*` label from a runner (its tunnel header or bearer) with 403, so an agent cannot route around the audit. A person with EDIT can still set them there. |
| Invariants hold even if a bundle is wrong. | The admin default policy `factory_invariants` ([`policies/builtins/factory_invariants.py`](../omnigent/policies/builtins/factory_invariants.py)), seeded into the server-wide defaults when `OMNIGENT_FACTORY_ENABLED=1` (`OMNIGENT_FACTORY_INVARIANTS=0` leaves it out), repeats the push, deny-repo, merge and knob rules for every session whose labels carry `omnigent.factory.*`. Admin policies run after the spec's and short-circuit on DENY. |

**The honest limit.** The guard is a safety net against accidents, not a
security boundary: agents act with the creator's GitHub token, so a script,
an alias, a `curl` to the REST API or code the guard cannot read can still
merge, and git config written by editing `.git/config` or `~/.gitconfig`
directly can still redirect a push. A lead or PL can also start a child with
`sys_session_create(agent_id=<another agent>)`: the child runs that agent's
spec, not the factory guard, and carries no factory labels, so
`factory_invariants` abstains for it too. The real boundary is a factory
GitHub identity without merge or admin rights, plus a ruleset on the base
branches. It is required before Tier 2 (self-modification PRs to omnigent)
and recommended sooner.

**Spike results (round 3).**

- **Can an admin default policy see conversation labels?** Yes for top-level
  members. `/policies/evaluate` builds the engine with the server-wide
  defaults appended after the spec's policies, and every function policy
  receives the evaluated session's labels as `event.context.labels`. The lead
  and the PLs carry `omnigent.factory.*` labels; sub-agents (IC, ic_codex,
  env_keeper, reviewer, demo, planner) carry none, so for them the invariant
  policy abstains. They stay fenced by their own guard and their root PL's
  spec guard, which bundle validation pins. The evaluate fast path skips
  the invariant for sessions without factory labels, so other sessions on
  that server build no extra engine.
- **Does a `config_path` child escape the spec guard?** Yes, before this
  change: the child runs the uploaded spec, not its parent's, so the parent's
  `factory_guard` does not apply to it. The server now refuses the upload
  (403 `factory_custom_bundle_denied`); `tests/server/test_bundle_validation_factory.py`
  proves it.
- **Can a sandbox process read the runner bearer?** By code reading, yes.
  Agents run unsandboxed as the runner's user (`os_env: caller_process`,
  `sandbox: none`). The tunnel binding token is stripped from child
  environments, but the runner's own `/proc/<pid>/environ` is readable by the
  same user, and the binding token mints an owner bearer through
  `POST /v1/runners/{id}/token`. The `org: runner` claim and the tunnel-header
  check make such calls distinguishable, so human-only routes refuse them
  either way; tokens minted before the deploy expire within their sub-hour TTL.

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
| Tier-0 invariants | `omnigent/policies/builtins/factory_invariants.py`, `omnigent/server/factory/origin.py`, `custom_bundle.py`, `omnigent/server/bundles.py` | `tests/policies/test_factory_invariants.py`, `tests/server/factory/test_human_origin.py`, `tests/server/test_bundle_validation_factory.py` |
| Org chart and badges | `omnigent/server/factory/org_chart.py`, `web/src/lib/factory.ts` | `tests/server/routes/test_child_sessions_include_factory.py`, web tests |
| Base lane and demo mirror | `omnigent/server/factory/base.py`, `demo_mirror.py`, `omnigent/factory_gates.py` (`base` gate) | `tests/server/factory/test_base_watcher.py`, `test_demo_mirror.py`, `tests/test_factory_gates.py` |
| Work graph | `omnigent/server/factory/tasks.py`, `graph.py`, `binder.py`, `api/tasks.py`, `api/graph.py` | `tests/server/factory/test_graph.py`, `test_binder.py`, `test_tasks_routes.py`, `test_graph_api.py` |
| Loop engine | `omnigent/server/factory/loops.py`, `loop_escalation.py`, `pipeline.py`, `budgets.py`, `api/loops.py` | `tests/server/factory/test_loops.py`, `test_pipeline.py`, `test_budgets.py` |
| Telemetry | `omnigent/server/factory/telemetry/`, `api/telemetry_read.py`, `background/prune.py` | `tests/server/factory/test_telemetry_*.py` (replays `fixtures/live_run_2026_10_26.json`), `test_prune.py` |
| Bundles | `examples/factory/` | `tests/examples/`, `tests/server/factory/test_factory_agents_listed.py` |
| Demo harness | `examples/factory/factory-pl/agents/demo/skills/factory-demo/harness/` | `tests/examples/test_factory_demo_harness.py` (runs `factory_demo_harness.test.mjs` under `node --test`); `node run.mjs --selftest` records real demos of a local fixture |
| End to end | — | `tests/integration/test_factory_tree.py` (live server and runner, mock LLM) |
