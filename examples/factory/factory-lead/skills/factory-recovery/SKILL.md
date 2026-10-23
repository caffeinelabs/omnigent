---
name: factory-recovery
description: Lead diagnosis tree for "[Factory alert", "[Factory delivery failure" and "[NEEDS HUMAN" messages in the Omnigent software factory - wake a down member, restart a wedged one, replace a member after repeated failures, and handle capacity, cost and message-storm limits.
---

# Factory recovery (leads)

Never cancel a long-running build or test just because it is slow. The server
never auto-cancels, and neither do you.

## Alerts you will see

- `[Factory alert · "<title>" (<id>) running with no progress for <dur> ...]`: stuck.
- `[Factory alert · "<title>" failed: <error>]`: failed.
- `[Factory alert · message storm ...]`: a pair hit the rate limit.
- `[Factory delivery failure · message to "<title>" (<id>) undeliverable: <reason>]`.
- `[NEEDS HUMAN · relaunch of "<title>" failed 3× (<reason>)]` or
  `[NEEDS HUMAN · "<title>" waiting since <ts>: <what>]`.

## Diagnosis tree

1. **Runner or pod down.** The roster shows `runner_online=false`, or the
   status is idle or failed while work is pending. Any `sys_session_post` wakes
   or relaunches it on the same disk. Post:
   "You were down. Re-read plan.md (or ledger.md), then report status."
   Record it and stop here.
2. **Wedged.** It is running but makes no progress. Typical signs: the same
   output for 90 min or more, a TUI waiting on a prompt, or a tool loop.
   1. Inspect it with `sys_session_get_history(session_id=<id>)`.
   2. Send one precise nudge.
   3. If it is still stuck at the next heartbeat, use
      `sys_session_post(to=<id>, restart=true, kind="request", message="Restarted by <you>: <reason>. Re-read plan.md and continue.")`.
3. **Failed.**
   - If the error names the model or the gateway (429, 5xx, overloaded,
     credentials), it is an outage, not the agent. Note it, and ask PLs to shift
     work to `ic_codex`. If it lasts more than 1 h, raise `[NEEDS HUMAN]`.
   - Any other failure gets one `restart=true`.
4. **Third restart in 24 h, or relaunch failed 3×: replace the member.**
   1. `sys_factory_set(session_id=<old>, state="paused")`, so the project has
      one active PL.
   2. `sys_factory_spawn` a new PL with the same slug. Its charter starts with
      "Continue from <old id>: read its plan via
      sys_session_get_history(session_id=<old id>) and its open PRs."
   3. Tell the human (Atlas) or post to `lead` (Sentinel).
   A dead lead cannot replace itself. The surviving lead raises `[NEEDS HUMAN]`.
5. **Delivery failure.**
   - If the target is `paused`, `done` or gone, drop the request.
   - Otherwise re-post at the next heartbeat. If that fails twice, treat the
     target as down (step 1), then escalate.
6. **Blocked on a human** (an approval card or a credential). Atlas puts it
   first in the human digest. Never approve on the human's behalf.
7. **Message storm.**
   1. Find the pair in the alert.
   2. Tell both members to stop the exchange and send one consolidated message.
   3. If it repeats, pause the noisier member and tell the human.

## Capacity and cost

- `pod_cap` from `sys_factory_spawn`: do not retry. Finish or pause a project
  first, or ask the human to raise `factory_max_pods`.
- A `spawn_bounds` denial means at most 4 spawns per turn. Spawn the rest on
  the next wake.
- Each PL runs at most 6 concurrent ICs. A PL that asks for more needs a
  human decision.
- Model or gateway rate limits: ask PLs to lower IC concurrency and to prefer
  `ic_codex`.
- Paused members cost nothing. Their sandboxes idle out and their envs are reaped.
