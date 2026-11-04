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
| `OMNIGENT_FACTORY_COCKPIT_V2` | unset | `1` lets each person switch to the new cockpit (Teams and the agent panel; `?cockpit=v2`, remembered per browser). Unset, every page and API behaves as before. |
| `OMNIGENT_FACTORY_DISCOVERY` | unset | `1` runs discovery on this server: the discovery ledger's routes, Room hunches as team signals, the ledger's upkeep (retention, daily score snapshots), the definition preview's re-rank, readouts and the prediction ledger, and trials of discovery's method on discovery guards. A factory also needs its own `discovery.enabled` (owners, off by default); until both are on every discovery route answers `404 discovery_off`. |
| `OMNIGENT_FACTORY_WEB_SEARCH` | unset | The keyless search backend of `web` sources: `keenable` (its public, keyless endpoint; no key, no account). Unset or `none`, `web` sources fetch only (their `search` tool is not offered). Turn one on only where the server can reach it. |
| `OMNIGENT_FACTORY_STATE_SEGMENTS` | follows `OMNIGENT_FACTORY_COCKPIT_V2` | `1` records each agent's working and waiting stretches (the agent panel's strip); any other value turns recording off. Segments older than 30 days are pruned. One server process is assumed to see all of a session's status edges. |
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
- `sys_factory_node(op="read", ref, children_page?)` (leads, PLs, Scout, the
  planner) — one node of cockpit v3's work tree as people see it (`GET
  /v1/factory/agent/node`: the node page, verbs disabled; agents change the
  structure through proposals). A server from before node pages answers
  `not_served`; Room messages about a node carry its header either way.

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
| S13 launch watch | an `active` member whose managed sandbox did not come up (pod never ready, timed out, image pull error, runner never connected), or whose launch a server restart cut off (timed from its latest launch) | `member.launch_failed`; 30 s later one retry of the server's own (`launch_auto=1`, `launch_tries=2`). A first launch (no sandbox, or one the failed launch removed) is retried on a **new sandbox** for the same session (`member.new_sandbox`). A member whose **own** sandbox did not wake is woken again on that same sandbox (`member.wake_retried`): the server never deletes a sandbox, and the workspace in it, on its own. A failure on record for a member whose host is online again or whose runner is attached is cleared. When the retry fails too, one owners' decision worded by what happened ("could not start its sandbox twice", "could not wake its sandbox twice" with Restart first, "cut off by a server restart", "failed after <owner> relaunched it"); nothing retries on its own again. No heartbeat goes to a member whose launch is in flight or failed. Once a new sandbox is up, the member gets `[Factory control · NEW SANDBOX …]` (a PL that never got its brief gets the brief again); `member.launch_recovered` either way. |
| S13 release | a PL whose project is cancelled, merged into another or done, or that finished itself, idle (and its sub-agents idle) with no mail on its way for 5 min, keeping no preview up for review | Its sandbox is stopped (`member.sandbox_released`); the host row stays, so a message (Resume) wakes it. Its sub-agents and, on the cluster, its dev env and previews go with the pod. |

Paused and done members get no keepalive and no heartbeats, so their runners
idle out and their sandboxes are reclaimed.

**Relaunch on a new sandbox** (`new_sandbox` control, owners; the project menu,
the failed-PL decisions and the dead-member runbook) is not Restart: Restart
and "Relaunch with spec mode" keep the member's sandbox, this one deletes it
(with its files, dev env and previews) and provisions a fresh one for the same
session, so the project, charter, role, tasks, PRs and decisions stay. While a
PL sandbox starts, the project card, page and Now card name what it waits on:
a node, the image (and for how long), the clone, the containers, the host.
Finished (merged or done) projects take no slot under `caps.max_projects`
(the `over_caps` refusal names only `park_candidates`); their pods still count
toward `factory_max_pods` until released. Resuming a stopped project takes its
slot back and is refused over the cap.

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
| `POST …/{fid}/projects/{slug}/start` | human (EDIT) | Start PL: the server spawns the PL of an active project that has none (it takes the spawn over from the lead; unmet dependencies still hold it). |
| `GET …/{fid}/proposals?kind=&status=`, `GET …/proposals/{ref}`, `POST …/proposals/{ref}/apply` (`{changes?}`), `…/reject` (`{note?}`), `…/undo` | human (EDIT; MANAGE for an owner-class proposal) | Proposals `C-<n>`; apply and reject answer the mirrored decision, so one of two clicks wins. |
| `POST /v1/factory/factories/{fid}/control` | human (EDIT; MANAGE for STOP, RESTART, RELAUNCH) | PAUSE, RESUME, STOP, RESTART, RELAUNCH, cadence, PREVIEW / PREVIEW_DOWN of a PR, ENV STATUS: a server-stamped `[Factory control …]` to the members. |
| `GET /v1/factory/decisions`, `POST …/decisions/{id}/answer`, `…/withdraw` | human | Open decisions (also in `/inbox`); the answer is delivered to the asker as `[Factory decision · D-<n> answered …]`. |
| `POST …/decisions/{id}/undo` | human (EDIT; MANAGE for an owner-class proposal) | Takes back an Apply on a projects proposal's decision within the proposal's Undo window: the proposal is undone and waits for a person again under a fresh decision (`{proposal, decision}`); the answered decision stays answered. |
| `POST /v1/factory/prs/{id}/feedback`, `…/refresh` | human | Approve or request changes (`[Factory review …]` to the PL); re-poll GitHub now. |
| `GET /v1/factory/prs/{id}/preview` | human | Stable live-preview link: 302 to the current preview app URL, or back to the cockpit when there is none. |
| `GET /v1/factory/demos/{demo_id}/files/{file_id}` | human (READ on the PL) | Demo files with Range/206 (a ranged read of the store) and ETag; only images and video inline, never HTML or SVG. |
| `POST /v1/factory/agent/decisions`, `POST …/agent/prs`, `PUT …/agent/envs`, `POST …/agent/demos` (multipart), `GET …/agent/charter` | runner (the tools below) | Agent reports. |
| `POST /v1/factory/agent/proposals`, `GET …/agent/projects` | runner (`sys_factory_project`) | Propose project changes; leads for every project, a PL for its own. |
| `GET /v1/factory/factories/{fid}/tasks?project=`, `POST …/{fid}/tasks`, `PATCH …/{fid}/tasks/{id or T-n}`, `GET …/{fid}/graph?kind=work\|bus\|flow\|org&project=&window=` | human (READ; EDIT and human origin to write) | The work graph (see below); adds and edits mail the PL `[Factory control · TASK ADD\|TASK EDIT …]`. |
| `PUT /v1/factory/agent/tasks`, `GET …/agent/tasks?view=list\|ready\|get`, `POST …/agent/tasks/{key}/start\|done\|fail\|cancel\|report` | runner (`sys_factory_task`) | The PL writes its project's tasks; leads read; the bound worker reports. |
| `GET /v1/factory/preflight` | human | GitHub connected, agents seeded, sandbox host, factory enabled; `default_repo`, `default_base` and `max_projects` for the start form. |
| `GET /v1/factory/factories/{fid}/spans?kind=&project=&pr_id=&task_id=&since=`, `GET /v1/factory/prs/{id}/timeline`, `GET …/{fid}/cost?group_by=day,project,role,agent,model`, `GET …/{fid}/export?tables=&format=ndjson\|csv` | human (READ on the root; hidden projects dropped) | Telemetry (see below). The export streams and never carries transcript content. |
| `GET /v1/factory/factories/{fid}/search?q=&limit=` | human (READ on the root; what the viewer cannot read is left out, as in every other read) | The new cockpit's ⌘K: hits of `q` over every id (`O-`, `S-`, `G-`, `C-`, `D-`, `INC-`, `F-`, `T-`, `R-`, `!` PRs, `#` projects, `v` versions), titles, members (vendor, model, state), definition field names (old names such as `models.ic` too; the blocks edited whole, such as the product profile and strategy, are not listed field by field), versions and Room threads, best first (a full ref, then titles that contain the query, then words). Reads the first 200 characters of `q`. Opportunities, signals and sources only while discovery runs; goals always. A signal hit carries `untrusted: true`, like discovery's signal reads. The palette's verbs stage team changes in the editor; discovery's verbs call the discovery routes (a source's pause is undone by pinning the version before it; resuming one is owners'). |

### Tools

| Tool | Roles | Use |
|---|---|---|
| `sys_factory_ask(question, options, context?, blocking=true, dedupe_key?, for?, subject?)` | every role | Ask the human. Opens D-<n> in the cockpit and the inbox and returns at once; the answer arrives as a message. Replaces DECISIONS NEEDED lists. `for` routes it to `owners`, `operators` or a person on the roster (default: the project's human owner, else operators). |
| `sys_factory_pr(url, task?, claim?, sentinel_verdict?, head_sha?, note?)` | every role (`sentinel_verdict`: leads) | Register a PR, claim `opened` / `ready_for_human_merge` / `abandoned`, or record Sentinel's verdict. Returns the server's gates `{ready, gates, reasons, claim_mismatch, claim_status}` and the PR body's review links (`review_url`, `preview_url`). |
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
  the factory's repos and default base (only repos a human gave the factory
  count: the default and live projects' repos, not a row reconcile backfilled
  from a lead's spawn nor the root's sandbox labels, so a lead cannot turn a
  repo it chose into one the guards let it write; any other repo needs the
  owner), `depends_on` cycles (409 `cycle` with
  its path) and scope overlaps (a warning). The autonomy policy
  (`server/factory/autonomy.py`, root label `omnigent.factory.autonomy_projects`:
  `confirm`, `requested` by default, or `grace`) decides what applies at once
  (a create a person asked for, `reply_to` a Room message or the start
  brief's mail id) with a 10-minute Undo; owners may scope a level to a
  subtree (`autonomy.scopes`, see "Autonomy and auto-merge per subtree"), and
  each op is then decided at the level of the nearest scoped ancestor of the
  projects it touches (the strictest of those); the rest waits on a `proposal`
  decision (Apply, Edit & apply, Reject) that re-checks `based_on` (409
  `stale_proposal`). Setting, raising or removing a USD `budgets` cap is the
  owners' (owner class; the cockpit add and charter PATCH need MANAGE for it).
  An apply that fails part-way takes back the projects it created before
  their PLs started. Pending proposals expire after 72 h; the proposer gets
  `[Factory proposal · C-<n> applied|rejected|expired|undone …]`.
- **Charter versions** (`server/factory/charter_versions.py`). A person's
  charter edit (`PATCH …/projects/{slug}`) is recorded as a human projects
  proposal that applied when it was filed (`C-n`, `result.charter_edit`, the
  charter and priority from before it), so it has a 10-minute Undo through
  `POST …/proposals/{ref}/undo` and the PL's CHARTER UPDATE names it. `GET
  …/projects/{slug}/charter/versions` lists every applied or undone change of
  the charter (people's and agents'), each field before and after; `POST
  …/projects/{slug}/charter/revert {proposal, note?}` puts the fields one
  version changed back as they were, as a new version (MANAGE when it raises
  a budget; 409 `nothing_to_revert` / `not_a_charter_version`). An undo puts
  back the whole charter from before a change, so only the newest change of a
  charter can be undone: each applied projects proposal keeps `after[slug]`
  with a hash of the charter and priority it left, and an undo after anything
  changed them since is 409 `undo_stale` (revert it instead); the history
  closes Undo on every version with a newer applied change on top. A no-op
  edit records nothing; charter edits do not count in the proposals metric,
  and the bounded scans that read agents' proposals (planner hub files,
  dedupe keys, an opportunity's proposals) skip people's. Dark
  by default: only while the server offers the new cockpit
  (`OMNIGENT_FACTORY_COCKPIT_V2=1`); otherwise edits apply as before and both
  routes answer 404 `charter_versions_off`.
- **Who asked for a project** (`charter.requested_by {kind, name, proposal?}`,
  server-written): the person who added it in the cockpit, the author of the
  proposal that created it, or the person whose message an agent's proposal
  answers (an agent's own idea shows as one); split children keep whoever
  asked for their parent.
- **The lead's update** (`factory_projects.lead_update`). A project lead posts
  `sys_factory_set(update={health: on_track|at_risk|off_track, done, next,
  blocker})` on itself (`PATCH /v1/factory/sessions/{id}` `update`; only the
  project's current lead, 403 `not_your_project`; 400 `invalid_update`). The
  row always keeps it; only while the new cockpit is offered is it logged as
  `project.update`, and only then does the PL's charter ask for it (an
  `Updates:` line), so the classic cockpit and its PLs behave as before. The
  snapshot's `projects[].update` is the latest one and `projects[].health` the
  health the server computes from facts (stopped, failing checks, spend
  against the budget, stuck members, a blocking question), with the budget it
  measured against (`budget_usd`: the team spec's, else the charter's project
  budget): the facts override the lead when they say worse, never when they
  say better, and an update older than 3 h is stale.
- **Dependencies are checked by every core**: an unknown `depends_on` is 400
  `unknown_dependency`, a cycle 409 `cycle` with its path, from the cockpit
  forms as from proposals. An item may name one change instead of a project
  (`PR-9876`, a tracked PR): it is met once that PR merged, whatever else its
  project still does (cockpit v3's Start now relaxes a wait to it); it draws
  no project edge and never closes a cycle. An older server drops such items
  on read, which only ever releases the project.
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
  `depends_on` (`project.held`) until each dependency's PRs merged with none
  open and none of its tasks still open (`depends_mode=merged`: a first merge
  does not finish a project that still works) or one is ready for human
  merge (`ready`); the same rule holds a task's `project:<slug>` dependency
  and is what the waits and the node pages report; release runs
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

### Slack DMs

The cockpit is pull: a non-blocking proposal can sit for days, a trial can
revert on its own and an idle factory can burn money while nobody looks. Each
person on the roster can therefore get **personal Slack DMs** about what is
waiting on *them*, with reminders before anything defaults and a daily
digest. The factory never waits on Slack.

**Setup.**

1. A Slack admin creates the app from the manifest (Settings > Alerts >
   Slack app > **Copy app manifest**, then api.slack.com/apps > Create New
   App > From a manifest), installs it and copies the Bot User OAuth Token
   (`xoxb-…`). The app only sends DMs (`chat:write`, `im:write`,
   `users:read`, `users:read.email`): no events, no interactivity.
2. An owner pastes the token under Settings > Alerts > Slack app and presses
   **Test**. The server checks it with `auth.test` (granted scopes come from
   `x-oauth-scopes`) and stores it per factory like the webhook:
   `factory/secrets/{fid}/slack_bot_token` plus non-secret metadata in
   `…/slack_bot_meta.json`. It is Tier 0 (`alerts.slack_bot_token`), set only
   through a human-origin owner route and never returned, logged or put in an
   event. A token of another workspace unlinks everyone linked in another
   one (also after the old token was removed); removing the token closes the
   `slack.bot` incident.
3. Each person links their own account under Settings > Notifications: a
   member ID or a Slack email (omnigent ids are GitHub-login emails and may
   differ), then a 6-digit code DM'd to that account (15 min, 5 tries; the DM
   names who entered it). Find shows only the handle and member ID; the
   account's name and email show once the code proved it is theirs. The code
   is checked against the stored pending account under the person's lock, so
   concurrent guesses each count and only the account the code went to is
   linked. One Slack account gets at most 3 codes an hour, one person can
   have 10 sent a day. A welcome DM lists what is waiting on them now.

| Route (`/v1/factory/factories/{fid}/…`) | Who | What |
|---|---|---|
| `PUT alerts/slack-bot` `{token\|null}`, `POST alerts/slack-bot/test` | MANAGE, human origin | Set (after `auth.test`) or remove the token; test it and DM the caller when linked. `GET alerts/routing` gains `slack_dm {configured, team, bot, status, error, missing_scopes}`. |
| `GET\|PUT notify/me` | on the roster (PUT: human origin) | Own settings with role defaults, link status, the app's status, last DM, quiet now. |
| `POST notify/me/slack` `{member_id\|email, send_code}`, `POST notify/me/slack/verify` `{code}`, `DELETE notify/me/slack` | self, human origin, 5 lookups per 10 min | Find, send the code, verify, unlink. Someone with access but no roster row gets 409 `not_on_roster`. |
| `POST notify/me/test` | self, human origin, 5 per 10 min | A test DM listing the effective settings. |
| `GET notify/team` | MANAGE | Who is reachable (status, groups, quiet now, last DM); no member ids. |

Settings live in the existing `factory_people.notify` JSON (no migration):
event groups (decisions, reminders, PRs, incidents, factory, budget,
mentions, retros), the incident minimum (warning or critical), the digest
(hour, every day or weekdays), quiet hours (default 22:00-08:00 in the
person's IANA zone, DST-correct; critical incidents may break through) and
"hold while I'm active in the cockpit". Viewers default to mentions only,
operators to everything but budgets, owners to everything; retros are
opt-in. The People list shows only `notify: {slack, enabled}`.

**What is sent.**

| Item | When | To |
|---|---|---|
| Decision needs you (any open decision, blocking or not) | 30 s after it opens or is routed to you | The claimer; else a person assignee; else operators (owners when no operator is reachable); owners for owner decisions |
| Reminder | 4 h and 24 h after it reached you | The same |
| Waiting on the owners | open 24 h | Every owner not already a recipient |
| Last call | 2 h before a projects, tuning or rules proposal expires (72 h) or a trial reverts on its own (2x its window); 15 min before quiet hours when the deadline falls inside them | Recipients and owners |
| PR ready for human merge (`pr.ready`) | at once; dropped if no longer ready, a new head, or a human reviewed it | The project's `human_owner`, else operators and owners |
| Incident (`issue.opened`, warning or worse by the person's minimum) | at once, at most once per issue and person an hour | `issues.is_mine`: assignee, else operators (owners as fallback); owners also get critical ones |
| Factory or project paused, stopped, resumed; `factory.warning` | at once | Owners and the project's human owner, never the person who did it |
| Budget at 80 % / 100 % (factory or project; a task's own budget stays with its loop) | at once | Owners and the project's human owner |
| Idle spend | ≥ $5 in 6 h with no `pr.ready`, `pr.merged`, `task.done`, `stage.changed`, `demo.published` or `proposal.applied`; once a day | Owners |
| Room @mention | at once; dropped once the thread is read | The person mentioned |
| Retro or finding (opt-in) | at once, named by who filed it, visibility of its target | Opted-in people |
| Daily digest | at the person's hour, after quiet hours; skipped when nothing happened and nothing waits | Everyone with the digest on |

**Engine.** `background/slack_dm.py` ticks every 30 s
(`omnigent/server/factory/slackdm/engine.py`). Decisions are level-triggered
from the open rows; transitions come from an event cursor (the last 12 h are
replayed after a restart). Each item is visibility-filtered per person (the
snapshot's `readable_for`: hidden projects never appear), held 10 min while
the person is active in the cockpit, held through quiet hours (and re-checked
when released; an event held that way may be up to 26 h old), grouped into at
most one DM a minute and 30 a day (the rest waits for the digest; last calls
and critical incidents still go). Every delivery is one `notify.slack` event
per item and person (`{to_user, key, stage, status}`): the dedupe ledger
(rebuilt from every row of the last 30 days, read in pages, and checked in the
store again right before each post, so a server roll or two processes during a
rollout never send twice; rows are pruned after 35 days), the cockpit's
"Slack · Lena 10:04" chips on decisions and incidents (`DecisionView.notified`,
`IssueView.notified`) and the Activity "Notifications" filter. An item whose
mark falls before the person linked is not sent (the welcome DM listed it).
`DecisionView` also gains `deadline_at` and `deadline_effect`.

Agent text is collapsed to one line, clipped, escaped and posted with
`parse: none` and no unfurls, so `<!channel>` and spoofed links cannot be
injected; links are built from ids on `https://$OMNIGENT_DOMAIN`. Slack
failures: retryable ones (network, 5xx, 429 with `Retry-After`) back off 5 s,
20 s, 80 s, 5 min, 20 min; a dead token (`invalid_auth`, `token_revoked`,
`missing_scope`, …) stops the factory's DMs and opens the `slack.bot` incident
for owners until a working token is saved; a person error (`user_not_found`,
`messages_tab_disabled`, …) marks their link; refused blocks are retried once
as plain text. A failure of a token that was replaced meanwhile never marks
the new one. Assumes one server replica: the ledger is not an atomic claim.

### Base branches & integration env

Each project has a **base branch**: the branch every PR of the project
targets. The New factory form has a factory-level **Default repo & base** row
(prefilled from `OMNIGENT_FACTORY_DEFAULT_BASE`, e.g.
`sample-org/sample-app#develop`, which `GET /v1/factory/preflight` returns as
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
  develop` with the `gh pr edit <url> --base <branch>` fix), pending while
  GitHub has not reported it. The first failure sends the PL one
  `[Factory notice · PR #<n> targets main, project base is develop · gh pr
  edit <url> --base develop]`. A PR off its base never reads READY FOR HUMAN
  MERGE; `sys_factory_pr` returns its `base_ref` next to `project_base`.
- **Base watcher** (`server/factory/base.py`). The PR poller's GraphQL query
  also reads the head of every `(repo, base)` the active factories watch (a
  query of its own every 2 min when no PR of the repo is due) and records it
  on the root (`omnigent.factory.base_head.<key>`). The heads are read with
  the repo's last PR batch and observed after its PRs are applied, so a
  merge and the move it caused in one poll carry the merged PR. When the head
  moves it emits `base.moved {repo, base, from, to, pr}`, mails the integration PL to
  redeploy and every work PL with an open PR on that base `[Factory notice ·
  base develop moved to <sha7> · sync open branches: git merge
  origin/develop]` (at most every 30 min per PL), and runs the
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
- **A test base.** To trial the factory without touching `main`, cut a test
  branch such as `develop` from main and start the factory on
  `<owner>/<repo>#develop`; every factory PR lands there and humans merge
  them. The preview tooling ref (`OMNIGENT_FACTORY_PREVIEW_TOOLING_REF`,
  default `main`) is separate: it only names where heads that predate the
  tooling get it from.

### Initiatives and acceptance runs

An ask bigger than one project (a core, a UI, an integration) is **one
initiative** (`I-<n>`, `server/factory/initiatives.py`, table
`factory_initiatives`). It is a small object next to the projects rather
than a reuse of a proposal (one wave each), an opportunity (discovery may be
off) or a bet (frozen per project): it holds the ask verbatim, who asked,
the acceptance steps, the projects, the planned waves and the newest
acceptance runs; its state is computed from the projects and runs.

- **Planning.** The lead files wave 1 as one projects proposal whose
  analysis file carries `initiative: {title, outcome, flag, acceptance[{step,
  expect, owner, needs}], skeleton, user_facing, why_wait, waves}`; a later
  wave extends it with `{"id": "I-<n>"}`. The server refuses
  `initiative_missing` (two or more projects for one person's ask, creates
  or a split, without a block; also a later plan for the same Room message,
  which extends that ask's initiative; `{"none": "<why>"}` opts out),
  `initiative_open_for_ask` (a second initiative for an ask that has one),
  `skeleton_waits` (the walking skeleton has a `depends_on`),
  `skeleton_owns_no_step` (the skeleton is the user surface, so it owns an
  acceptance step), `surface_waits_on_all` (a user-facing project, i.e.
  `user_facing`, the skeleton or a step's owner, waits on every other new
  project) and `unrelated_dependency` (a new project of the initiative
  waits on a project outside it), each unless `why_wait` says why; each
  dependency's own reason stays the project's `depends_why`. A plan that
  extends an initiative a person asked for (a later wave, a follow-up for a
  closed owner) is filed on that person's behalf when it only creates
  projects or makes minor updates, so it applies at once (Undo stays open;
  `confirm` autonomy still asks). Applying it stores the row and stamps each
  project's charter with `initiative: {ref, title, role, wave, flag}`, which
  the PL's charter renders; undoing the proposal takes it back.
- **Acceptance runs** (`server/factory/acceptance.py`). A base move that
  brings in a merged PR of an initiative's project (the one at the new head,
  or any tracked PR merged since the previous head) makes a run `due`; an
  initiative keeps one due run, at the newest head, and one run on its way
  at a time (a due run waits for the requested one to report or expire).
  When the base env reports ready at that head (or after 30 min anyway) the
  server sends the base env's PL (else the skeleton's) `[Factory acceptance
  · I-<n> run <k> · base <base> at <sha7>]` with an `ACCEPT …` line for its
  demo agent. The demo agent records the steps on the base env with the demo
  harness (`--skip-stale-check`; a step the env cannot run is skipped with
  its reason) and publishes it with `sys_factory_demo(title="Acceptance
  I-<n> run <k>", pr_url=<the run's PR>, head_sha=<the run's head>)`; only a
  title that starts so counts, and only the member it was asked of (or the
  base env's or skeleton's project) may report it (403 `not_your_run`). The
  server stores it as a demo of no PR (`pr_number` 0, left out of the
  project's PR demos) and the per-step result on the initiative. Each failed
  step becomes a task of its owner project (`accept-i-<n>-s<k>`), and its
  PL is told once; a later failing run updates the open task instead of
  adding one. A closed owner (not active, done, or merged with no PL left)
  goes to the lead as a follow-up to plan, and so does a step that still
  fails after 3 fix tasks (told once); no person decides. A run with no
  result expires after 3 h; `POST …/initiatives/{ref}/acceptance` runs one
  now. Once the skeleton is on the base env, the card and the page link
  "Try it on the base env" with the flag to turn on.
- **Reads.** `GET /v1/factory/factories/{fid}/initiatives` (one line each)
  and `GET …/initiatives/{ref}` (ask, acceptance with evidence links,
  projects with stage, role, wave, dependencies and what each waits on,
  runs, next wave, cost, time since the ask). The asks, the status summary
  and the Slack digest carry the same one line (`initiatives`, left out when
  none); Home shows a card per initiative above the projects. People rename
  it, rewrite its steps, close, cancel or reopen it with `PATCH
  …/initiatives/{ref}` (human origin; every change is an event).

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
  the product repo (`pnpm factory:preview`), with a Tiltfile preview mode that
  stamps `GET /__factory/preview` (`{repo, pr, sha, short, deployed_at, env_id}`,
  an Envoy direct response) and an `x-factory-commit` header on the app and API
  hosts, and creates the `factory-env-secrets` synthetic-session secret. It
  is on the product repo's main, so a PR head that
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
`sample-org/factory-demos`; unset, nothing is mirrored).

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
| `[Factory review · CHANGES REQUESTED on PR #<n> @<sha7> by <human> (cockpit\|GitHub)]` / `[Factory review · APPROVED PR #<n> …]` | the PL | A human's review from the cockpit or GitHub. A change request holds that head out of ready until a new head passes, the human approves, or (GitHub) it is dismissed. |
| `[Factory notice · PR <repo>#<n> change request by <human> @<sha7> dismissed on GitHub]` | the PL | The head is no longer held. |
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
| `OMNIGENT_FACTORY_AUTO_MERGE_S` | `60` | How often the server's auto-merger looks at ready PRs into the owners' auto-merge targets (`product.delivery.auto_merge`); `0` turns it off. A PR into a target that turns ready wakes it at once. |
| `OMNIGENT_FACTORY_ENV_PROBE_S` / `OMNIGENT_FACTORY_PREVIEW_PROBE_S` | `60` / `300` | Dev env and preview probe intervals. |
| `OMNIGENT_FACTORY_PREVIEW_DOMAIN` | unset | The deployment's env domain: the only domain env and preview URLs may use (SSRF guard) for a factory whose product profile sets no `product.delivery.preview_domain`. `OMNIGENT_ENV_DOMAIN` (the sandboxes' env domain) is read when it is unset. With neither, no env URL is accepted. |
| `OMNIGENT_FACTORY_PREVIEW_DOMAINS` | the env domain | Tier 0: comma-separated domains a product profile may name as its `product.delivery.preview_domain` (the env domain is always one). A definition change to any other domain is refused (422 `tier0`); a stored one the deployment no longer lists is ignored with a warning, and the env domain applies. |
| `OMNIGENT_FACTORY_DELIVERY_SEED` | unset | JSON keyed by repo, `{"<owner/repo>": {<product.delivery fields>}}` (`merge_policy`, `auto_merge`, `protected_repos`, `demo_paths`, `preview_domain`, `release`, `flags`): the profile a factory on that repo starts from. A new factory whose default repo is listed gets it in v1; a factory from before the product profile whose default or project repo is listed gets it once as a seed version at boot. A factory on another repo starts with an empty profile. Owners edit it afterwards; a factory whose profile was already seeded or set takes new `auto_merge` targets only from an owner in Steer. Example: `{"sample-org/sample-app": {"auto_merge": [{"repo": "sample-org/sample-app", "base": "staging"}]}}`. |
| `OMNIGENT_FACTORY_ENVIRONMENT_SEED` | unset | JSON keyed by repo, `{"<owner/repo>": {<environment fields>}}` (`summary`, `services`, `tooling`, `registry`, `base_image`, `pull_secret`, `notes`, `conventions`, `protected_branches`, `demo`): the environment profile a factory on that repo starts from, seeded like `OMNIGENT_FACTORY_DELIVERY_SEED` (in v1, or once at boot for a factory from before it). The seed's `repo` is its key unless it names one, so it reaches only that repo's projects. An invalid value seeds nothing (logged). |
| `OMNIGENT_FACTORY_ALWAYS_DENY_REPOS` | unset | Tier 0: comma-separated `owner/repo` values no factory agent ever writes, whatever a spec, a project or a profile says. Read once at boot. Unset, the server logs an error at start and the cockpit health strip shows "Config: no always-deny repos configured". |
| `OMNIGENT_DOMAIN` | unset | Public host of this server, e.g. `omnigent.example.com`. Demo PR comments and `demo_url` use it for absolute links; unset, they use the address the runner called. |
| `OMNIGENT_FACTORY_MAX_PREVIEWS` | `8` | Live previews per factory. |
| `OMNIGENT_FACTORY_DEFAULT_BASE` | unset | `owner/repo#branch` new factories default to (the start form's Default repo & base); a bare `owner/repo` means its `main`. |
| `OMNIGENT_FACTORY_DEMO_MIRROR_REPO` | unset | Private `owner/repo` demo posters and GIFs are mirrored to for the PR comment; unset, nothing is mirrored. |
| `OMNIGENT_FACTORY_PREVIEW_TTL_H` | `72` | Hours without human attention before a preview gets a park notice. |
| `OMNIGENT_FACTORY_PREVIEW_TOOLING_REF` | empty (recommended: `main`) | Product-repo ref the preview tooling is overlaid from onto PR heads that predate it; stored in new charters. Not the project's base branch. Empty: env_keeper uses `main`. |
| `OMNIGENT_FACTORY_QUIET_BEATS`, `OMNIGENT_FACTORY_MAIL_REPLAY` | on | `0` turns off skipping unchanged heartbeats / replaying queued mail after a restart. |
| `OMNIGENT_FACTORY_IDLE_BACKSTOP_MAX_S` | `86400` | With quiet beats, the backstop beat of a member nothing touched waits 3 intervals, doubled after each one that found nothing, up to this; any relevant event resets it. Mail never waits. |
| `OMNIGENT_FACTORY_INVARIANTS` | on | `0` leaves the `factory_invariants` admin default policy unseeded (see Invariants). |
| `OMNIGENT_IMAGE_TAG`, `OMNIGENT_SERVER_SHA` | unset | Image tag and commit recorded in the platform version (`factory_versions`). |
| `OMNIGENT_FACTORY_RETRO_HOUR` | `6` | UTC hour after which Sentinel gets its daily retro beat. |
| `OMNIGENT_FACTORY_SLACK_DM` | on | `0` turns Slack DMs off (the engine is inert anyway until an owner saves a Slack app token). |
| `OMNIGENT_FACTORY_SLACK_TICK_S`, `OMNIGENT_FACTORY_SLACK_API` | `30`, `https://slack.com/api/` | Slack DM tick (at least 5 s) and the Slack Web API base (tests). DM links use `OMNIGENT_DOMAIN`. |
| `OMNIGENT_FACTORY_SMITH`, `…_SMITH_GITHUB_LOGIN`, `…_SMITH_GITHUB_TOKEN`, `…_SMITH_REPO` | off | Tier 2 prerequisites (see Self-modification); the repo defaults to the deployment's omnigent fork at `staging` (`tier2.DEFAULT_REPO`). |

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
- **App tooling.** The preview tooling is part of the product repo's main.
  PR heads that include it use their own copy; only
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
  lead), spawn, restart (cockpit or peer) and relaunch. A new version that
  replaces an older one is announced in every factory as `version.changed`
  (with the bundles that changed).
- **Spans** (`factory_spans`, never pruned, idempotent on `dedupe_key`). An
  event-log recorder writes `project`, `stage`, `ready_wait`, `decision`,
  `task`, `env`, `preview`, `demo`, `incident`, `intervention` and `proposal`
  spans; every GitHub refresh writes `pr`, one `pr_round` per head and one
  `gate` per head and gate with `attrs.first_pass`; the supervisor puts a
  sub-agent's cost on its `task` span. Spans carry the version id and the
  root's `def_version` (resolving the definition first, so a new factory's
  rows are stamped from the start); new projects and issues are stamped the
  same way. Only a sha seen as the PR's head (`headRefOid`, a
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
thresholds) and `rules`. The supervisor
(`SupervisorConfig.for_factory`), the mailer, previews, the env prober, the
loop engine, the detectors, decision escalation, PL spawns, task cards and
briefs all read it.

Everything the factory knows about its product is data in six more blocks
(`definition_blocks.py`; no product name, metric or source lives in code):
`product` (name, kind, summary, links, `segments[]`, `areas[]`, competitors,
`delivery` {merge policy in words, protected repos, demo paths, preview
domain, `release`, flags}), `environment` (see below), `strategy` (`goals[]` `G-n`, guardrails,
non-goals, focus segments, the capacity `mix` adding up to 100), `discovery`
(`enabled` off by default, `seat.model`, `sources[]`, `rubric`, `thresholds`,
`budgets`, `cadence`, `autonomy`), `briefs` (per role, Atlas and Sentinel
included, ≤ 4,000 chars) and `role_defaults` (per role vendor and model
alias; the old, unread `models.ic|reviewer|planner` moved here and their
paths still write it). They stay inert until something reads them; discovery
also needs the server env `OMNIGENT_FACTORY_DISCOVERY`. Lists with ids change
item by item: `add` at `strategy.goals`, `replace`/`remove` at
`strategy.goals/G-2` or one field at `discovery.sources/forum/state`; goal
numbers come from the server and are never reused. A source never holds a
secret (Tier 0): a params key with a secret word in it (`accessToken`,
`client_secret`, `auth`, `pat`), a token-shaped value, a basic or bearer
credential, or a URL with a password or a secret query parameter is refused,
and a link never carries one either. A source always applies the redaction
floor. What it leaves out comes from its kind: `chat` and `feedback` are
`personal`, kept 90 days, on `opt_in` consent and start paused; `analytics`,
`observability`, `tracker`, `flags`, `team`, `mcp` and `repo` are
`internal`, 180 days; `web` and `reviews` are `public`, 365 days. A
`personal` or `sensitive` source stays paused. Changes to these blocks are not
mailed to members (their readers fetch them as data).

**The levels** (`structure`, read by cockpit v3) name the two trees work is
shown in: the product tree (product › goal › need › feature › project › task
› change › run) and the factory tree (fx › area › item). The keys and their
order are code; per level the block holds `name`, `plural`, `show`
(`always`, `when_any`: hidden while no node is on it, `when_many`: while at
most one is, `inline`: drawn inside the parent's row), `debug` and
`authority` {`create`, `move`: seats, `approval`: `none`, `autonomy`,
`owner`, `budget` or `ledger`}; beside them `home` (the level each tree's
home shows), `responders` and `verbs` per level (and for the study kind),
`kinds` (what the kinds not named after their level are called: a feature,
project or task reads its level's name), `kind_authority` (a study: Scout,
within the need's research budget), `order` {`cols`, `later`} (the sibling
column titles), `tiles_max`, `fold_single_child` and `node_header` (which
Room deliveries carry a node's header; see Threads per node). Owners rename a level,
and the kind named after it, with one change
(`structure.levels.feature.name`); the block is owner-only, never a trial,
and no agent proposes it (403 `path_not_allowed`). Cockpit v3 renames them
from the factory tree's Definition area: its `rename` verb (Rename levels,
`r`) is offered there and on no other node, and that page carries
`levels: true` (it shows the levels to every viewer). A structure stored when
items offered `rename` has it moved to `verbs.area` on read (`upgrade`).
`GET …/factories/{fid}/levels` returns both trees with `hidden` computed for
this factory (a level counts the implicit parents its nodes get), the maps
above and the definition `version`.

**Node refs** (`node_refs.py`) give every node one address that never
changes when it moves and is safe in a URL: `product`, `G-n`, `O-n`,
`ask:<message id>`, `need:<implicit key>`, `I-n`, `R-n`,
`feature:<implicit key>`, a project's slug, `T-n`, `adhoc:<pr id>`, `PR-n`,
`commit:<sha>`, `run:<session id>`, and in the factory tree `fx`,
`fx:<area>`, `C-n`, `D-n`, `member:<id>`, `env:<name>`. `normalize()` reads
any spelling (refs, the stored keys such as `task:T-4`, `project:<slug>`,
`pr:<repo>#<n>`, `member:<id>`, the chat's `!9630` and `#<slug>`, and with
the store `pr:<id>`, `task:<slug>/<key>` and `loop:<id>`) as the ref, and
`resolve()` finds the node behind it; unknown text is `None`, never a guess
(a spelling that names a repo never resolves to another repo's PR).
Decisions' subjects, issues' targets, events' subjects and chat thread names
are normalized on write to the node's key (`T-4` is stored as `task:T-4`); a
decision stores `PR-9630` as `pr:<id>` when the store knows the PR, while
events, issues and an unknown PR store `pr:9630`. Every key the server
writes is stored as it is, so old rows read back through `normalize()` with
no migration.

**Each project's home feature** is `factory_projects.feature` (`I-n`, or
NULL while its feature is implicit; `features.py`): the initiative that
planned it, pays for it and accepts it. Applying a projects proposal that
starts or extends an initiative writes it; the reconcile loop backfills rows
that have none from the initiatives' `plan.projects` (the one that added it
first) and then from the initiative its creating proposal
(`charter.why.proposal`) started; a project found homeless is settled, so
later passes read nothing until a new homeless project appears. Any other
initiative that lists the project gets an "also serves" link, never a
second parent.

**Node pages** (`GET …/factories/{fid}/n/{ref}`, `nodes.py`) project the
factory onto the levels one node at a time: the node, its breadcrumb (folded
implicit ancestors flagged), its state (the worst among its active
children; a closed project is done whatever tasks it left, and those read
"Closed"; a project between queued and ready moves only while its lead or a
teammate works, else it waits), its status line (below),
progress (acceptance steps passing on the base env for a feature, task
weight for a project),
its children in order columns (siblings layered by their dependencies;
Later holds an initiative's later waves, ghosts the projects an open
proposal would add), 60 a page (`children_page` is the next cursor; past 60
they are sorted by state), grandchildren as state counts, the levels below
it (only levels with a node), its results and its thread. Implicit parents
are keyed by their origin (`need:from-C-12`, `feature:from-C-12`,
`feature:by-<who>`, `goal:no-goal` once goals exist) and fold into their only
child; a level the definition hides now is left out and its nodes' children
hang one level up; integration and self projects sit in the factory tree
under Environments; the Team area draws the working agents (and any an
open ask names) and counts the idle ones. A PR number two repos share is
`PR-n` in the factory's repo and `commit:<sha>` in the other. Runs show
under their task's PR; a task with runs and no PR yet has no run page
(a follow-up). The projection is built from the snapshot cut to the
viewer (`filter_snapshot`: members, team chips, decisions, cost and burn,
the base env), memoized for 5 s per factory and set of sessions the viewer
may read, and dropped on any recorded write; open asks come from one
batched read of the messages live nodes quote. Cockpit v3 (`?cockpit=v3`)
always reads these pages; its sample factory lives only in Storybook and
the web tests. `GET …/n/{ref}/level/{level}` lists one level under a node
grouped under each item's parent (Home lists the features under the needs
they came from), all-done groups folded, 60 a group with a cursor each;
`state` and `q` keep only the nodes in one state or whose title or code
holds the words (`state=later` or `ghost` lists the held or proposed nodes
as the items); a group with nothing to show is left out.

**Lenses** (`node_lenses.py`) are the other ways to read one node.
`GET …/n/{ref}/lineage` reads top to bottom: each ancestor with its one
line (a need with its evidence, a feature with the proposal that approved
it), the node, the levels below it with their counts and, in the product
tree, its results. `GET …/n/{ref}/timeline?from=&to=` (epoch seconds or ISO
from 2000 to a week from now, at most 7 days apart; by default the last day
from the hour work began) draws one lane per child from the recorded spans:
`stage` spans for projects, `task` spans for tasks, as work bars; queued and
ready stages, PRs waiting for a merge, open asks and agents parked on a
person as waits, amber (`you`) when a person is the cause and grey when the
lane waits on another node or on the server (a past queue names the project
it waited on). An answered ask is amber only when it held work by the same
rule as an open one (a proposal on a project since done held nothing). The
wait a lane is in now is its status line, from when it began: never before
the lane's first bar nor before its last recorded work ended, so work that
ran after an ask was made stays visible; a merge is a person's from when its
PRs became ready. Gaps between bars are waits; a lane over several projects
works while any of them does (one bar); the alarms the server closed never
count, and lanes never join. `GET …/n/{ref}/money` lists the children's cost against
their budgets (the roll-ups' numbers), most first, the total, and `rest`:
what the node spent that no child holds (its leads and agents).

**Needs you and Find** (`node_sheets.py`). `GET …/factories/{fid}/needs-you
?tree=product|factory` lists every ask a person owes in one tree, ranked by
the work it holds (the blocker paths' weight: the estimates of the tasks it
holds plus the projects that wait on where it sits), each with its
breadcrumb and what it holds by name; ready PRs a person merges into one
base are one item; asks that block nothing come apart (`blocks_nothing`,
oldest first); the product tree also lists the alarms the server closed
with their causes, and `other_tree` counts the asks in the other tree.
`GET …/factories/{fid}/find?q=` finds nodes by ref (`T-16`, `#9876`,
`PR-9876`, a slug, a closed alarm's `D-n`), by what a decision or proposal
is about (`D-45`, `C-16`) and by title, each with its breadcrumb; an empty
query lists the work that holds an ask. Done work stays in the tree, so it
is found however long ago it closed.

**Since your last look** (`last_look.py`, `factory_node_reads`): a person's
last look at a node is a mark (`PUT …/n/{ref}/seen`, or `GET …/n/{ref}
?seen=1` from a person, never a runner), one per person per node, only
moving forward. A node they never opened reads its nearest ancestor's mark.
The node page then carries `rollup.since` (the three most significant
changes in its subtree since that mark, and how many more) and `changed` on
each child; events are placed on the node they are about (a merge on its
change, a task, a new ask on its node, a project, an applied proposal on
the highest node it touched), so work the viewer cannot read says nothing.
Most significant first: a new ask that holds work, a merge or a failure,
work added, a change applied, an answer, a task done, a stage moved; three
merges or more read as one ("4 merged"); a server-closed alarm, an ask that
blocks nothing and a builder going idle on an unfinished task are not
changes.
A proposal's decision is stored as blocking nothing; the projection counts
it as holding work while the proposal adds a project or a rule, or changes
a project that is still active, so "needs you" follows what really waits.

**Status lines** (`node_status.py`) say in one line why a node is or isn't
moving, ending at the person who must act. The blocker path reads the
node's own waits first (`waits.py`: the snapshot's `waiting_on` for a
project, `task_waits` for a task, a ready PR's merge), most decisive first:
an ask a person answers ends it ("needs you: Apply C-16 (D-45)"), a
dependency, a merge of another project or a hold on another node hops there
(`→`). With none, it steps down (`›`) into the child that blocks the most:
paths that end at a person first, by the weight they hold (the estimates of
the tasks they hold plus the projects that wait on where the ask sits), then
dependents, then the earliest column. A node's own open decision, or one on
a child it was proposed to get, ends the path when no child's ends at a
person; a path that ends at a person always wins over one that ends at the
server or at moving work. A project that has not started and that nothing
holds ends at who starts it (the snapshot's `start`: "the server starts it
within a minute", "waits for Atlas to start it", "starting: pulling the
image", or the capacity wait with how full the machines are); so does the
base env's setup project in the factory tree, and a task nothing holds ends
at its project's lead ("Not started: waits for Juno to start it"), or at who
starts the project while nobody has. A node with nothing blocked under it and
work not started steps down to that work ("Not started: T-16 → waits for Juno
to start it"), never a bare "Not started". It stops at a
cycle, a person or the server; a line shows at most 6 segments, keeping the
first steps, an elision (`…`) and the end, so the person and their ask stay.
Ready PRs a person merges into one base are one ask (`merge:<base>`,
answered on the code host). The header's line carries its label ("Not
started for 7 h:", "Held on purpose:", "Top blocker:" while other work
moves, "Not moving:"), what else the path holds (`also_holds`), a numbered
`why` and a `note` naming met links that had no reason; a tile's line
(`status_compact`) is only the end of the path. Steps into implicit parents
and, from a root, into levels above its home are left out of what shows.

**Roll-ups** add burn (the last hour's spend from `turn` spans, a turn still
open counting up to its session's cost now; the root shows the factory's own
rate, which also pays for work no project owns), a budget (the node's own
cap, else the sum of its children's caps when every child has one), an ETA only when
every unfinished task's kind has at least five observed durations (a
project's remaining critical path; a feature's slowest project per column,
summed), feature progress from acceptance on the base env with build
progress second, Try it (the acceptance steps that pass there, on their
feature and the project that owns each; "API only" while a user-facing
project is not built) and, at the root, the base env (`env`; the factory
tree's Environments area carries the same) and the alarms the server closed
(`alarms_closed`). A change is in the base env when the
commit the env runs contains its merge (`base.contains`): the base watcher
records that commit on the root (`omnigent.factory.base_env.<key>`, served
as `factory.base.deployed`) when the integration project's env reports
ready, placed in the base's history (when the watcher saw it become the
head); a merge into that repo and base is in every head the watcher saw
after it landed. A deployed commit not yet placed holds only itself until
its next move or report places it. The base env's smoke shows only when the
server's probe ran.

**Alarms closed with a cause.** A stall, claim or done-check alarm the
server answers, or expires with a cause, records why (`cause`), the ask a
person really owes (`real_ask`, with what it has them do), the node it was
about and what it asked on its closing event
(`server_decisions.closed_data`); a loop that runs again expires its alarm
with "T-16 runs again: …". An expiry with no cause, or a claim's "Keep
waiting?" that a newer alarm replaced (`superseded_by`), is not a closed
alarm. A no-progress stall that ran again because its task is now held on
purpose is answered with "T-16 is held on purpose for C-16; a held task is
not stalled" and the hold's decision as the real ask, and the lead is told
nothing; an open one on such a task is no ask on the node pages even before
the tick closes it. A wall stall, an exhausted or a thrashing loop stays an
alarm whatever holds its task. The factory tree's Health lists
today's closed alarms as items (`D-n`) with `closed_alarms` on its page, the
node each was about carries an "alarm closed" chip, and the root's roll-up
counts them (`alarms_closed`).

**Threads per node** (cockpit v3). Beside `factory`, `project:<slug>` and
`pr:<id>`, the Room has a thread per need, feature, study and task:
`need:<ref>` (`need:O-3`, `need:ask:<id>`, `need:from-C-12`), `feature:<ref>`,
`study:R-<n>` and `task:<ref>` (`task:T-16`; `T-16` spells it too). A key
resolves only to a node of this factory (404 `unknown_node`). Who answers is
the definition's `structure.responders` per level: Atlas for features, Scout
for needs and studies (Atlas while discovery is off), the project's lead for
a task (the delivery names the task); `null` routes a plain message to
nobody, with a note. A lead posts anywhere, a PL also in its tasks' threads,
Scout in need and study threads; agents' messages still route only by
@mention, so node threads never loop. A delivery in a node thread carries
the node's header as the cockpit shows it (`node_context.py`: the path, why,
status line, roll-up and open asks, and how to read the page with
`sys_factory_node`). The definition's `structure.node_header` says where:
`node_threads` (the default: only these per-node threads, which only cockpit
v3 posts in, so `#factory`, a project's and a PR's thread keep the envelope
v2 knows), `all` (those threads too, about the product, the project or the
change), or `off`. An owner sets it like any definition field (`PUT
…/definition` with `{op: "replace", path: "structure.node_header", value:
"all"}`). What the server adds around the delivered messages stays within
the factory's message cap (8000 chars): the header's detail gives way first,
then the oldest thread context, then the whole header. The messages
themselves are never cut. Cards follow their node: a decision about a task, need, feature
or study is also carded in that node's thread (the thread above keeps its
card), an alarm the server closed is a `closed_alarm` card in its task's
thread, and the cockpit's own cards (`why` for a bare "why" in a node thread,
which is not routed; `offer`; `preview_applied` after a verb, with its Undo)
go only to their node's thread. `GET …/chat/threads` names each thread's
node (`node_ref`) and, with `nodes=1`, lists the node threads that have
messages; preflight says `node_threads: true`.

**Steering at a node** (`node_verbs.py`). `POST …/n/{ref}/preview {verb,
args, based_on}` plans a structural verb over the facts and stores nothing:
its effects in plain words, the parent's columns with the change as a ghost
(`columns_after`), the roll-up before and after (`rollup_delta`), who
approves (`approver`, `can_apply`, `who_may`), warnings, whether it can be
undone, and a `preview_id` that digests the plan and the facts it touches.
`POST …/n/{ref}/apply {…, preview_id}` re-plans over fresh facts and applies
only that plan (409 `stale`, with the new preview, when what it touches
moved), then posts a `preview_applied` card and returns the node page with
an Undo token; `POST …/undo/{token}` takes it back once within 10 minutes
(the person who applied it, or an owner). The card carries the token
(`meta.undo_token`, `undo_until`) and who applied it (`meta.by_user`), so
cockpit v3's chat offers the same Undo after a reload, to that person or an
owner only; the card an Undo posts (`meta.undone`) names the token it
undid, and the applied card stops offering it. The verbs run today's cores: `add`
under a feature files the person's projects proposal extending it and
applies it at once ("Plan X now" goes to the feature's thread; a title that
matches a held item pulls that item forward; `col: "later"` holds it in a
later wave), under a project adds a task, under a need starts a feature
(owners); `start` relaxes a project's unmet project dependencies to their
newest merged change (`PR-9876`) and starts its lead; `pause`/`resume` run
the controls of every lead under the node (a task: a hold; a project whose
lead has not started yet still starts once what it waits on is met, and the
preview names it); `move` files a project under another stored feature or a
feature under an opportunity, one level up only (422 `wrong_level`);
`reorder` edits `depends_on` among siblings (with `after`, the node lands in
the column after that sibling's, whatever `col` says, and the preview shows
that column); `split`/`merge`/`close` are the proposal ops (no Undo); `name`
makes an implicit feature or need a stored one (a need through the discovery
ledger's own create), or files its children under one; `team` is a `PUT
…/definition` team change (Undo pins the version before, only while the
version it made is still in force: 409 `undo_closed` otherwise). An add under
a feature joins the wave it builds now, so its planned later waves stay
ahead; a held item pulled forward leaves the wave that held it. A verb is
served only where its node's level lists it in `structure.verbs` (resume
with pause; reorder, split, merge and close with move; 422 `not_here`
otherwise), and the node page offers exactly the verbs served there. Who
applies it follows `structure.levels[<level it changes>].authority`: empty
seats mean nobody (422 `not_here`); an `owner` approval, or seats with
`owner` but not `operator`, leave it to owners; otherwise editors apply it
(403 `authority` with `who` when the viewer may not). Beside `api.md`'s
fields a preview carries what cockpit v3 reads (below): `title`,
`approver_note`, `moved_from` (Start now, Order, a pull forward),
`pulls_forward` and `apply_label` (Add child), and `parents` (Move and Name
it: the stored nodes one level up). Move and Name it with no parent and no
name are the form's first read: a 200 with `parents`, `can_apply: false` and
nothing to apply (400 `invalid_args` if applied). Apply and Undo answer with
a one-line `message`; Undo's `expires_at` is an ISO time.

**Steering at the node (cockpit v3).** Every structural verb on a node page
(Add child, Start now, Team, Autonomy & merge, Pause / Resume, Move, Name it /
file under; their keys `a s t u p m n`) opens a preview under the header
before anything changes: the parent's First / Then / Later columns with the
change as a dashed ghost (and its old place struck through), the roll-up
change, the effects and who approves. Apply sends the preview's id and the
page's facts version (`based_on`); the toast offers Undo, and the node keeps
"Undo (n min left)" for the rest of its 10 minutes. Add child pulls a held
child whose title matches forward instead of copying it; Move offers only
parents one level up. Plain words in the node's chat ("start the panel now",
"also plan …", "swap luz to codex") open the same previews, never an apply.
Team is the team editor in a panel (every member's vendor and model, nothing
preselected, the $/h before and after) on the definition's own preview, a
definition version and Undo by re-pinning the version before; the other
verbs preview and apply through `POST …/n/{ref}/preview|apply` and undo
through `POST …/undo/{token}`. An answer that applied a projects proposal
(Apply on D-45 applies C-16) keeps an Undo while the proposal's
`undo_until` lasts (a rules, tuning or code change, a cancel, split or
merge has none, so none is offered): `POST /v1/factory/decisions/{id}/undo`
undoes the proposal and puts it back to pending under a fresh decision, so
the work held for it waits again (a hold the sweep let lapse comes back,
and its PL is told `[Factory notice · HOLD BACK …]`) and the ask is in Needs
you again. An answer the server stored but whose proposal failed to apply
says so ("D-50 answered; applying C-17 failed: …"), with no Undo. Sheets open from the address:
`?peek=needs` (Needs you, ranked by what each ask holds, the asks that block
nothing grouped with "Apply all", the closed alarms, the other tree's count;
from `GET …/needs-you`), `?peek=env` (the base env), `?peek=try&at=<ref>`
(Try it: the passing steps by name, the flag and the sign-in note) and
`?peek=keys`; ⌘K finds nodes by ref or title (`GET …/find`), and the
search's projects, PRs and tasks open their node pages. Three seconds on a
page marks this person's last look (`PUT …/n/{ref}/seen`); the "since" line
and each child's changes it showed stay for the whole visit (an apply or an
answer reads the page again, counted from the new mark), and the next visit
starts from the new mark. A refused apply (409 `stale`, 422, 403) reads the
page and its preview again, so Apply works on what is there now. The Team
Undo re-pins the version before only while the version it made is still the
one in force (read again first); a later version says "undo from Versions".

**Lenses and the factory tree (cockpit v3).** Below a node's header its
children show in one lens, kept in the address (`?lens=`; `v` steps to the
next): Map (tiles) and List (rows), Lineage (the ancestors top to bottom with
their one line, the node, the counts per level below and the results; in the
product tree only; a node with nothing below folds it into one line, and on
a phone the Lineage button opens it as a sheet, `?peek=lineage&at=<ref>`),
Timeline (one lane per child from `…/timeline`: work solid, waits hatched,
amber only when a person is the cause, the stretch since this viewer's last
look shaded, nothing drawn between lanes) and Money (each child's cost
against its budget from `…/money`, most first, the total and what no child
holds). The bar offers a lens only when it has something to show: Timeline
once the server drew a lane, Money when two or more children have a cost or
a budget, neither on a level grouped under parents (Home). Every page asks
`…/timeline` whether it has lanes, so the route reads the spans once per
projection (the memoized tree) and the client never asks again on a refocus. No lens draws an
SVG line or path. The top bar's Product | Factory switch (with the asks
waiting in the factory tree) takes the place of v2's places; the factory
tree's Health lists the alarms the server closed with their causes and the
real ask, Environments shows the base env (what it runs, its health, its
sign-in, a way to try it; the env's `area` names it, so the env sheet opens
it, and `ref` the project that sets it up) and Definition the levels with Rename levels: the
product's levels with their names and plurals, previewed through
`POST …/definition/preview` and saved by `PUT …/definition` on the version
read, with Undo by re-pinning the version before.

**What cockpit v3 reads from a preview beyond `api.md` §4 (the S12
contract).** `POST …/n/{ref}/preview` answers `{preview_id, columns_after?,
rollup_delta, effects, approver, warnings}`; the web also reads these
optional fields, each with a fallback when absent (`CLIENT_PREVIEW_FIELDS`
in `web/src/components/factory/v3/nodeWire.ts`, checked against this list
by `nodeSteer.test.tsx`):

- `title`: the panel's heading ("Start assistant-panel-ux now"); else the
  verb's own words.
- `moved_from` (`{ref, col}`): where the changed node sits now, struck
  through in the mini columns.
- `approver_note`: "Who approves" in words ("You, as owner. Applies now;
  Undo for 10 min."); else words for `approver`.
- `pulls_forward` (`{ref, title, held_for?, note?}`): the held child a new
  child's title matched; Add child then says it pulls it forward.
- `parents` (`[{ref, title}]`): the parents one level up a node may go
  under. Move and Name it / file under ask the preview with no `to` (the
  form's first read) and offer only these; a server that answers that read
  with an error offers none and says so.
- `apply_label`: Apply's words ("Pull forward and plan").

`rollup_delta` and `effects` are read as `[{label, html}]` lines, as
sentences, or (`rollup_delta`) as `{field: {before, after}}` or the S12
server's `{nodes: [{ref, title, items: [{key, label, before, after}]}],
text}` (one line per changed item, then the sentence; `null` when nothing in
the roll-up changes). The arguments
the web sends: add `{title, col, depends_on?: [ref], depends_why?: {ref: reason}, held_for?}`, start,
pause and resume `{}`, auto `{auto_merge?, autonomy?, base?}` (what is left out
stays as it is; `base` once the preview asks for it, `args.base`; the server
also takes `repo`, and sends its effects as `{label, text}` lines), move `{to?}`, name
`{title?, to?}`; a blocked form (Add child without a title, Team with
nothing picked) asks no preview. The apply's one-line result is read from
`message`, and its `undo.expires_at` is an ISO time.

**The environment profile** (`environment`, `environment.py`) holds the
facts one product's dev env and previews need, which the bundles used to
carry: `repo` (the repo whose projects run it; unset: every project's),
`summary` (how the dev env starts), `services[]` `{id, name, port, health}`
(`id` is the service's key in `env.json`'s `urls`, `name` its Deployment and
Service), `tooling` (where the env tooling lives, known tooling fixes),
`registry`, `base_image`, `pull_secret`, `notes[]` (for env_keeper),
`conventions[]` (repo conventions builders follow), `protected_branches[]`
(literal names: the guards compare them exactly, so a glob is refused) and
`demo` {`storage`, `clear`} (how the app keeps a demo's synthetic session in
localStorage; `{user_id}` stands for the signed-in user; values are
shell-safe, since the demo agent passes them in single quotes). The bundles keep
the factory's env protocol (the `factory:*` scripts, `env.json` and
`preview.json`, the verify marker) and read the rest: the PL's charter
renders an `Environment` block (dev env, services with the in-cluster check
form, tooling, conventions, protected branches) for projects on its repo,
every task card a `Repo conventions:` line, and `sys_factory_rules()` returns
the block in full as `environment` (null when it does not apply to the
caller's project repo), which env_keeper and the demo agent read at the
start of every command (most fields are on no charter, so no CHARTER UPDATE
announces them). Its protected branches join the guards' defaults (`main`,
`master`, `staging`) as `factory_protected_branches`. Only owners change it
(no agent seat may propose it, since its facts reach shell commands), as a
version, never a trial ("facts, not a way of working"); a change that moves
a charter's `Environment` lines sends that PL a CHARTER UPDATE asking it to
have env_keeper and the demo agent read it again. Every text field refuses a
secret. A deployment seeds it per repo with `OMNIGENT_FACTORY_ENVIRONMENT_SEED`.

A version stores these blocks (and `goal_seq`) only once they differ from
their defaults, and role defaults that set only the `ic`, `reviewer` or
`planner` model alias are stored as the old `models` block. A server from
before the blocks therefore still reads every version of a factory that has
not used them; rolling back past them for a factory that has needs a reader
that ignores unknown blocks. Such a server still reads that factory (it falls
back block by block), but it cannot write its definition: restore the last
version without the block first (Versions › Restore). The environment boot
seed stores the block at once for a factory on a seeded repo, so after such a
rollback an owner restores the version before the seed.

**Teams** are a block too (`team_spec.py`, `team.py`). `team.templates[]` are
named shapes (`standard`, every agent of the PL bundle, is the default a new
team starts from; `pair`, `lean`, `investigation`); `team.projects.<slug>` is
one project's own team: `template`, `builders_at_once` and `budget_usd`
(`null`: the charter's) and `members[]` `{id M-n, handle, role, agent, vendor,
model}` plus `briefs` (`M-n` → text ≤ 1,000). A project without its own team
runs today's bundle (every agent of the PL bundle on its default model;
template edits never change it, and the snapshot says `follows: "bundle"`).
Its first change (`replace team.projects.<slug>/members/M-2/model`) stores
that team as its own, so every diff line names a real member and nothing else
changes at run time; a template applies only when a project switches to it.
Member numbers are handed out by the server and never reused, and handles are
unique within a team. A member's `model: null` is its bundle's default
(`role_defaults` does not steer dispatch yet). Removing the last member a
ready gate needs (a reviewer, an env keeper while the charter needs an env, a
demo agent while previews are on, the last builder) is refused with 422
`gate_requires_role` (Tier 0), and so is a template without a reviewer and a
builder; a template a team names cannot be removed (409 `in_use`); the project
lead stays. When a charter later needs a role its team lacks (env or previews
turned on), that role's agent stays dispatchable and the snapshot flags
`missing_gate_role`. Editors apply class-A team changes and may Undo their own
within 10 minutes; only Atlas and Sentinel may propose team changes (class B).
`GET …/catalog[?project=]` lists the roles the PL bundle ships, the vendor ·
models each runs (project leads, env keepers and demo agents run on Claude Code
only today), the gate each is needed by, $/h estimates from the cost ledger,
the templates (with the project's locks) and the roles no bundle covers. The
preview's team effects say the rate while working, the cost to finish against
the budget, who restarts and when (`when`: `after_current` or `now`, which
interrupts), the review route and the TEAM UPDATE it sends. A PL whose project
has its own team gets a `Team` block in its charter and one
`[Factory control · TEAM UPDATE · vN · by <who>]` per change;
`sys_factory_task(op="start")` returns the member, `agent` and `model` per
task (the PL passes `args.model`). Dispatches outside the team are recorded
(`team.dispatch_outside_spec`) while `OMNIGENT_FACTORY_TEAM_SPEC=warn` (the
default). `enforce` needs the PL's `team_guard` policy, which is held out of the
`factory-pl` bundle until every PL pod runs a sandbox host image that ships
`omnigent.policies.builtins.factory_team` (a runner that cannot import a policy
path denies every tool call); add it back before turning `enforce` on.

In the new cockpit, Projects shows outcome-first cards or a board by stage
(`?view=board`), filtered to all, from evidence, requested, needs you or stopped
(`?f=`), with shipped work folded; the table moved to Debug › Projects table.
A project's page is its header (what users get, the facts, the health chip),
why it exists (the why-chain from evidence to readout, missing links hatched,
and the stage bar), its team, then the Overview, Work, History and Charter tabs
(`/projects/:slug/{work,history,charter}`; an old `#section` link opens the tab
that holds it, and the classic cockpit sends a tab address to its section).

In the new cockpit a project's page draws its team as an org chart (the lead,
then Build, Check and Run), and `/projects/<slug>/team` (also `e` on the
project, "Change model…" on an agent, Edit team on Teams) is its editor: the
staged changes compile to the team paths above, the server's preview prices
them and says who applies them, and Apply makes one definition version with a
10-minute Undo (a re-pin of the version before it). History restores an
earlier team of that project only, as a staged replace of
`team.projects.<slug>`. Apply stays off, so the editor only stages and
previews, until the server runs with `OMNIGENT_FACTORY_TEAM_SPEC=enforce`.

| Paths | Class |
|---|---|
| `cadence.*` | A when slower, else B (0 = off is B) |
| `loops.*.attempts\|rounds\|per_head` | A when lower, else B |
| `loops.*.usd` | A when lower (editors apply it at once), else B |
| other `loops.*` | B |
| `mail.*` | A when lower, else B |
| `supervisor.*`, `alerts.*`, `decisions.*` | A |
| `previews.*`, `caps.*`, `rules` | B |
| `engine.*`, `autonomy.*` | B (owners) |
| `budgets.*` | A when lower (editors apply it at once), else B (owners) |
| `product.*`, `strategy.*` | B (owners); never a trial, applies as a version |
| `discovery.enabled` | A when turning it off, else B (owners) |
| `discovery.sources.<id>` | A when it only narrows (pause, less budget, fewer roles), else B |
| `discovery.budgets.*` | A when lower (editors apply it at once), else B (owners) |
| `discovery.cadence.*` | A when slower or off, else B (owners) |
| other `discovery.*`, `briefs.*`, `role_defaults.*` | B (owners) |
| `structure.*` | B (owners only: agents never propose it); never a trial, applies as a version |
| `team.templates.*` | B (owners); never a trial (a template reaches a project only on a switch) |
| `team.projects.<slug>` member added, vendor or model changed, brief | B (owners) |
| `team.projects.<slug>` member removed or renamed | A (editors apply it) |
| `team.projects.<slug>.builders_at_once`, `.budget_usd` | A when lower, else B |
| any `team.projects.<slug>` change | never a trial (one team merges about one PR a day) |

Owners apply the owner-only paths (`engine.`, `autonomy.`, `budgets.`, and
the seven blocks), except the class-A changes editors apply (below). For agents
the class of those paths does not matter: an agent's proposal of one always
waits for the owners (class B), whatever the table says. Editors
(`PUT …/definition`) apply a change that only narrows a team or a source, or
only lowers dollar budgets (`budgets.*`, `discovery.budgets.*`, `loops.*.usd`:
lowering one only reduces spend), and may Undo their own for 10 minutes;
raising a budget stays the owners', and so do trials (an editor's change
applies at once, never as a trial: 403 `owners_only`). A $0 budget is a cap
like any other: with `discovery.budgets.per_ask` at 0, a research ask that
spends anything waits for an owner. The preview shows the table's class. In
the blocks, agents propose only their seat's paths (403
`path_not_allowed` otherwise): Scout `product`, `strategy` and
`discovery.sources`; Sentinel `discovery.rubric`, `discovery.thresholds` and
`briefs.scout`; Atlas none; nobody `environment` or `structure`.

**Proposals** (`C-n`, `sys_factory_propose`): leads propose tuning, rules and
code; a PL proposes rules for its own project. Each cites evidence ids (422
without) and one expected metric effect; at most 2 per agent per day.

- **A** goes to the other lead, who co-signs
  (`sys_factory_propose(op="cosign", kind=…, id="C-n")`; never their own).
  It applies at once as a new version on **trial** (default 48 h).
- **B** opens an owners `proposal` decision; Approve applies it as a trial
  (strategy and product profile changes apply as a version: a trial would
  judge them on delivery metrics; so do discovery's settings, and its method
  on a server that does not run discovery).
- **People** edit with `PUT /v1/factory/factories/{fid}/definition`
  (`{changes, note?, trial?, base_version?}`, MANAGE, human origin): a human
  `C-n` applied at once. `POST …/definition/preview` (same body, or
  `{restore: K}`) is its dry run: the diff with each path's class, who can
  apply it, the effects, what blocks it, whether it may run as a trial, and
  `rerank`: the open opportunities a strategy or rubric change moves, each
  with its position, rank and band before and after.
- **One trial per block.** A trial on one of the six blocks (`briefs`,
  `role_defaults`, …) refuses a second trial there with 409 `trial_overlap`
  naming the running one; applying directly stays possible. Today's settings
  (`cadence`, `loops`, `alerts`, …) and rules trial side by side, as before.
  `trial: true` on strategy or the product profile is 422 `not_trialable`
  (`reason` "judged on outcomes"). Discovery's method (`discovery.rubric`,
  `discovery.thresholds`, `briefs.scout`) trials on discovery guards on a
  server that runs discovery and in a factory with `discovery.enabled` (else
  it applies as a version: "needs discovery guards", or "discovery off for
  this factory"), never in one trial with other changes; its other settings
  apply as a version ("a setting, not discovery's method"). A proposal filed
  meanwhile says it will wait.
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
  one window after it ended (2× its window). A trial of discovery's method
  runs 7 to 56 days (14 by default; `trial_h` 168-1344, else 422
  `invalid_trial_window`; an agent's out-of-window `trial_h` becomes the
  default with a warning, since its tool bounds it to 24-168; the approve
  option reads "trial 14 days") on the discovery guards (quote failures and
  privacy events stay at 0, accepted bets −10 pp, $ per accepted bet +20 %),
  its expected effect may name a discovery metric, and its n is the bets
  people decided (≥ 3; none decided is idle). The guard set is stamped on the
  proposal when the trial starts (`result.guards`) and judged on that.
- **Idle trials**: a window with no merged PR, finished task or ended sub-agent
  run proves nothing. Such a trial is extended by its own length (at most
  twice, one line in `#improvements` each time), then the owners decide with
  Keep recommended; unanswered for 24 h, it stays applied. A regressed guard,
  or an incident (or a human's or Sentinel's finding) that names `C-n`, skips
  all of this: the trial takes the revert path above.
- **Revert** (`POST …/proposals/{ref}/revert`, owners, one click) re-pins the
  parent version when it is still in force, else undoes only that change as a
  `revert` version. `…/keep` ends a trial early; `…/definition/pin {version}`
  puts any stored version back, and re-pinning the parent of a version made
  in the last 10 minutes is its Undo (the version shows `undone_by`).
  `POST …/definition/restore {version, note?}` (owners) stores an old
  version's document as the next version, so history stays linear (its Undo
  is another restore); trials whose change it takes out end as reverted.
  `GET …/definition`, `…/definition/versions` (with `undone_by` and
  `restored_from`) and `…/definition/diff?from=&to=` show them.

**Tier 2: the Factory Smith lane** (code and bundle PRs to omnigent) ships
off. It turns on only when `OMNIGENT_FACTORY_SMITH=1`, rulesets requiring
review, CODEOWNERS review and status checks protect the Smith repo's branch
(`OMNIGENT_FACTORY_SMITH_REPO`, by default the deployment's omnigent fork at
`staging`; read through the GitHub API at server start)
and a dedicated factory identity
(`OMNIGENT_FACTORY_SMITH_GITHUB_LOGIN` and `…_TOKEN`) has neither admin nor
maintain rights. `GET /v1/factory/preflight` returns `tier2 {enabled,
reason}`. While it is off, `kind=code` proposals are recorded with their
evidence and never run.

### Discovery ledger

What the factory learns about its users is a ledger the server keeps
(`omnigent/server/factory/discovery/`, tables `factory_signals`,
`factory_signal_links`, `factory_opportunities`, `factory_score_snapshots`,
`factory_source_state`). It is dark until `OMNIGENT_FACTORY_DISCOVERY=1` and
the factory's `discovery.enabled`; no product knowledge lives in it.

- **Signals** (`S-n`) are written only by `ledger.py` (Tier 0): redact
  (`redact.py`: emails, phones, tokens and keys, IPs, secret-bearing URLs,
  card-like numbers become `[email]` …), hash the account id with a salt per
  factory, score the text for prompt injection (a suspect is flagged
  `injection_suspect` and kept out of scoring), clip to the source's
  `quote_max`, store. A `(source, pointer)` is stored once; the evidence never
  changes. Labels (paraphrase, kind, intensity, force) are the only later
  writes, and the server's kinds (`hunch`, `idea`, `behavior`, `test_result`,
  `simulation`) never change. Text past the source's `keep_days` is dropped by
  the hourly upkeep (`background/discovery_ledger.py`); the row and its counts
  stay. A paused source, a `personal`/`sensitive` one, or one that asks for a
  redaction the server cannot do yet (`name`, `address`) stores nothing. Only
  the server's own source writes `test_result` (a readout's verdict), and only
  analytics and observability sources write `behavior` (an aggregate's reach).
- **The team source**: people's hunches (`POST …/hunches`, `hunch: …` in any
  Room thread, "I think … because …" in `#factory` or `#discovery`) and requests
  ("we need …", "we should have|add|build …") are `hunch` signals of the
  `team` source (weight 0.1); so are repo items whose author is on the team.
- **The evidence meter** (`evidence.py`): hunch, weak, moderate (≥ 5 accounts'
  worth from ≥ 2 source kinds within 90 days), strong (plus "do" evidence),
  proven (a won test). An account counts once per opportunity; evidence
  decays with a 60-day half-life; counter-evidence of a third lowers it a
  level; votes, pins, simulations and suspects never count. Every number is
  a `discovery.rubric` field.
- **The rank** (`rubric.py`): `reach_s × intensity × fit × confidence`, bands
  Now ≥ 0.20, Next ≥ 0.10, Later; each part with its input and reason. The
  intensity is the median label with the meter's per-account cap, so one
  loud account is one voice. The formula is code, the numbers are rubric
  fields.
- **Opportunities** (`O-n`, `opportunities.py`): status is derived (idea,
  validated, proposed, building, shipped, measured, kept, dropped, parked,
  merged) from the evidence, the `C-n` that name them and their projects'
  `charter.why`. An agent's opportunity must be a need in job form that names
  no solution, with two different solutions, cited ids that resolve, and
  quotes (in any quote marks, in any field it writes, `bet_note` included)
  that match a stored excerpt as whole words (`quotes.py`, also the bets'
  quote check): an ellipsis joins at most three parts of three words or
  more, and may not drop a negation.
- **People's levers** (`levers.py`, `api/discovery.py`, prefix
  `/v1/factory/factories/{fid}`; reads READ, writes EDIT and human origin):
  `GET /discovery`, `GET /opportunities`, `GET /opportunities/{O-n}`,
  `PATCH /opportunities/{O-n}` (`vote`, `pin`, `park`, `unpark`, `kill` with a
  seeded reason, `revive`, `merge`, `split`, `rename`, `move`,
  `cancel_research`, `undo`), `POST …/research`, `POST …/plan` (the person's
  own `#factory` message asking Atlas, so the bet counts as requested),
  `GET /signals`, `GET /signals/{S-n}`, `POST /signals/{S-n}/attach|detach`,
  `POST /hunches`, `DELETE /hunches/{S-n}`. Killing an opportunity with a
  running bet and `bet_anyway` are for owners. Each act writes an
  `opportunity.*`, `signal.*` or `hunch.*` event naming who did it and, within
  10 minutes, `{op: "undo", action}` puts it back (409 `undo_stale` once it
  changed again). Merges, splits, renames, moves and detaches are kept as
  `calibration` feedback on the `opportunity` or `signal`; a kill's reason as
  `closure`; an undo takes that feedback back too.

### Source gateway

Only the server reads a source (`discovery/gateway.py`, `discovery/sources.py`,
`discovery/kinds/`); it is the one path from an agent to one, dark like the
rest of discovery. A source is an owner-approved `discovery.sources[]` entry
(class B to add or widen); nothing in code enables one.

- **Kinds without a secret**: `repo` reads issues, discussions and releases of
  the source's `params.repos` (else the repos of the factory's projects) with
  the factory owner's GitHub connection, like the PR poller, through fixed
  read-only GraphQL documents; an author GitHub calls `OWNER`, `MEMBER` or
  `COLLABORATOR`, and a release of the factory's own repo, is the team's and
  becomes a hunch; bots are skipped; searches lose every qualifier and stay in
  the source's repos. `web` fetches public pages (`web_io.py`: `http(s)` on
  default ports, host names sent as ASCII (IDNA), every address public
  (also an IPv4 address inside NAT64 or IPv4-compatible IPv6) and pinned
  across redirects, no cookies, no credentials, no proxy or `.netrc` from the
  environment, text types only, 1 MB cap applied before decoding: only
  `gzip`/`deflate` bodies are inflated, a bounded slice at a time, and any
  other encoding is refused; a page's `main`/`article` text first, without
  menus and frames, kept as a few chunk signals) and searches only when
  `OMNIGENT_FACTORY_WEB_SEARCH` names a keyless backend (its answer capped the
  same way). `team` reads the
  Room's hunches already in the ledger. Other kinds answer `422
  kind_not_supported` until secret-backed sources exist.
- **Agents** (`/v1/factory/agent/discovery/*`, scoped like every agent route):
  `POST …/query {from_session_id, source, tool, args}` and `GET …/sources`.
  The factory and the seat (`lead`, or the discovery seat `scout`) come from
  the caller's session labels; a body naming a factory is refused. The
  gateway refuses, before any call counts: a seat the source does not list
  (`403 role_not_allowed`), a paused source (`409 source_paused`) or one on
  hold (`409 source_failing`), a tool it does not offer or one that looks like
  a write (`403 tool_not_allowed`), bad arguments (`400 invalid_args`), a URL
  or repo outside the source (`400 url_refused`, `403 repo_not_allowed`); a
  spent daily cap answers `429 over_budget` (queries, sync requests and tests
  all count, per UTC day, atomically). A page the agent picked that cannot be
  read (a dead link, a host that does not resolve) answers `502 item_failed`
  and never counts against the source; the source failing itself (the search
  backend, GitHub) answers `502 source_failed`. Every error an agent gets is a
  fixed sentence: what a remote server sent (a content type, a redirect's
  host, an error text, a hold's reason) stays in the access log for people.
- **Storing and answering**: raw items go to the ledger, which alone writes
  signal text (redact, hash, injection score, clip). Each read writes one
  `source.query` (or `source.sync`) event: who, seat, tool, an argument
  digest, the search words, the URLs read, items, new, flagged, redactions,
  calls, cost, duration and outcome; every signal it stored keeps that
  event's id. The answer lists signal ids and labels; their excerpts come
  only inside the untrusted-text envelope (`envelope.format_untrusted`: a
  server header and rule, every outside line quoted between markers with a
  fresh nonce). A signal flagged `injection_suspect` is withheld from agents.
- **Sync** (`background/discovery_sync.py`, every minute): each source with
  `interval_min` above 0 whose interval passed, in factories that are not
  done or closed, as the server. Three failures in a row (a `web` sync fails
  when every URL it reads fails) put a source on hold and open a
  `source.failed` incident for the owners; `POST …/sources/{id}/resume` (an
  editor) lifts the hold.
- **People** (prefix `/v1/factory/factories/{fid}`; writes carry human
  origin): `POST /sources/{id}/test` (EDIT; one call, stores nothing; a held
  source may be tested, a paused one answers `409 source_paused`),
  `POST /sources/{id}/pause` (EDIT; a class-A definition version applied at
  once, which stops a running sync; its Undo is `POST …/definition/pin` of the
  version before, theirs for 10 minutes), `POST /sources/{id}/resume` (owners
  resume a paused source to the state it was paused from), `GET
  /sources/{id}/log` (READ; status and every `source.*` event, newest first).
  Any version that adds, removes, pauses or resumes a source also writes
  `source.added|removed|paused|resumed`.

### Readouts and the prediction ledger

Every bet is measured after its release, by the server, against the plan frozen
into its projects' charters (`discovery/readouts.py`, table
`factory_readouts`, `background/discovery_readouts.py` every 2 minutes); dark
like the rest of discovery.

- **Release** (`product.delivery.release`): `merged` (all the bet's projects
  are out: stage merged or done; from the newest merged PR), `github_release`
  or `github_deployment:<env>` (then a release, or a successful deployment to
  `<env>`, of their repos at or after that; read every 15 minutes with the
  owner's GitHub connection, read-only GraphQL), `manual` (only a person:
  `POST …/readouts/release {proposal | project, at?}`, EDIT; any mode may use
  it). One readout per readout day of the plan (7 and 28 by default), counted
  from the release; an eval-mode factory's readout day is a minute.
- **Methods**: `signal_trend` (the distinct accounts behind the opportunity's
  linked user signals in the window after the release against the same window
  before; counter-evidence, hunches, suspects and the server's own signals left
  out; the effect in the plan's unit: a relative change for `share`/`%`, a
  change per week for a count; n under the plan's `min_n`, default 5, is too
  little data). It counts who still asks, so its plan's metric is
  `signal_trend:O-n` and it predicts a drop: a bet filed otherwise is refused
  (422 `metric_not_measurable`, "predict a drop"), and a frozen plan that
  says otherwise reads out inconclusive. The window after the release counts
  only once it was observed: every source behind the opportunity read
  successfully (`source.sync` / `source.query`) after the window closed, and
  every user signal seen in it sorted (linked to an opportunity, or detached).
  Until then the readout stays scheduled and says why (`waiting`), for at most
  7 readout days; then it is inconclusive and names what was missing. An
  opportunity whose evidence comes from no source the server reads cannot be
  measured by a signal trend. `human_verdict`: a "Needs you" question "Did C-n
  help?" (no one-click answer: the answer, `helped` / `no_change` / `hurt`,
  needs a reason, in the cockpit).
  `before_after`, `adoption` and `controlled` are hooks
  (`readouts.register_method`): until a source measures them their readouts
  are inconclusive and say so. Guardrails no source measures are
  `unmeasured`; a person may report one broken.
- **Fixed rules** (Tier 0, `readouts.judge`): won (moved the promised way by
  at least the smallest predicted change: `low` going up, `high` going down,
  zero when the range spans zero) → keep; flat (less; a move the wrong way
  smaller than that is noise, not a loss) → iterate; lost (the wrong way by
  that much or more, or any guardrail broken) → roll back; inconclusive (too
  little data or a failed data check) → extend once, then iterate. Each
  readout says whether the effect fell inside the predicted range; a won or
  flat readout whose guardrails no source measured says "(guardrails not
  measured)".
- **Write-back**, through the ledger only: a `test_result` signal of the
  server's own source (verdict, method and the level that method can show:
  signal trend moderate, a person's verdict weak, before/after and adoption
  strong, controlled proven) linked to the opportunity, which turns measured.
  The meter counts a readout only through its verdict: lost drops it to
  weak, won raises it to that level. A `readout.written` event, a line in the
  Room's `#discovery` thread, and `[Factory readout · C-n · day 28 · flat]`
  to Scout when it is awake (seated, active and within today's budget).
- **Deciding** (`POST …/readouts/{id}/decide`, EDIT, human origin; `{id}` is
  the row id or `C-31-d28`): `{decision: keep|drop|iterate|extend, reason?,
  reviewer?}`, `{answer, reason}`, `{guardrails_broken: [...]}` (judged
  again: lost). Following the recommendation is an operator's; going against
  it is an owner's with a named reviewer and a reason, all shown on the
  readout. The last readout of a bet, and any lost one, opens a `readout`
  decision (assignee operators) whose one-click options follow the
  recommendation (Home's Needs you kind `readout`); "Decide otherwise…" opens
  the readout. Keep and drop close the opportunity (kept, dropped) and cancel
  later readouts; drop names the flag to turn off; iterate closes the bet and
  the opportunity falls back to its evidence; extend adds one readout at twice
  the day. `POST …/readouts/{id}/reopen` (EDIT within 10 minutes of the
  decision, an owner after that; human origin) takes a keep, drop or iterate
  back: the readout is written again (its question asked again), the later
  readouts it cancelled are back, and the opportunity is measured again.
  `readout.scheduled`, `readout.written`, `readout.decided`,
  `readout.reopened` events. A manual release's `note` is kept with it.
- **Reads** (READ): `GET …/readouts` (readouts, the bets `pending` with their
  band only, the `ledger`), `GET …/readouts/{id}`; `GET …/discovery` gains
  `readouts` and `ledger`.
- **The prediction ledger** (discovery family in `telemetry/metrics.py`, kept
  out of the Insights catalog): each bet's latest readout scores it: the share
  inside the predicted range, the win rate by the evidence level at filing (a
  level is a rate from 5 readouts, else "not enough data"), the yield per
  source in its evidence at filing, the hit rate by origin (discovered,
  requested, hunch; requested work without a plan is shipped, never
  measured), each with n and grouped by the definition version that ranked the
  bet (a filed bet snapshots its opportunity's score). The discovery guards
  are in the same family: `quote_failures` (every `*.quote_refused`: a bet,
  an opportunity or Scout's signal paraphrase), `privacy_events` (source reads
  refused for privacy: the gateway logs each as `privacy.source_refused`
  besides its access-log row, so the guard never scans the access log),
  `bet_acceptance`, `cost_per_accepted_bet` (the discovery seat's spend). The
  family is read only for its own metrics, and is unknown while the server
  does not run discovery.

### Scout (the discovery seat)

Scout (`examples/factory/factory-scout`, Claude Code, role `scout`) learns
what the product's users need and keeps the ranked opportunity list Atlas plans
from. It is dark like the rest of discovery, and its bundle holds no product
knowledge: it reads the profile, the strategy, `briefs.scout` and the signals.

- **The seat** (`discovery/seat.py`, `background/discovery_seat.py`, every
  minute): while the server env and the factory's `discovery.enabled` are on,
  the server seats one Scout (an in-process `POST /v1/sessions` of the
  built-in `factory-scout` as the factory's creator, on `discovery.seat.model`
  as its model override, the factory's default repo checked out read-only;
  the create carries no factory label and the server stamps them after, so no
  runner token can mint a seat). Off, it is stopped (`state=done`; its idle
  sandbox is released); on again, the same session is woken with a fresh
  brief. It counts against `factory_max_pods` (owners get one decision a day
  when it does not fit); failed creates back off and open a `discovery.seat`
  incident. With the server env off there is no loop; a pass at start stops
  leftover seats. Its heartbeat is off: the beats below wake it. A changed
  `discovery.seat.model` is switched on the live Scout the way a person's
  `/model` is (`PATCH /v1/sessions/{id}`); clearing it drops the override the
  server set. A session the server created but could not label is archived.
- **Tools** (runner, `factory_role: scout`): `sys_discovery_read`,
  `sys_discovery_query`, `sys_discovery_code`, `sys_discovery_opportunity`,
  the roster, its beacon, the rules, `sys_factory_ask`, `sys_factory_chat`
  (`#discovery`, and elsewhere only a reply where an `@scout` reached it;
  it mentions only `@atlas` and `@sentinel`, never a project lead) and
  `sys_factory_propose` (`kind=tuning` under `product`, `strategy` and
  `discovery.sources` only, an allowlist whatever the block, owners decide, 2
  a day). No messaging, PR, project, task or spawn tool, and no shell: the
  bundle has no terminal and switches Claude Code's Bash, WebSearch and
  WebFetch off (`executor.config.disallowed_tools`), and for role `scout` the
  guard is an allowlist (Claude Code's Read, Glob, Grep and the like, writes
  only to `~/factory/*.md`, and the Omnigent discovery and factory tools;
  every shell, terminal, web, browser or other MCP tool is denied). Leads get `sys_discovery_read` and
  `sys_discovery_opportunity(op="idea")`; nobody else gets a discovery tool,
  and a sub-agent running Scout's spec is an `ic`.
- **Agent routes** (`api/discovery_agent.py`, `discovery/agent.py`):
  `POST /v1/factory/agent/discovery/read` (Scout and the leads: `summary`,
  `profile`, `strategy`, `brief`, `sources`, `opportunities`, `opportunity`,
  `signals`, `unsorted`, `calibration`, `projects`, `metrics`; signal text only
  in the untrusted envelope, a suspect withheld), `…/code` (Scout: labels and
  links; a paraphrase is checked for quotes and instructions, a person's
  attach or "not about this" stands, a flagged signal is never attached) and
  `…/opportunity` (Scout: `create`, `rephrase`, `merge`, `split`, `bet_note`;
  everyone with the tool: `idea`, an `idea` signal of weight 0.1, 5 per agent
  a day; a flagged idea is never attached). Refused quotes are logged as
  `opportunity.quote_refused` and `signal.quote_refused`; any agent text on an
  opportunity (title, statement, alternatives, bet note, parts, reason) that
  reads like instructions is refused (`422 text_refused`, logged as
  `opportunity.text_refused`). People's corrections win: Scout may not
  rephrase what a person renamed, merge back a person's split, split what a
  person merged (30 days) or reshape a pinned opportunity (`409
  changed_by_a_person`). People can Undo Scout's opportunity edits for 10
  minutes, and an agent's acts are never calibration.
- **Room**: `#discovery` is Scout's thread (Atlas answers while no Scout is
  seated, and while it is over today's budget), listed only while discovery
  runs; `@scout` routes only then. While discovery is off, `#discovery` still
  names a project of that name. "I think … because …" and "we need …" there
  are team hunches too.
- **Beats** (`background/discovery_beats.py`, every minute, each a
  `discovery.*` event): `[Factory discovery · sweep · …]` to Scout per
  `discovery.cadence.sweep_h` when signals arrived (idle sweeps are skipped),
  `[Factory discovery · synthesis · …]` per `synthesis_h` (a seat's first
  ones come 2 h and 24 h after it is seated), queued research asks once Scout
  can take them, `[Factory discovery · ready to plan · …]` to Atlas a while
  after a synthesis or when capacity frees up (a project's work reaches
  merged or done, or it is cancelled: the slots of `caps.max_projects`)
  with validated opportunities pinned first, their titles and bet notes inside
  the untrusted envelope, test-first ones, goals without a metric, the mix
  gap, free capacity, at most `thresholds.bets_per_week` minus the week's
  bets, and a monthly `[Factory discovery · retro · …]` to Sentinel when
  something happened. Scout's spend today (the cost ledger, role `scout`)
  posts one line in `#discovery` at 80 % of `discovery.budgets.usd_per_day`
  and stops every beat, ask and Room wake at 100 % until the next UTC day
  (`discovery.budget_stop`); a budget of $0 means Scout is never woken.
- **Rollout**: the role, the tools, the guard and the bundle option are runner
  code. On a host from before this, Scout's guard does not know role `scout`
  and denies every tool call, so turn `OMNIGENT_FACTORY_DISCOVERY` on only once
  the hosts run a build with it. The lead and PL bundle changes are text only.

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
  in `#improvements`). The daily beat waits (`retro.skipped`, rechecked
  hourly) while the last 24 h had nothing to read: no work, incident, finding,
  closed PR, answered or escalated decision, exhausted loop, human feedback or
  human Room post. Owners can run it now: `POST …/retro`.
- **Eval mode** (root label `omnigent.factory.mode=eval`): decisions are
  answered with the recommended option after 2 minutes (`answered_by
  eval-auto`) and each project's PL is paused at READY. Decisions only an
  owner may approve stay open: access requests, tuning, rules and code
  proposals, owner-approval projects proposals and budget raises past the
  operator cap. The benchmark charters
  are in `examples/factory/benchmarks/`.

### Insights, Improvements and Settings (cockpit)

- **Insights** (`/factory/:id/insights`): KPI tiles (merged · acceptance,
  lead time, human wait, human load against the 30–50 band, $ per merged PR,
  incidents · MTTR, NEXT wakes), each with n, the change against the previous
  window and a day sparkline. Below n=3 a tile reads "n=2 — inconclusive" and
  shows no delta. Definition and platform version markers come from
  `GET …/versions`; pressing a definition marker measures only that version
  (`?def=`). Then Flow (stage funnel, cumulative flow, where the time went per
  project), Loops, Quality, People (per-person numbers for owners only), Cost,
  Reliability, Findings and retros, and Experiments, a group-by table
  (`?group=`) and Export (NDJSON or CSV from `GET …/export`, never
  transcripts). The Overview's health strip gets 24 h activity sparklines.
- **Improvements** (`/factory/:id/improvements?tab=`): On trial (the live
  scorecard from `GET …/proposals/{ref}/scorecard`: target and every guard,
  baseline vs trial with n; Keep now, Revert), Proposed (Approve trial, Edit,
  Reject; `awaiting_cosign` waits for the peer lead), History (every C-n),
  Rules, Definition (versions, diff, Revert to vN, bundle drift from
  `GET …/bundles` with Relaunch) and Findings ("Is this right?" calibration,
  Propose change). Code proposals show "Tier 2: off" with the missing
  prerequisite, **Copy as issue** and **Dismiss** (owners; `POST …/reject`
  rejects a recorded code change that has no decision). `/improvements/:ref`
  is a change's page.
  Edit applies the edited change as the owner's own trial `C-n` and rejects
  the original with a note.
- **Propose change** (findings, Insights tiles, incidents): owners apply it
  at once (`PUT …/definition`); operators file it for the owners with
  `POST …/definition/proposals` (`{kind, changes, rationale, evidence,
  expected_effect?}`, EDIT, human origin), which opens an owners decision;
  approving it runs it as a trial.
- **Settings** (`/factory/:id/settings/:section`): People, Autonomy (project
  autonomy and engine mode), Budgets & loops, Alerts (detector thresholds,
  decision escalation, the Slack webhook, set-only through
  `PUT …/alerts/slack`, and the Slack app for DMs), Notifications (each
  person's Slack DMs, see Slack DMs), Rules and Definition (history, bundles, "Run retro
  now", heartbeats, mail, previews, models). Every save is one
  `PUT …/definition` with `base_version` (a newer version answers 409
  `stale_definition`): an applied human `C-n` in History that one click
  reverts. Viewers and operators see the values read-only.
- **Feedback widgets**: 1–5 stars with reason tags on a PR review (sent with
  Approve or Request changes, or alone once merged or closed), 👍/👎 under a
  demo, "Unnecessary?" on an agent's decision, the required closure-reason
  picker on a closed PR's decision, a wrap-up rating on a finished project,
  "Is this right?" on finding and retro labels, and **Report a problem** (an
  incident or finding as you, `POST …/issues`) on agents, PRs and projects.
  Viewers may give feedback; it is not a lever.

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
- **Stalls read the task's people** (`task_pulse.py`), not only task events:
  - An open **blocking** question a person owes one of the task's sessions,
    one its PL asked about this task or about no task in particular (one
    naming another task, e.g. "T-9", does not count), or one whose subject
    is the task, holds the implement loop: no stall, the cockpit shows
    "waiting on D-n" (each ref links to its decision), and the no-progress
    clock restarts when the last one is answered. While the IC lives the
    held time is not charged to `wall_h`; with nobody working on the task it
    is, so a question cannot hide a dead IC for good.
  - While the task's IC lives, its open sessions' activity is progress:
    their newest item (IC, reviewer and demo turns, tool calls, the PL's
    messages to them), a turn running now (until it has been silent for
    `supervisor.stuck_s`), and, only once the clock runs out, a PL tool call
    that names the task (its ref, branch, or key). Closing a session is not
    activity, and the PL's replies never count (they are mostly status
    chatter). With the IC gone, failed or never bound, only task events and
    questions count.
  - The stall's decision says why in one plain line at the end of its
    question ("The IC worked 3 minutes ago; it is running pnpm build.") and
    recommends **Keep waiting** while the IC is alive, or **Switch vendor**
    when it is gone, failed or erroring, has been silent mid-turn past the
    stuck time, or produced nothing since the last Keep waiting. The IC's
    last error is quoted with secrets redacted. A decision re-opened later
    (after Pause project) is worded as of then.
  - A no-progress stall runs again by itself only on real work (a turn
    running now while the IC lives, or a task event) or when the task now
    waits on a person: a ping and a one-line reply do not reopen it. Each
    time it does, the try's next no-progress stall waits twice as long (up
    to 8x `no_progress_s`; a new try starts over).
  - After **Keep waiting** (it doubles `wall_h`) the no-progress rule rests
    on that try: it asks again only at the new wall budget, or sooner if the
    IC, once alive, is gone or failed, or the worker reports FAIL or BLOCKED.
    Keep waiting on a stall whose IC was already dead asks again after
    `no_progress_s` unless an IC starts, and its option says so.
- **Claims** (`loops.claim_status`, judged on the fresh gates and stored on
  `pr.claim`): `held` (ready), `refuted` (a failed gate, a draft, a moved head,
  a PR not open; or Sentinel `not_verified`) or `early` (only pending gates:
  CI running, mergeable computing, no review, verify or demo on the head yet).
  Only refuted claims count toward `mismatches` (and `sys_factory_pr` answers
  `claim_mismatch: true` only for them); an early one uses no iteration,
  raises no `claim.mismatch` and mails the PL `[Factory loop · WAIT · …]` once
  per head. The claim loop then waits on that head: `pr.ready` there holds the
  claim, a gate failing there (`gate.changed`) refutes it after all (counted,
  `claim.mismatch`), a new head or a closed PR lapses it, and a wait past
  `loops.claim.wait_h` (2 h) stalls the loop (`loop.stalled`, a `Keep waiting`
  decision that raises `wait_h`).
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
  `git push origin HEAD`, deletes, tags and `main`/`master`/`staging` (and the
  rest of `DEFAULT_PROTECTED_BRANCHES`) are denied, and so is git config that
  could redirect an explicit push (see Invariants); any push or PR creation by
  a lead, reviewer, planner or the demo agent; writes to repos outside the
  member's project repos, to the protected repos of the factory's product
  profile (`product.delivery.protected_repos`) unless a human made one a
  project's repo, and to the always-deny repos of
  `OMNIGENT_FACTORY_ALWAYS_DENY_REPOS`;
  the `factory:allow-protected` label; every PR write but `gh pr comment` by
  the demo agent and every PR or issue write by the planner; and env lifecycle
  (`tilt up|down|ci`, `pnpm factory:up|down|preview` or
  `scripts/factory.ts up|down|preview`, `kubectl delete ns`) for anyone but
  `env_keeper` (and its PL, whose guard its sub-agents also pass).
- **Humans merge.** No agent merges, on any repo. A PR is done
  for the factory when it is ready for human merge (not a draft, CI green, an
  APPROVE review marker and a PASS verify marker for the head). The PL posts
  `READY FOR HUMAN MERGE <url>` to its lead, Sentinel verifies the claim, and
  Atlas lists it for the human.
- **Auto-merge targets (owners only).** An owner may name repo and base pairs
  in Steer › Product profile › Repo and delivery (`product.delivery.auto_merge`,
  e.g. `[{"repo": "sample-org/sample-app", "base": "staging"}]`; names only,
  class B, a person's edit, never an agent's proposal). Steer warns when a
  target is the repo's default branch. The server's auto-merger
  (`server/factory/background/auto_merger.py`) then squash-merges a factory PR
  into a target, with the owner's GitHub connection, only when
  - its head is a factory branch of its project (`factory/<slug>/<task>`) and
    the project is active; the factory is not paused and the project's lead is
    not paused (the owner's brakes hold merges too);
  - the factory marks it ready (the merge ask's condition: every gate passed on
    its head, no change request on it);
  - GitHub, read just before, reports the same head in the same repository
    (never a fork), open, not a draft, `MERGEABLE`, no change request, nothing
    changed since the poller's last read (else the poller reads it again
    first), every check on the head completed as success, skipped or neutral
    (optional checks too), and the review and verify markers that pass the
    head written by a repo owner, member or collaborator, or by the factory
    owner's login the agents act as (an outsider's marker never decides);
  - the stored row, the targets and the brakes, read again right before the
    merge, still allow it.

  The merge names that head as the expected one, so a later push is refused.
  It records `pr.auto_merged` (the Room shows it in the PR's and the project's
  threads), and the poller's merged flow runs as for any merge. Those PRs leave
  Needs you while the merger runs (`OMNIGENT_FACTORY_AUTO_MERGE_S > 0`): they
  read "merges automatically when checks pass". Nothing waits silently: a
  GitHub error backs the PR off (5 min, doubling to an hour) and raises
  `pr.auto_merge_failed`; a ready PR that does not merge for a reason a person
  must fix (a failing check, no checks, a fork's head, an untrusted marker, a
  change request, GitHub not connected) at once, or for any reason for 30 min,
  raises `pr.auto_merge_waiting`, which replaces the "merges automatically"
  line with a Needs you row. Both are owners' incidents; they clear when the
  PR merges or closes, leaves the targets, is no longer ready or gets a new
  head. The PL's charter says "PRs into `<repo>#<base>` are merged by the
  factory's server when ready; never merge yourself". A version stores the
  targets under their own top-level key (`auto_merge_targets`), so a server
  from before them drops only the targets on a rollback and keeps the rest of
  the profile (`protected_repos` included).
- **Autonomy and auto-merge per subtree (owners only).** An owner scopes
  either setting to one stored node of the product tree (a goal `G-n`, a need
  `O-n`, a feature `I-n` or a project slug) with the node's "Autonomy &
  merge" verb (`u`; preview, apply as a definition version, Undo for 10 min;
  `server/factory/subtrees.py`). `product.delivery.auto_merge_scopes`
  `[{ref, repo, base}]` makes the server merge the ready PRs of every project
  under the node into `repo#base` (added to the factory-wide targets there);
  `{ref, merge: false}` keeps them merged by people, whatever an ancestor or
  the factory-wide targets say. `autonomy.scopes` `[{ref, level}]` sets the
  autonomy level for proposals about the projects under it. The nearest
  scoped ancestor decides, the node's own entry first; a project's ancestors
  are read the way the node tree files them (its home feature, that
  feature's need, the need's goal; a project with no home feature sits under
  the opportunity of the proposal that created it), so a project filed under
  the node later is covered too. A new project an agent proposes is decided
  where applying files it: in the feature its plan extends
  (`analysis.initiative.id`), else under the proposal's opportunity, so a
  create that only cites the ask that started a feature is not decided at
  that feature's level. A scope always names its base: the verb takes the node's
  projects' one base (or the factory's) but never `main`, `master` or a
  protected branch unless an owner names it (`args.base`); the preview lists
  what it covers now, its first effect and what it does not touch ("main
  stays merged by people (… is outside this subtree)"). Both lists are class
  B and in `AGENT_NEVER`: no agent proposes them, so no agent adds or widens a
  scope. The merger records the scope it used (`pr.auto_merged` `data.scope`);
  node pages show "auto-merge → `<base>` · this subtree / from `<node>`" (and
  the autonomy level where it differs from the factory's) on the node and
  everything under it. A version stores the lists under their own top-level
  keys (`auto_merge_scopes`, `autonomy_scopes`): a server from before them
  drops only them on a rollback. Nothing merges by a scope then, but the
  rollback widens where a scope was stricter: a `merge: false` scope is
  dropped, so the factory-wide targets merge the PRs it kept for people, and
  ops under a scope stricter than `autonomy.level` are decided at the looser
  factory level.
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
| No agent merges (any repo, a test base included); only the server merges, into the owners' auto-merge targets. | The merger reads its targets only from `product.delivery.auto_merge` and, per subtree, `product.delivery.auto_merge_scopes`, which only an owner's edit changes (`AGENT_NEVER`: no agent proposal names it, and approving one never applies it). The guard denies raw `gh pr merge`, MCP merges, merge-capable `gh api`/GraphQL calls and every `omnigent.factory_merge` run without `--dry-run` (`python -m`, a script path, `python -c` code, a Python heredoc), for every role. It also denies any `OMNIGENT_FACTORY_*=` assignment, `env OMNIGENT_FACTORY_…` or `export OMNIGENT_FACTORY_…`, which closes the inline `OMNIGENT_FACTORY_MERGE_REPOS=… python -m omnigent.factory_merge` override. `OMNIGENT_FACTORY_MERGE_REPOS` stays empty on the server. |
| Agents push only `factory/**` (the `smith` profile: `factory/self/**`). | `push_ref_prefixes` in every bundle's `factory_guard`: each explicit refspec destination must be `refs/heads/<prefix>**`. A bare `git push` and `git push <remote> HEAD` are denied (their destination depends on config and the checked-out branch, which the guard cannot see), so the IC prompts always push `git push -u origin factory/<slug>/<task>`. An explicit `<src>` still follows config: with `push.default=upstream` it lands on the branch's upstream, and `remote.<r>.push`, `pushurl` or `url.<x>.pushInsteadOf` repoint it. So the guard also denies `-c`/`--config-env` on a push, any `GIT_CONFIG_*` assignment, `git config` writes to `push.*`, `remote.<r>.url\|pushurl\|push\|mirror`, `url.*`, `branch.*`, `alias.*` and `include.*`, and `git remote add\|set-url\|rename`; the IC worktree is created with `--no-track`, so its branch has no upstream to fall back to. Deletes (`:<b>`, `--delete`, `--prune`), tags, `--all`/`--mirror` and API ref writes outside the prefix are denied too. Protected branches are `main`, `master` and `staging` plus the environment profile's `protected_branches`, which the server passes as `factory_protected_branches`. |
| No writes to the always-deny repos; none to a protected repo of the product profile unless a human made it a project's repo (or under the `smith` profile). | The server passes the profile's `product.delivery.protected_repos` as `factory_protected_repos`; `OMNIGENT_FACTORY_ALWAYS_DENY_REPOS` (read at boot) is denied whatever a spec passes; an explicit spec `deny_repos` binds too. A project's repo is lifted from the protected repos and the default `deny_repos` (empty) for the members that write it (next row). The `smith` profile (no `write_repos` of its own: as a member it writes its project's repo, the deployment's omnigent fork; `push_ref_prefixes: [factory/self/]`, never merges) is registered for Tier 2 and unused while Tier 2 is off. |
| A member writes only the repos of its factory projects: a PL and its sub-agents their own project's repo, a lead (and the planner) every project's. Pushes, `gh pr create/edit/comment/review/…`, `gh issue` writes, `gh api` writes and `GH_REPO` all check the same set. | The server resolves the set whenever it builds a member's policy engine (`server/factory/guard_repos.py`, registered in `runtime/policies/builder.SERVER_POLICY_ARGUMENTS`) and passes it as `factory_repos` to `factory_role_guard` and `factory_invariants`. It replaces the bundle's `write_repos` (bundles pass none; outside a factory the guard writes only the repos a spec names). It reads only the server-stamped membership labels (`id`, `role`, `project`) and the factory's project rows, never tool input, `session_state`, charters or other labels; a row reconcile backfilled from a PL's sandbox (its repo came from a lead's spawn) does not count, nor do cancelled or merged-away projects. An agent's project proposal on a repo outside the same set (plus the default repo) needs the owner, so no agent can grow it. Rows are read on every build, so a project added later extends the set at once. A store error, or a sub-agent whose root cannot be loaded, leaves the member no writable repo. The runner-side gate keeps the bundle defaults; no factory bundle declares an MCP server, so every GitHub write is a shell command the server decides. |
| A write target the guard cannot read is denied, never assumed to be the project's. | `-R`/`--repo`, `GH_REPO`, PR and issue URL arguments and `git push` URLs are read the way gh and git read them (host in any case, `www.` or another github.com subdomain, a root dot, a port, a token in the URL, `-R=x`). A value that names no GitHub `owner/repo` (a shell expansion such as `-R $R` or `git push $URL`, another host, `.`/`..` segments, percent-escaped separators) is denied, as is a push refspec the shell fills in (`factory/$X`), a `gh api` write whose repo is not spelled out, `gh api -X=PUT` (read as PUT), and `gh repo create`, `fork` and `sync <repo>`. Still open, as on staging: `gh api graphql` mutations by node id, a fresh `git clone` followed by `git push origin`, and calls through `curl` or a script. |
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

## Round 3 operations runbook

### Deploy waves

Each wave: merge the listed packages into the fork's `staging`, bump `newTag`
to `staging-<sha7>` in an infra PR, then run the live check on
the deployment's server.

| Wave | Packages | Engine mode | Live check |
|---|---|---|---|
| 0 | base-branch and S3 PRs; infra knobs `OMNIGENT_FACTORY_ARTIFACT_LOCATION`, `OMNIGENT_FACTORY_DEFAULT_BASE`, `OMNIGENT_FACTORY_DEMO_MIRROR_REPO`; the `factory-demos` repo | — | `GET /v1/factory/preflight` shows `default_base=sample-org/sample-app#develop`; the server log names the S3 demo location |
| 1 | base-branch lane, schema, Tier 0, telemetry core | observe | PRs open against `develop` with the Base gate green; base-env shows `develop@<sha>`; red-team pushes are denied; spans and the cost ledger fill |
| 2 | people, projects through chat, the Room, UI shell | observe | invites work; chat creates projects; the server spawns PLs; the project page shows demos |
| 3 | work graph, loop engine, ops, Room and graph UI, ops UI | **assist** | the work graph, NEXT hints, loop cards, fleet "now" and interventions work |
| 4 | learning and Tier 1, learning UI and the multi-user E2E | assist → **enforce** after one clean run | a Tier-1 trial is kept or reverted; Insights is complete; the multi-user E2E is green |

### Rollback

- **Engine mode first.** An owner sets Settings ▸ Autonomy ▸ Engine mode back
  to `observe` (a reversible `C-n`): NEXT and READY hints stop, loop budgets
  stop refusing starts, the dispatch guard does nothing (no warning, no
  deny), a stuck loop only opens an issue, and spend only posts a notice,
  never auto-pauses. Binder, spans, loop state and detectors keep measuring.
  Use `assist` to keep hints and warnings but drop enforcement.
- **A bad change.** Improvements ▸ the change ▸ **Revert**, or Definition ▸
  **Revert to vN** to put any stored version back; members get the diff.
- **Everything.** `OMNIGENT_FACTORY_ENABLED=0` turns the factory built-ins
  and supervisor off.
- **The database is forward-only.** Never roll the image back below the
  round-3 migration `fz1a2b3c4d5e`.

### Live acceptance run

Three browser profiles on the deployment's server: **Lena** (creator),
**Mia** (operator, a second GitHub account admitted by OIDC; check
`GET /v1/info` and the allowed domains first) and **Ops** (viewer). Record
the results in `notes/ROUND3-LIVE-RUN.md`.

1. **Describe it (Lena).** New factory ▸ Describe it: name "Atlas factory",
   `sample-org/sample-app` # `develop`, live env on, autonomy *requested*, engine
   *observe*, brief "Show the deployed versions in Settings: an API version
   endpoint, a DB version endpoint, and a Settings row showing both." Expect
   the Room on `#factory` with the brief as message #1 and base-env reaching
   `● develop@<sha> = head` in about 15 min.
2. **Agents split the work.** Atlas posts `C-1` (db-version, api-version,
   settings-versions depending on both); the creates auto-apply with Undo and
   two PLs spawn without an Atlas turn; settings-versions waits.
3. **A project by chat (Lena).** "also add a small project: a docs note in
   docs/ about the version endpoints" gives an auto-applied `C-2` and a
   `#docs-version` thread. `/project new` opens Add project prefilled with
   `sample-app#develop`; cancel it.
4. **Invite (Lena).** Share: Mia as Operator, Ops as Viewer; both admitted.
5. **Join (Mia).** Every project visible, presence shows both, the composer
   says "posting as Mia · operator".
6. **Mini chat (Mia).** In `#db-version`: "@pl:db-version use knex_migrations
   as the source of truth"; the PL answers in the thread within one turn; a
   question it asks is assigned to Mia.
7. **Viewer (Ops).** Read-only banner; composer, Apply, Answer, Pause, Share
   and every Settings and Improvements lever disabled with tooltips; Request
   access opens a decision Lena declines.
8. **Work graph and loops.** Settings ▸ Autonomy ▸ Engine mode = assist.
   Tasks, waves and the critical path on the project page; NEXT wakes in
   Insights; PRs on `develop` with Base ✓; previews and demos on the PR and
   project pages; the GitHub demo comment shows the mirrored GIF.
9. **Something goes wrong (Mia).** An impossible acceptance exhausts
   `implement 3/3`: incident, loop decision; Agent page ▸ Interrupt, then
   Switch to `ic_codex`; attempt 4 runs on codex; the Timeline shows r0–r3.
10. **Merge into develop (the user, on GitHub).** `base.moved` within
    5 min; base-env redeploys; settings-versions is released once both
    dependencies merged.
11. **Improve.** Settings ▸ Definition ▸ **Run retro now**: a digest in
    `#improvements` with at least 2 findings; a class-A tuning change goes on
    trial after Atlas co-signs (Improvements shows its scorecard); a class-B
    rule waits for Lena, who approves it (PLs get RULES UPDATE; the next card
    carries the rule) and reverts the class-A trial in one click.
12. **Settings (Lena).** Budgets & loops ▸ implement attempts 3 → 4 ▸ Save:
    an applied human `C-n` in Improvements ▸ History; revert it from its page.
13. **Insights (Ops).** Every tile shows n; version markers appear;
    per-person numbers are hidden from the viewer.
14. **Safety checks** (orchestrator, on an IC pod; each denied and logged):
    `git push origin HEAD:develop`, a bare `git push`, the merge gate with
    and without `OMNIGENT_FACTORY_MERGE_REPOS`, the runner bearer on
    `POST /v1/factory/factories/{fid}/proposals/<n>/apply` (403
    `human_only`), and `sys_session_create(config_path=…)` from an IC (403).
    The factory merges nothing.
15. **Record**: human prompts per day per person, interventions per merged
    PR, $ per merged PR, iterations to converge per loop kind, first-pass
    yield per gate, decision latency, spawn latency, base-move → redeploy time
    and the NEXT share of wakes (Insights ▸ Export has the raw data).

The automated version of steps 1, 4–7 runs as
`tests/e2e_ui/factory_round3/test_factory_room_multi_user.py` (a multi-user
header-auth server; GitHub and the agents are stubbed: the test posts Atlas's
reply and proposal through the agent routes as the root's owner).

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
| Member repos (the guards' `factory_repos`) | `omnigent/server/factory/guard_repos.py`, `omnigent/runtime/policies/builder.py` | `tests/server/factory/test_guard_repos.py` |
| Tier-0 invariants | `omnigent/policies/builtins/factory_invariants.py`, `omnigent/server/factory/origin.py`, `custom_bundle.py`, `omnigent/server/bundles.py` | `tests/policies/test_factory_invariants.py`, `tests/server/factory/test_human_origin.py`, `tests/server/test_bundle_validation_factory.py` |
| Org chart and badges | `omnigent/server/factory/org_chart.py`, `web/src/lib/factory.ts` | `tests/server/routes/test_child_sessions_include_factory.py`, web tests |
| Base lane and demo mirror | `omnigent/server/factory/base.py`, `demo_mirror.py`, `omnigent/factory_gates.py` (`base` gate) | `tests/server/factory/test_base_watcher.py`, `test_demo_mirror.py`, `tests/test_factory_gates.py` |
| Work graph | `omnigent/server/factory/tasks.py`, `graph.py`, `binder.py`, `api/tasks.py`, `api/graph.py` | `tests/server/factory/test_graph.py`, `test_binder.py`, `test_tasks_routes.py`, `test_graph_api.py` |
| Loop engine | `omnigent/server/factory/loops.py`, `loop_escalation.py`, `pipeline.py`, `budgets.py`, `api/loops.py` | `tests/server/factory/test_loops.py`, `test_pipeline.py`, `test_budgets.py` |
| Telemetry | `omnigent/server/factory/telemetry/`, `api/telemetry_read.py`, `background/prune.py` | `tests/server/factory/test_telemetry_*.py` (replays `fixtures/live_run_2026_10_26.json`), `test_prune.py` |
| Slack DMs | `omnigent/server/factory/slackdm/`, `background/slack_dm.py`, `api/slack_dm.py`, `web/src/components/factory/settings/{NotificationsSettings,SlackAppCard,TeamReachability}.tsx` | `tests/server/factory/test_slack_dm_*.py` (a fake Slack behind `httpx.MockTransport`, a fake clock), web `notify*.test.tsx` |
| Bundles | `examples/factory/` | `tests/examples/`, `tests/server/factory/test_factory_agents_listed.py` |
| Demo harness | `examples/factory/factory-pl/agents/demo/skills/factory-demo/harness/` | `tests/examples/test_factory_demo_harness.py` (runs `factory_demo_harness.test.mjs` under `node --test`); `node run.mjs --selftest` records real demos of a local fixture |
| Discovery ledger | `omnigent/server/factory/discovery/`, `api/discovery.py`, `background/discovery_ledger.py`, `omnigent/stores/factory_store/discovery.py`, `sqlalchemy_discovery.py` | `tests/server/factory/test_{ledger_append_only,redact,evidence_meter,rubric_example,opportunity_status,quote_check,discovery_routes,preview_rerank,team_signals,discovery_ledger_upkeep}.py`, `tests/stores/test_factory_store_discovery.py` |
| Source gateway | `omnigent/server/factory/discovery/{gateway,sources,web_io}.py`, `discovery/kinds/`, `api/discovery_sources.py`, `background/discovery_sync.py`, `envelope.py` (`format_untrusted`) | `tests/server/factory/test_{gateway_scope,source_repo,source_web,source_pause_undo,injection_envelope}.py` |
| Readouts and the prediction ledger | `omnigent/server/factory/discovery/readouts.py`, `api/discovery_readouts.py`, `background/discovery_readouts.py`, the discovery family in `telemetry/metrics.py`, discovery trials in `evaluate.py` and `definition.py` (`guard_set`, `trial_hours`) | `tests/server/factory/test_{readouts,release_detection,prediction_ledger,discovery_trials}.py`, `tests/db/test_migration_factory_readouts.py` |
| Learning reads | `omnigent/server/factory/learn_view.py`, `human_tuning.py`, `api/learn.py` | `tests/server/factory/test_learn_routes.py` |
| Learning UI | `web/src/components/factory/{insights,improvements,settings,feedback}/`, `web/src/pages/factory/{Insights,Improvements,Proposal,Settings}Page.tsx` | `web/src/pages/factory/LearnPages.test.tsx` and the component tests; stories under Factory/Insights, Improvements, Settings and Feedback |
| End to end | — | `tests/integration/test_factory_tree.py` (live server and runner, mock LLM); `tests/e2e_ui/factory_round3/` (multi-user browser run) |
