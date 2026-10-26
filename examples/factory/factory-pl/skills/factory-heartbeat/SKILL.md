---
name: factory-heartbeat
description: Project-lead heartbeat sweep for the Omnigent software factory - run on every "[Factory heartbeat" message. Handles only the items in the heartbeat's delta, checks every IC and the envs, nudges or re-dispatches stuck and failed ICs, redeploys previews of PRs awaiting review, updates plan.md and the beacon, and ends with exactly "HEARTBEAT_OK · <status>" when nothing needed doing.
---

# PL heartbeat

The server sends `[Factory heartbeat #<n> · <iso>]` when you have been idle for
`heartbeat_s`, often followed by `<k> changes since #<m>: • … • …`: the
delta. CI results, review and verify markers, merges, closes and preview
health come to you as their own notices; the heartbeat never needs GitHub or
curl. Do the sweep in one turn.

1. Read `~/factory/<slug>/plan.md`. Handle only the items listed in the delta,
   plus anything plan.md marks as waiting on a deadline.
2. Call `sys_factory_roster()`; your sub-agents are listed under your row. For
   every IC and demo agent:
   - Running and active within the last 90 min: fine.
   - Running with no progress for 90 min or more, or a stuck alert:
     1. Inspect it with `sys_session_get_history(conversation_id=<id>)`.
     2. Nudge it with `sys_session_send(session_id=<id>, args={input: "<precise next step>"})`.
     3. If it is still stuck at the next heartbeat, call `sys_cancel_task(task_id=<id>)`
        and re-dispatch `<task>-r<k>` with a handoff: worktree, branch, PR and
        "read NOTES.md first".
   - Idle and its task is done: give it the next card, or
     `sys_session_close(conversation_id=<id>)` it.
   - Idle with an `IN_PROGRESS` result: tell it the next step.
   - Failed: re-dispatch the task, or switch between `ic` and `ic_codex`.
3. Envs, when the env is needed:
   - After a wake (the roster shows a new runner, or env_keeper says the env
     is gone): env_keeper `UP`, and `PREVIEW pr=<n> head=<sha>` for every PR
     awaiting human review.
   - A dev env not READY: env_keeper `STATUS`.
4. Every open PR in plan.md has a next step in the pipeline (playbook) or is
   handed over and waiting for a human. Push the ones that are stuck.
5. Re-ask once any request to `parent` or peers that has had no answer since
   the last heartbeat. Open D-n questions need no re-asking; the cockpit
   holds them.
6. Update plan.md. If anything changed, call
   `sys_factory_set(summary="<tasks done/total> · <PRs> · env <state>", health="ok"|"degraded"|"blocked")`
   and post to `parent` only for a milestone, a blocker or a need.
7. If nothing needed doing, end your turn with exactly one line:
   `HEARTBEAT_OK · <status of at most 80 chars>`
   for example `HEARTBEAT_OK · 2 PRs awaiting human merge · previews up`.
   No other text: the cockpit folds these turns.
