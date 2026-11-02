"""Slack DM notifications for the factory.

Each person on a factory's roster links their own Slack account (a member id
or Slack email, proven by a code DM) and gets a personal DM for what is
waiting on *them*: decisions (with reminders and a last call before anything
defaults), PRs ready for human merge, incidents, factory and budget changes,
idle spend, Room mentions and a daily digest.

- :mod:`.client`: the Slack Web API client (``auth.test``, ``users.info``,
  ``users.lookupByEmail``, ``conversations.open``, ``chat.postMessage``).
- :mod:`.secrets`: the per-factory bot token (a Tier-0 secret) and its metadata.
- :mod:`.prefs`: per-person settings in ``factory_people.notify``, role
  defaults, quiet hours and the redacted roster summary.
- :mod:`.rules`: recipients, deadlines and the items E1-E11.
- :mod:`.render`: DM text and blocks (escaped, clipped, links built from ids).
- :mod:`.digest`: the daily digest and the welcome DM.
- :mod:`.engine`: the tick, queues, quiet hours, the ``notify.slack`` ledger.

The engine runs as a background service (``background/slack_dm.py``); no
factory code path calls into this package, so Slack can never block one.
"""
