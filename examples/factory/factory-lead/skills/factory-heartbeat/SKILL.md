---
name: factory-heartbeat
description: Lead heartbeat sweep for the Omnigent software factory - run on every "[Factory heartbeat" message. Handles only the items in the heartbeat's delta, audits direct reports and the peer lead, nudges or restarts stuck and failed members, re-asks unanswered requests, updates the ledger, and ends with exactly "HEARTBEAT_OK · <status>" when nothing needed doing.
---

# Lead heartbeat

The server sends `[Factory heartbeat #<n> · <iso>]` when you have been idle for
`heartbeat_s`, often followed by `<k> changes since #<m>: • … • …`: the
delta. PR readiness, merges and closes reach you as `[Factory notice · PR …]`
messages and the cockpit shows the gates; the heartbeat never polls GitHub.
Do the sweep in one turn.

1. Read `~/factory/ledger.md`. Handle only the items listed in the delta,
   plus anything the ledger marks as waiting on a deadline.
2. Call `sys_factory_roster()`.
3. For every report, check state, status, `runner_online`, health, summary
   and last activity. Atlas's reports are its PLs. Sentinel's are every PL and
   Atlas.
   - `paused` or `done`: skip it.
   - Running with recent activity (under 90 min): fine.
   - Idle with no report since the last heartbeat and no next step in its
     summary: nudge it with `sys_session_post(to=<id>, kind="request",
     message="Heartbeat check: status and next step?")`.
   - Running with no activity for 90 min or more, or a stuck alert:
     1. Inspect it with `sys_session_get_history(conversation_id=<id>)`.
     2. Nudge it with one precise instruction.
     3. If it is still stuck at the next heartbeat, use
        `sys_session_post(to=<id>, restart=true, kind="request", message="Restarted by <you>: <reason>. Re-read plan.md and continue.")`.
   - `failed`, or `runner_online=false` while it should be working: post a
     request, which wakes or relaunches it. If it is wedged, use `restart=true`.
     Count every restart in the ledger. The third restart in 24 h goes to
     factory-recovery (replace the member).
   - Health `degraded`, `blocked` or `dead`: read its summary and history.
     Unblock it or escalate.
4. Check the peer lead. If it is offline, a post wakes it. If it is wedged,
   restart it. If it is dead, ask the human with `sys_factory_ask` (blocking).
5. Re-ask once every open request older than one heartbeat. Drop requests
   whose target is `done`. Open D-n questions need no re-asking; the cockpit
   holds them.
6. Atlas: spawn anything left under "To spawn".
7. Update the ledger, and your beacon if it changed
   (`sys_factory_set(summary="<at most 120 chars>")`; your title stays your name).
8. Atlas only: if something the human should know changed (a milestone, a PR
   ready for human merge, a verified claim, a blocker), send the digest from
   the playbook (at most 5 lines and the cockpit link).
9. If nothing needed doing, end your turn with exactly one line:
   `HEARTBEAT_OK · <status of at most 80 chars>`
   for example `HEARTBEAT_OK · 3 projects building · 1 PR awaiting merge`.
   No other text: the cockpit folds these turns.
