# Factory cockpit review guide

Open `/factory/<factory id>`. The **Projects** board shows each project with a
pipeline stage and its PR cards. A project moves through `Planning`,
`Building`, `Review`, `Verify`, `Ready` and `Merged`; it can also wait while
`Queued` or show `Done` after closure. PR cards list open PRs first, with ready
work ahead of the rest.

## Gate chips

PR cards show gate chips, including `Draft`, `Base`, `CI`, `Review`, `Verify`,
`Demo`, `Mergeable` and `Paths`. A passing gate is a neutral pill with a green
glyph; pending is amber, failed is red, and `not needed`/`unknown` is grey.

- `Draft` waits until the PR is marked ready on GitHub.
- `Base` fails when the PR targets a branch other than its project's base.
- `CI` checks the head's required checks.
- `Review` requires an approving factory review marker for the current head.
- `Verify` requires a `PASS` factory verify marker for the current head, or
  skips it for PRs labelled `factory:no-env` or `factory:revert`.
- `Demo` appears when the project uses previews and requires a `PASS` or
  `PARTIAL` factory demo of the head.
- `Mergeable` follows GitHub: pending while it computes conflicts, then green
  when mergeable or red when conflicts remain.
- `Paths` checks changed paths against protected path patterns. It is red if a
  protected path is touched without the `factory:allow-protected` label.

The GitHub poller computes all gates server-side against the PR's current head
SHA. Markers must name that head, and review markers must be newer than the
newest commit. Hover a chip for counts, marker SHA, links and failure reasons;
a marker SHA that differs from the PR head is flagged as an old head.

## Factory previews

A runnable PR gets one live factory preview, deployed at its head by
env_keeper, which then verifies it. The stable `Live preview` link is a cockpit
redirect that always opens the preview's current deploy. The prober compares
the observed commit with the PR head and flags a preview that is behind head.

Project preview slots cap how many live previews run at once. Idle or capped
previews can be parked. Restore a parked preview, or redeploy a preview that is
behind the head, unreachable or failed. PRs labelled `factory:no-env` get no
preview and no demo.

## Recorded demos

The project's **Demos** section shows one card per PR with its newest demo.
That card can play its Playwright video and show one screenshot per UI step,
the API calls, console errors, and the step list with pass, fail and skip
results.

A demo is `PASS`, `PARTIAL`, `FAIL` or `ERROR`. The demo agent also posts a PR
comment naming the demo and its full head SHA. `PASS` and `PARTIAL` pass the
demo gate; `FAIL` is red, while a new, `ERROR`, or unresolved run is pending.
PRs labelled `factory:no-env` skip the demo gate.

## Decisions and review

Agents ask `D-n` questions through factory decisions. Open decisions appear in
the cockpit Inbox; answer one there, or claim or assign it to operators,
owners, or a person when it needs a different reviewer.

Open a PR's review page to see its gates, preview, demo and evidence. Use
`Approve & open merge` or `Request changes…`. The cockpit records the review
for that PR head and opens GitHub's merge view. Humans always merge on GitHub;
the factory never merges.
