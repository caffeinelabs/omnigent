---
name: factory-heartbeat
description: Project-lead heartbeat sweep for the Omnigent software factory - run on every "[Factory heartbeat" message. Checks every IC and the env, nudges or re-dispatches stuck and failed ICs, pushes open PRs through review, verify and the merge gate, updates plan.md and the beacon, and reports to the lead only on change.
---

# PL heartbeat

The server sends `[Factory heartbeat #<n> · <iso>]` when you have been idle for
`heartbeat_s`. Do the sweep in one turn.

1. Read `~/factory/<slug>/plan.md`. Call `sys_factory_roster()`; your
   sub-agents are listed under your row. Use `sys_session_list` if you need
   more detail.
2. For every IC, check its status and when it last made progress:
   - Running and active within the last 90 min: fine.
   - Running with no progress for 90 min or more, or a stuck alert:
     1. Inspect it with `sys_session_get_history(session_id=<ic id>)`.
     2. Nudge it with `sys_session_send(session_id=<ic id>, args={input: "<precise next step>"})`.
     3. If it is still stuck at the next heartbeat, call `sys_cancel_task(task_id=<ic id>)`
        and re-dispatch `<task>-r<k>` with a handoff: worktree, branch, PR and
        "read NOTES.md first".
   - Idle and its task is done: give it the next card, or `sys_session_close` it.
   - Idle with an `IN_PROGRESS` result: tell it the next step.
   - Failed: re-dispatch the task, or switch between `ic` and `ic_codex`.
3. Env, when the env is needed:
   - If the last STATUS is older than 30 min, or the env is not READY, send
     `sys_session_send(session_id=<env_keeper id>, args={input: "STATUS"})`.
   - After a wake the env is gone. Send `UP`.
4. For each open PR, check CI, the review marker and the verify marker against
   the current head, and push it through the next step of the pipeline.
5. Re-ask once any request to `parent` or peers that has had no answer since the
   last heartbeat.
6. Update plan.md. Call `sys_factory_set(summary="<tasks done/total> · <PRs> · env <state>", health="ok"|"degraded"|"blocked")`.
7. Post to `parent` only if something changed: a milestone, a blocker, or a
   need. Otherwise end the turn quietly.
