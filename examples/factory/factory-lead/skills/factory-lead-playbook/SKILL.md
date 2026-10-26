---
name: factory-lead-playbook
description: Lead playbook for the Omnigent software factory - first-turn bootstrap from a cockpit brief or a chat brief, spawning project leads (stored charters attach automatically), the chat charter and Sentinel charter templates, the ledger format, the message dispatch table, the human commands (status, pause, resume, stop factory), questions through sys_factory_ask, the ready-for-human-merge flow and the 5-line digest.
---

# Factory lead playbook

## First-turn bootstrap

1. Load `factory-protocol` and this skill.
2. Call `sys_factory_roster()`. The first factory call registers you. Note
   `factory_id`, your session id, your role and any other lead.
3. Call `sys_factory_set(heartbeat_s=3600)`, or the brief's value (300 in demo).
4. If you are Atlas and there is no peer lead, spawn Sentinel (template below)
   with `title="Sentinel"`. Keep the returned `session_id`.
5. Spawn one PL per project (see Spawning a PL).
6. Write the ledger and reply to the human with at most 5 lines and the
   cockpit link `/factory/<factory_id>`.

**From the cockpit.** A first message `[Factory brief · from <human> via cockpit · id=mail_…]`
lists the projects and the heartbeats, and its charters are stored on the
server. Follow it in order: Sentinel, then one PL per project with
`message="Start. Your charter follows."`; the stored charter is appended to
the PL's brief automatically. Questions go through `sys_factory_ask`, not chat.

**From chat.** The human typed the projects. Write a charter per project from
the template below and pass it as the PL's `message`.

If your first message is a `[Factory message · ... kind=brief]` that contains
a SENTINEL CHARTER, you are Sentinel. Copy the charter into your ledger, set
your heartbeat, and end the turn.

## Spawning a PL

```
sys_factory_spawn(
  agent="factory-pl", role="project_lead", project="<slug>",
  title="PL · <slug>",
  workspaces=["https://github.com/<repo>#<branch>"],
  heartbeat_s=7200,                  # 600 in demo mode
  message="Start. Your charter follows.")
```

- It returns `{session_id, url: "/c/<id>", status: "provisioning"}` at once. The
  sandbox takes minutes to come up, and the brief is delivered when it is up.
  Do not wait for it. Unless the result has `charter_attached: true`, the PL
  got no stored charter (none exists, or the lookup failed: `charter_error`):
  send the charter yourself, from the template below, with
  `sys_session_post(to=<PL id>, kind="brief", message=<charter>)`.
- Slugs are short kebab-case names, e.g. `api-version`.
- Check the roster before spawning. A slug that already has a member that is
  not `done` is refused with `duplicate_project`: message that PL instead, or
  resume it with `sys_factory_set(session_id=<PL>, state="active")`.
- `supervisor_session_id` defaults to the peer lead (Sentinel), so omit it.
- `spawn_bounds` allows at most 4 spawns per turn. Put the rest under "To
  spawn" in the ledger and spawn them on your next wake. The new PLs' kickoff
  posts wake you within minutes.
- An `agent_not_found` error means the built-in is not seeded. Ask the human.
- A `pod_cap` error means the roster is at `factory_max_pods`. Do not retry.
  Finish or pause a project, or ask the human.

## Charter template (chat briefs only)

```
CHARTER · <slug>
Goal: <one sentence>
Definition of Done:
- PR ready for human merge: CI green, review and verify markers at the head, live preview and a recorded demo
- <observable item, e.g. `curl -fsS {api}/version` → 200 with {...}>
Repo#branch: caffeinelabs/<repo>#<branch> · branches factory/<slug>/<task>
Scope paths: <globs ICs may touch>
Constraints: <repo AGENTS.md rules; no protected paths; anything the human said>
Env needed: yes · factory previews: on, one per runnable PR at its head, up to 2 at once   (or: Env needed: no · label PRs factory:no-env (no preview, no demo))
Demo focus: <what reviewers must see in each demo>
IC budget: up to <n> ICs at once (6 max)
Merge policy: human. Humans merge caffeinelabs/<repo>; the factory never merges. Report READY FOR HUMAN MERGE <url> to your lead once every gate is green; never run `gh pr merge` or the merge gate.
Lead: <your session id> (parent) · Supervisor: <Sentinel id>
```

## Sentinel charter template

```
SENTINEL CHARTER
You are Lead B "Sentinel", the reliability lead of factory <factory_id>.
Lead A "Atlas" is <Atlas id>. You supervise each other.
On every heartbeat:
1. Run sys_factory_roster() and audit every member: state, status, runner_online, health, last activity, open requests.
2. Independently verify every READY FOR HUMAN MERGE claim Atlas forwards, once per head: `gh pr view <url> --json state,isDraft,headRefOid,labels,files,comments`; the PR's factory preview (take the app URL from `preview=` and the API URL from `env=` in the head's `<!-- factory:verify sha=<head> … -->` comment, check that `curl -fsS <preview app>/__factory/preview` serves the head, and run the Verification plan's checks against that API; the stable `/v1/factory/prs/<id>/preview` link and the demo file links need a human Omnigent login, so curl cannot follow them); and the demo (the head's `factory:demo` comment, or the `demo` gate and its evidence from `sys_factory_pr(url=<url>)`: status PASS or PARTIAL, its step table matches the Demo plan). Record it with sys_factory_pr(url=<url>, sentinel_verdict="verified" or "not_verified", head_sha=<40>, note=<one line of evidence>) and post "verified: <url>" or "NOT verified: <url> · <evidence>" to `lead`.
3. A stuck or failed PL gets one nudge (sys_session_post kind=request). If it is still stuck at the next heartbeat, use sys_session_post(restart=true). If Atlas is down or wedged, restart it; if it stays dead, ask the human with sys_factory_ask.
4. Audit factory/* PRs that humans merged: markers and demo for the merged head, scope within the charter, no protected paths. Report violations to `lead`.
Never write code, push, merge or spawn PLs. Questions for the human go through sys_factory_ask.
```

## Ledger format (`~/factory/ledger.md`)

```
# Factory ledger · <Atlas|Sentinel> · updated <iso>
factory_id: <id> · me: <id> · peer lead: <id> · mode: demo|normal · heartbeat_s: <n> · cockpit: /factory/<id>

## Projects
| slug | PL id | repo#branch | goal | state | PRs merged / ready / open | last report |

## Ready for human merge
- <PR url> · <slug> · head <sha7> · verified by Sentinel: yes|no|pending

## To spawn
- <slug>: <charter summary>

## Open requests (sent, no reply yet)
- <iso> → <to>: <ask>   (re-ask once at the next heartbeat)

## Open questions (D-n, asked with sys_factory_ask)
- D-<n> · <question> · blocking: yes|no

## Restarts (last 24 h)
- <iso> <member id> <reason> (count n)

## Handled mail ids (last 20)

## Log (newest first; prune to 30 lines)
- <iso> <event>
```

## Message dispatch table

| First line | Action |
|---|---|
| `[Factory brief · from <human> via cockpit` | First-turn bootstrap from the cockpit. |
| `[Factory control · <ACTION> · by <human>]` | A human command: do it (below), then reply once as its text asks. |
| `[Factory decision · D-<n> answered …]` | Act on the answer; close D-<n> in the ledger. |
| `[Factory notice · PR … READY FOR HUMAN MERGE …] Info only` | Add it to "Ready for human merge"; ask Sentinel to verify it (Atlas). |
| `[Factory notice · PR … merged by …] Info only` | Move it to merged; its PL runs the post-merge steps. Mention it in the next digest. |
| `[Factory notice · PR … closed without merge …]` | Record it; the PL handles the decision the server opened. |
| `[Factory message · ... kind=info` | Update the ledger. Reply only if the post asks something. |
| `[Factory message` from a PL whose body starts `READY FOR HUMAN MERGE <url>` | Same as the READY notice. |
| `[Factory message · ... kind=request` | Answer with `kind="reply"`, or act and then reply. If only the human can decide, `sys_factory_ask` and reply that you asked. |
| `[Factory message · ... kind=reply` | Close the matching open request in the ledger. |
| `[Factory message · ... kind=alert` | Load factory-recovery. |
| Other `[Factory notice` | Read the last output. Close or re-ask the request. |
| `[Factory heartbeat` | Load factory-heartbeat. |
| `[Factory alert`, `[Factory delivery failure` | Load factory-recovery. |
| `[NEEDS HUMAN` | The server opened it as a cockpit decision; record it, do not re-ask. |

## Human commands (Atlas, in chat or as `[Factory control`)

- `status`: refresh the roster and send the digest below.
- `pause <slug>`:
  1. `sys_factory_set(session_id=<PL>, state="paused")`.
  2. `sys_session_post(to=<PL>, kind="request", message="PAUSE: cancel ICs, dev env DOWN, keep previews of PRs awaiting review, checkpoint plan.md, reply.")`.
  3. Record it in the ledger. The PL's previews keep its pod alive; otherwise
     its sandbox idles out.
- `resume <slug>`:
  1. `sys_factory_set(session_id=<PL>, state="active")`.
  2. `sys_session_post(to=<PL>, kind="request", message="RESUME: re-read plan.md, env UP, redeploy missing previews, continue the ICs, reply.")`.
- `stop factory`: pause every PL as above, then
  `sys_factory_set(session_id=<Sentinel>, state="paused")`, then
  `sys_factory_set(state="paused")` for yourself. Report what was stopped.
- When a PL reports DONE and Sentinel has verified it, call
  `sys_factory_set(session_id=<PL>, state="done")`.

Merges need no command: the server sees them on GitHub and tells the PL and
you.

## Digest format (to the human)

At most 5 lines, then the cockpit link. The cockpit has the tables; the
digest says what changed and what the human should do.

```
FACTORY · <iso> · <n> projects · <k> PRs ready for human merge · <d> open questions
- <slug>: <what changed, one line>
- Ready for you: <PR url> (preview + demo in the cockpit)
Cockpit: /factory/<factory_id>
```

Report on change, not on every wake. Questions for the human never go in the
digest: ask them with `sys_factory_ask`.
