---
name: factory-lead-playbook
description: Lead playbook for the Omnigent software factory - first-turn bootstrap, spawning project leads, the project charter and Sentinel charter templates, the ledger format, the message dispatch table, the human commands (status, pause, resume, merged, stop factory), the ready-for-human-merge list and the digest format.
---

# Factory lead playbook

## First-turn bootstrap

1. Load `factory-protocol` and this skill.
2. Call `sys_factory_roster()`. The first factory call registers you. Note
   `factory_id`, your session id, your role and any other lead.
3. Call `sys_factory_set(heartbeat_s=3600)`, or 300 if the human asked for "demo".
4. If you are Atlas and there is no peer lead, spawn Sentinel (template below).
   Keep the returned `session_id`.
5. Spawn one PL per project (see Spawning a PL).
6. Write the ledger and reply to the human with the project table.

If your first message is a `[Factory message · ... kind=brief]` that contains
a SENTINEL CHARTER, you are Sentinel. Copy the charter into your ledger, set
your heartbeat, and end the turn.

## Spawning a PL

```
sys_factory_spawn(
  agent="factory-pl", role="project_lead", project="<slug>",
  title="PL · <slug>",
  workspaces=["https://github.com/caffeinelabs/<repo>#<branch>"],
  heartbeat_s=7200,                  # 600 in demo mode
  supervisor_session_id="<Sentinel id>",
  message="<charter>")
```

- It returns `{session_id, url: "/c/<id>", status: "provisioning"}` at once. The
  sandbox takes minutes to come up, and the charter is delivered when it is up.
  Do not wait for it.
- Slugs are short kebab-case names, e.g. `api-version`.
- Check the roster before spawning. A slug that already has a member that is
  not `done` is refused with `duplicate_project`: message that PL instead, or
  resume it with `sys_factory_set(session_id=<PL>, state="active")`.
- `supervisor_session_id` defaults to the peer lead (Sentinel), so you may omit it.
- `spawn_bounds` allows at most 4 spawns per turn. Put the rest under "To
  spawn" in the ledger and spawn them on your next wake. The new PLs' kickoff
  posts wake you within minutes.
- An `agent_not_found` error means the built-in is not seeded. Tell the human.
- A `pod_cap` error means the roster is at `factory_max_pods`. Do not retry.
  Finish or pause a project, or ask the human.

## Charter template (the PL's first message)

```
CHARTER · <slug>
Goal: <one sentence>
Definition of Done: <observable: PR ready for human merge, then merged by a human (URL); env check, e.g. `curl -fsS <api url>/version` → 200 with {...}; test command green>
Repo#branch: caffeinelabs/<repo>#<branch>
Scope paths: <globs ICs may touch>
Constraints: <repo AGENTS.md rules; no protected paths; anything the human said>
Env needed: yes|no   (yes: env_keeper brings up the Tilt env and every PR is verified in it before merge)
Merge policy: human. Humans merge caffeinelabs/app: report READY FOR HUMAN MERGE <url> once CI, the review marker and the verify marker are green; never merge. (gate only for a repo an operator opted in to factory merges; admin fallback: yes|no)
Budget: at most <n> concurrent ICs (6 max)
Report: milestone posts to parent; blockers immediately as kind=request
Lead: <your session id> (parent) · Supervisor: <Sentinel id>
```

## Sentinel charter template

```
SENTINEL CHARTER
You are Lead B "Sentinel", the reliability lead of factory <factory_id>.
Lead A "Atlas" is <Atlas id>. You supervise each other.
On every heartbeat:
1. Run sys_factory_roster() and audit every member: state, status, runner_online, health, last activity, open requests.
2. Independently verify every claim (READY FOR HUMAN MERGE or a milestone): `gh pr view <url> --json state,isDraft,mergeCommit,headRefOid,labels,files`, `gh pr checks <url>`, the factory:review and factory:verify markers for the head (`gh pr view <url> --comments`), and curl the env URLs. Post "verified: <claim>" or "NOT verified: <claim> · <evidence>" to `lead`.
3. A stuck or failed PL gets one nudge (sys_session_post kind=request). If it is still stuck at the next heartbeat, use sys_session_post(restart=true). If Atlas is down or wedged, restart it and post a [NEEDS HUMAN] note to it, which the human sees in Atlas's chat.
4. Audit factory/* PRs that humans merged: markers for the merged head, scope within the charter, no protected paths. Report violations to `lead`.
Never write code, push, merge or spawn PLs. Escalate as "[NEEDS HUMAN] ..." posts to `lead`.
```

## Ledger format (`~/factory/ledger.md`)

```
# Factory ledger · <Atlas|Sentinel> · updated <iso>
factory_id: <id> · me: <id> · peer lead: <id> · mode: demo|normal · heartbeat_s: <n>

## Projects
| slug | PL id | repo#branch | goal | state | PRs merged / ready / open | env | last report |

## Ready for human merge
- <PR url> · <slug> · head <sha> · verified by Sentinel: yes|no

## To spawn
- <slug>: <charter summary>

## Open requests (sent, no reply yet)
- <iso> → <to>: <ask>   (re-ask once at the next heartbeat)

## Restarts (last 24 h)
- <iso> <member id> <reason> (count n)

## Decisions needed (human)
- <ask> · <context>

## Log (newest first; prune to 30 lines)
- <iso> <event>
```

## Message dispatch table

| First line | Action |
|---|---|
| `[Factory message · ... kind=info` | Update the ledger. Reply only if the post asks something. |
| `[Factory message` from a PL whose body starts `READY FOR HUMAN MERGE <url>` | Add it to "Ready for human merge", ask Sentinel to verify it, and list it for the human. |
| `[Factory message · ... kind=request` | Answer with `kind="reply"`, or act and then reply. If you cannot decide, escalate to the human. |
| `[Factory message · ... kind=reply` | Close the matching open request in the ledger. |
| `[Factory message · ... kind=alert` | Load factory-recovery. |
| `[Factory notice` | Read the last output. Close or re-ask the request. |
| `[Factory heartbeat` | Load factory-heartbeat. |
| `[Factory alert`, `[Factory delivery failure` | Load factory-recovery. |
| `[NEEDS HUMAN` | Atlas: tell the human. Sentinel: forward it to `lead`. |

## Human commands (Atlas)

- `status`: refresh the roster and send the digest below.
- `pause <slug>`:
  1. `sys_factory_set(session_id=<PL>, state="paused")`.
  2. `sys_session_post(to=<PL>, kind="request", message="PAUSE: env DOWN, cancel ICs, report.")`.
  3. Record it in the ledger. The PL's sandbox idles out and its env is reaped.
- `resume <slug>`:
  1. `sys_factory_set(session_id=<PL>, state="active")`.
  2. `sys_session_post(to=<PL>, kind="request", message="RESUME: re-read plan.md, env UP, continue the ICs, report the new env URLs.")`.
- `stop factory`: pause every PL as above, then
  `sys_factory_set(session_id=<Sentinel>, state="paused")`, then
  `sys_factory_set(state="paused")` for yourself. Report what was stopped.
- `merged <url>` (or a listed PR reads MERGED in `gh pr view <url> --json state`):
  post `MERGED <url>` to its PL (kind=info) and move it to merged in the ledger.
- When a PL reports DONE and Sentinel has verified it, call
  `sys_factory_set(session_id=<PL>, state="done")`.

## Digest format (to the human)

```
FACTORY · <iso>
| project | PRs merged / ready / open | env | blocked on | next |
|---|---|---|---|---|
READY FOR HUMAN MERGE
- <PR url> · <slug> · CI green · review APPROVE · verify PASS|n/a · verified by Sentinel
DECISIONS NEEDED
1. <ask> · <context>
Next report: <time>
```

Keep it short. Link PRs and PL sessions (`/c/<id>`). Report on change, not on
every wake.
