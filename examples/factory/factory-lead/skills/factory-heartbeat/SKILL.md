---
name: factory-heartbeat
description: Lead heartbeat sweep for the Omnigent software factory - run on every "[Factory heartbeat" message. Audits direct reports and the peer lead, nudges or restarts stuck and failed members, re-asks unanswered requests, updates the ledger, and reports to the human only on change.
---

# Lead heartbeat

The server sends `[Factory heartbeat #<n> · <iso>]` when you have been idle for
`heartbeat_s`. Do the sweep in one turn. Write to the human only if something
changed.

1. Read `~/factory/ledger.md`.
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
     1. Inspect it with `sys_session_get_history(session_id=<id>)`.
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
   restart it. If it is dead, raise `[NEEDS HUMAN]` (Sentinel posts it to Atlas;
   Atlas tells the human).
5. Re-ask once every open request older than one heartbeat. Drop requests
   whose target is `done`.
6. Atlas: spawn anything left under "To spawn".
7. Update the ledger and your beacon (`sys_factory_set(title=..., summary=...)`).
8. Atlas only: if anything changed (milestone, verified claim, blocker,
   decision needed), send the human the digest from the playbook. Otherwise
   end the turn with no chat output.
