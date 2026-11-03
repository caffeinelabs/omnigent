---
name: discovery-profile
description: Scout's cold start - when the product profile is empty or the strategy has no goal, draft the profile (name, kind, summary, links, segments with the rule a source can use, areas with code paths, competitors) from the repo checkout and the approved sources, and a first goal from the signals, then file both as proposals (sys_factory_propose kind="tuning") for the owners to correct. Never the delivery settings.
---

# Drafting the product profile and a first goal

The profile is the only way the factory learns what the product is. You draft
it; the owners correct it and apply it. Nothing you draft takes effect until an
owner applies it, and every word of it is data the owners can change.

## 1. Read what is there

- `sys_discovery_read(kind="profile")` and `sys_discovery_read(kind="strategy")`.
  Fill only what is empty; never rewrite what an owner set.
- `sys_discovery_read(kind="sources")`: what you may query.

## 2. Learn the product (cheapest first)

1. The repo checkout in your workspace, if there is one: `README*`, `docs/`,
   the top-level layout, route or page definitions, the changelog. Read with
   `ls`, `cat`, `head`, `grep`; change nothing.
2. The repo's own source (`kind: repo`): recent releases and the most
   discussed issues (`tool="releases"`, `tool="issues"`).
3. The public site, when the README or the profile links it and a `web`
   source is approved: `sys_discovery_query(source="<web source>", tool="fetch",
   args={url: "<the site>"})`. Never guess other sites.

Stop when you can say what the product is, who uses it and how they differ.
Spend at most a third of the day's budget on this.

## 3. Draft the profile

Write it in plain words, as the product describes itself (never as you would
sell it):
- `product.name`, `product.kind` (e.g. "web app", "CLI", "library"),
  `product.summary` (one paragraph: who it is for, what they do with it).
- `product.links`: `{site, docs, help, …}` that you saw.
- `product.segments`: 2 to 5 kinds of user, each `{id, label, rule}`, where
  `rule` is how a source can tell (e.g. "account age 14 days or less", "asks
  about self-hosting"). Prefer segments the evidence distinguishes.
- `product.areas`: the main product areas, each `{id, label, paths}` with code
  path globs from the checkout, so a bet can become a project with sensible
  scope.
- `product.competitors`: `{name, changelog}` only for those the product's own
  text or users name.
- Never `product.delivery` (merge policy, protected repos, demo paths, preview
  domain, release, flags): the owners set how work ships.

File it as one proposal:
`sys_factory_propose(kind="tuning", title="Product profile (draft)", changes=[{op: "replace", path: "product.summary", value: "…"}, {op: "add", path: "product.segments", value: {id: "new_user", label: "…", rule: "…"}}, …], rationale="Drafted from the README, docs and N signals; please correct names, segments and areas.", evidence=["repo:README.md", "S-12"])`.
A field error names its path: fix it and call again.

## 4. Suggest a first goal

From the profile and the signals so far, suggest one goal (two at most) in
users' terms, e.g. "More new users finish their first project in week one".
Leave `metric` empty unless a source can measure it; the factory plans an
enabler that measures it later.
`sys_factory_propose(kind="tuning", title="A first goal (suggested)", changes=[{op: "add", path: "strategy.goals", value: {statement: "…", weight: 1}}], rationale="Why this goal: what the signals show (S-…).", evidence=["S-…"])`.
The server numbers it `G-n`.

You may file 2 proposals a day: the profile and the goal take both on day one.

## 5. Tell people

One post: `sys_factory_chat(thread="discovery", message="Drafted the product profile (C-<n>) and a first goal (C-<m>) for the owners to correct. Next: the first sweep once signals arrive.")`.
Record both proposals in `~/factory/scout.md`. When an owner applies,
rejects or edits them you get `[Factory proposal · C-<n> …]`: re-read the
profile and work from the owners' version.
