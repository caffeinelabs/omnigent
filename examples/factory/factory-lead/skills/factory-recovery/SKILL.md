---
name: factory-recovery
description: Lead diagnosis tree for "[Factory alert", "[Factory delivery failure" and "[NEEDS HUMAN" messages in the Omnigent software factory - wake a down member, restart a wedged one, replace a member after repeated failures, ask the human through sys_factory_ask, and handle capacity, cost and message-storm limits.
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
  `[NEEDS HUMAN · "<title>" waiting since <ts>: <what>]`. The server already
  opened each as a decision in the cockpit (with Restart, Relaunch or Pause
  options); never ask it again.

Questions you raise yourself go through
`sys_factory_ask(question=<one line>, options=[{label, description, recommended}], context=<evidence>, blocking=true)`.
It returns `D-<n>` at once; record it in the ledger and keep working.

## Diagnosis tree

1. **Runner or pod down.** The roster shows `runner_online=false`, or the
   status is idle or failed while work is pending. Any `sys_session_post` wakes
   or relaunches it on the same disk. Post:
   "You were down. Re-read plan.md (or ledger.md), then report status."
   Record it and stop here.
2. **Wedged.** It is running but makes no progress. Typical signs: the same
   output for 90 min or more, a TUI waiting on a prompt, or a tool loop.
   1. Inspect it with `sys_session_get_history(conversation_id=<id>)`.
   2. Send one precise nudge.
   3. If it is still stuck at the next heartbeat, use
      `sys_session_post(to=<id>, restart=true, kind="request", message="Restarted by <you>: <reason>. Re-read plan.md and continue.")`.
3. **Failed.**
   - If the error names the model or the gateway (429, 5xx, overloaded,
     credentials), it is an outage, not the agent. Note it, and ask PLs to shift
     work to `ic_codex`. If it lasts more than 1 h, ask the human.
   - Any other failure gets one `restart=true`.
4. **Third restart in 24 h, or relaunch failed 3×: replace the member.**
   1. `sys_factory_set(session_id=<old>, state="done")`. Spawn refuses a slug
      that still has a member that is not `done`, and a done member gets no
      heartbeats and idles out.
   2. `sys_factory_spawn` a new PL with the same slug. Its charter starts with
      "Continue from <old id>: read its plan via
      sys_session_get_history(conversation_id=<old id>) and its open PRs."
   3. Tell the human in the next digest (Atlas) or post to `lead` (Sentinel).
   A dead lead cannot replace itself. The surviving lead asks the human.
5. **Delivery failure.**
   - If the target is `paused`, `done` or gone, drop the request.
   - Otherwise re-post at the next heartbeat. If that fails twice, treat the
     target as down (step 1), then escalate.
6. **Blocked on a human** (an approval card or a credential). The cockpit
   shows it as a decision; Atlas names it in the next digest. Never approve on
   the human's behalf.
7. **Message storm.**
   1. Find the pair in the alert.
   2. Tell both members to stop the exchange and send one consolidated message.
   3. If it repeats, pause the noisier member. The server opens a storm
      decision for the human.

## Capacity and cost

- `pod_cap` from `sys_factory_spawn`: do not retry. Finish or pause a project
  first, or ask the human to raise `factory_max_pods`.
- A `spawn_bounds` denial means at most 4 spawns per turn. Spawn the rest on
  the next wake.
- Each PL runs at most 6 concurrent ICs and its charter's preview slots
  (at most 3). A PL that asks for more needs a human decision.
- `preview_cap` (the factory runs `OMNIGENT_FACTORY_MAX_PREVIEWS` live
  previews): ask the PLs to park previews of PRs that are not ready.
- Model or gateway rate limits: ask PLs to lower IC concurrency and to prefer
  `ic_codex`.
- Paused members cost nothing unless they hold previews of PRs awaiting
  review, which keep their pod alive. Idle previews are parked after 72 h; the
  envs of a pod that idles out are reaped.
