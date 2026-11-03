// (sample) Synthetic data at the size of a big product: three goals, six more
// needs, ten more features, ~45 projects and ~360 tasks, seeded so every run
// draws the same factory. It shows folding, rows past 12, grouping past 60
// and paging; the page labels it as synthetic.

import type { RawFactory, RawNode } from "./sampleData";

/** Adds the synthetic nodes to `raw` (a fresh `sampleFactory()`), and returns it. */
export function withScale(raw: RawFactory): RawFactory {
  let seed = 7;
  const rnd = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  const nodes = raw.nodes;
  const goals: [string, string][] = [
    ["G-1", "New users get a working app on day one"],
    ["G-2", "Builders run their business from the app"],
    ["G-3", "Assistant costs stay under $0.40 per active user"],
  ];
  for (const [ref, title] of goals) {
    nodes.push({
      id: ref,
      tree: "product",
      parent: "product",
      lvl: "goal",
      kind: "goal",
      tag: ref,
      title,
      owner: "Atlas",
      why: "Synthetic goal.",
    });
  }
  nodes.push({
    id: "goal:none",
    tree: "product",
    parent: "product",
    lvl: "goal",
    kind: "goal",
    implicit: true,
    title: "No goal yet",
    owner: "Atlas",
    why: "Needs that no goal names yet.",
  });
  for (const n of nodes) if (n.lvl === "need" && n.parent === "product") n.parent = "goal:none";
  const plan: [string, string, [string, number][]][] = [
    [
      "G-2",
      "Builders want help selling what they built",
      [
        ["Sales skill", 4],
        ["CRM connector", 3],
      ],
    ],
    [
      "G-2",
      "Builders need users to find their app",
      [
        ["Promotion with ads", 4],
        ["Growth / SEO skill", 3],
      ],
    ],
    [
      "G-3",
      "Assistant cost must track usage",
      [
        ["Usage billing", 4],
        ["Model routing", 3],
      ],
    ],
    ["G-3", "The assistant must never act without consent", [["Safety and limits", 4]]],
    [
      "G-1",
      "First builds fail for many new users",
      [
        ["Build tier drafts", 4],
        ["Starter templates", 4],
      ],
    ],
    ["G-1", "Builders ask what their users do", [["Analytics answers", 4]]],
  ];
  const parts = ["core", "api", "ui", "connector", "tests", "docs"];
  const leads = ["Juno", "Iris", "Kai", "Nova", "Ori"];
  const builders = ["Luz", "Mae", "Ori"];
  let tn = 100;
  let pn = 9900;
  plan.forEach(([goal, needTitle, feats], ni) => {
    const nid = `O-${10 + ni}`;
    nodes.push({
      id: nid,
      tree: "product",
      parent: goal,
      lvl: "need",
      kind: "opportunity",
      tag: nid,
      title: needTitle,
      owner: "Scout",
      evidence: `${20 + Math.floor(rnd() * 80)} accounts · ${["moderate", "strong", "weak"][ni % 3]} evidence`,
      why: "Synthetic need.",
    });
    feats.forEach(([featureTitle, projects], fi) => {
      const fid = `I-${10 + ni * 2 + fi}`;
      const slug = featureTitle
        .toLowerCase()
        .replace(/[^a-z]+/g, "-")
        .replace(/-$/, "");
      nodes.push({
        id: fid,
        tree: "product",
        parent: nid,
        lvl: "feature",
        kind: "feature",
        tag: fid,
        title: featureTitle,
        owner: "Atlas",
        why: "Synthetic feature.",
        short: `${projects} projects`,
      });
      for (let p = 0; p < projects; p++) {
        const pid = `${slug}-${parts[p % parts.length]}`;
        const phase = rnd();
        nodes.push({
          id: pid,
          tree: "product",
          parent: fid,
          lvl: "project",
          kind: "project",
          title: pid,
          owner: leads[p % leads.length],
          col: p === 0 ? 0 : 1,
          cost: Number((rnd() * 60).toFixed(2)),
          budget: 150,
          why: "Synthetic project.",
        });
        const count = 5 + Math.floor(rnd() * 10);
        for (let t = 0; t < count; t++) {
          const r = rnd();
          const state =
            phase > 0.7
              ? "done"
              : r < 0.5
                ? "done"
                : r < 0.62
                  ? "moving"
                  : r < 0.72
                    ? "waiting"
                    : "notstarted";
          const id = `T-${tn}`;
          const task: RawNode = {
            id,
            tree: "product",
            parent: pid,
            lvl: "task",
            kind: "task",
            tag: id,
            title: `${["Wire the", "Test the", "Ship the", "Draft the", "Review the"][t % 5]} ${parts[t % parts.length]}`,
            owner: builders[t % builders.length],
            state,
            est: 30 + Math.floor(rnd() * 90),
            ...(state === "moving" ? { activity: "building" } : {}),
            ...(state === "waiting"
              ? { waits: [{ kind: "note" as const, label: "waits for a review slot" }] }
              : {}),
            ...(state === "done" ? { inBase: true } : {}),
          };
          nodes.push(task);
          if (state === "done" || state === "moving") {
            nodes.push({
              id: `PR-${pn}`,
              tree: "product",
              parent: id,
              lvl: "change",
              kind: "pr",
              tag: `#${pn}`,
              title: "synthetic change",
              state: state === "done" ? "done" : "moving",
              ...(state === "done"
                ? { inBase: true, doneNote: "merged · in base env" }
                : { activity: "checks running" }),
            });
            pn += 1;
          }
          tn += 1;
        }
      }
    });
  });
  raw.decisions.push({
    id: "D-90",
    node: "sales-skill-core",
    open: true,
    asker: "Atlas",
    age: "20m",
    verb: "Approve PII handling for Sales",
    title: "Approve PII handling for the Sales skill",
    text: "Synthetic ask for the scale demo.",
    actions: ["Approve", "Reject"],
  });
  return raw;
}
