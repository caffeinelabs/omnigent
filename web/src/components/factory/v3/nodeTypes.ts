// The work-levels API the v3 cockpit reads (`notes/factory-v3/final/api.md`):
// one node at a time, with its breadcrumb, status line, roll-up and children,
// nothing deeper than grandchild counts. The UI never computes state, paths
// or roll-ups; it renders these shapes. Level names, kinds, verbs and wording
// are data, so nothing here names a product or a level.
//
// Fields marked "(client)" (marked the same in api.md) let the page render
// without a second read (display names next to refs, the level's name on each
// node); the server's node projection (S4) fills them. Every one is optional:
// the page falls back to the level key, the ref or nothing, and `nodeWire.ts`
// also reads `also_holds` and `why` sent as plain strings.

/** A node's state; amber (`needs`) only when a person must act. */
export type NodeState =
  | "broken"
  | "needs"
  | "waiting"
  | "moving"
  | "notstarted"
  | "offered"
  | "done"
  | "later"
  | "ghost"
  | "paused";

/** Someone who owns or works on a node. */
export interface Actor {
  name: string;
  role?: string;
  /** Working now (an animated ring); idle people are counted, never drawn. */
  working?: boolean;
}

/** One answer a decision offers (`option_id` on `POST /decisions/{id}/answer`). */
export interface DecisionAction {
  id: string;
  label: string;
}

/** An open ask a blocker path ends at. */
export interface DecisionRef {
  /** `D-45`; a group of merges on one human-merged base has its own id. */
  id: string;
  /** What the person does, in words ("Apply C-16"). */
  verb: string;
  actions: DecisionAction[];
  asker: string;
  age: string;
  expires?: string;
  /** The answer happens elsewhere (a person merges on GitHub). */
  external_url?: string;
  /** What "Open …" names (the proposal, `C-16`); the id when absent. (client) */
  subject?: string;
  title?: string;
  text?: string;
  /** Asks that block nothing are counted, never coloured. */
  blocks_nothing?: boolean;
  /** A group (merges on one base): the id is not shown next to the verb. (client) */
  grouped?: boolean;
}

/** A path step: down into the child that blocks most (`›`), a hop or a hold (`→`), or the person. */
export type Segment =
  | { sep: "›"; ref: string; title?: string }
  | { sep: "→"; label: string; ref?: string; title?: string; reason?: string }
  | { sep: "→"; person: true; decision: DecisionRef };

/** A node named in a line of text: its address and how it reads. (client) */
export interface NodeLink {
  ref: string;
  title: string;
}

/** One numbered sentence of "why it is not moving" (limited markup, see `RichText`). */
export interface WhyItem {
  html: string;
  /** `you`: the ask that ends the path; `ok`: it is moving now. */
  tone?: "you" | "ok";
}

/** The one line that says whether a node moves, and if not, who must act. */
export interface StatusLine {
  kind:
    "moving" | "needs" | "waiting" | "done" | "later" | "offered" | "proposed" | "paused" | "idle";
  /** "Not started for 7 h:", "Held on purpose:", "Top blocker:", "Moving:", "Waits on". */
  label: string;
  segments: Segment[];
  ends: "person" | "server" | "moving" | "cycle" | "cut" | "other";
  decision?: DecisionRef;
  also_holds?: NodeLink[];
  moving?: { ref: string; title?: string; activity: string };
  /** Plain words after the label when there is no path ("#9876 merged 06:55 · in base env"). (client) */
  detail?: string;
  why?: WhyItem[];
  /** Why's heading ("Why assistant-panel-ux has not started"). (client) */
  why_title?: string;
  note?: string;
}

/** A link to another node, as a chip: kind · target · its state. Never a line. */
export interface Chip {
  kind?: string;
  ref?: string;
  title: string;
  state?: string;
  tone?: "amb" | "warn" | "acc" | "try";
  /** Why the link exists (`depends_why`), shown on hover. */
  reason?: string;
  /** A count badge ("3 met links"): opens these, grouped under breadcrumbs. */
  list?: ChipGroup[];
}

export interface ChipGroup {
  crumb?: string;
  chips: Chip[];
  note?: string;
}

/** A verb this viewer may use here (or why not). */
export interface VerbAvail {
  verb: string;
  label: string;
  key?: string;
  enabled: boolean;
  reason?: string;
}

/** The node's own chat thread. */
export interface ThreadRef {
  key: string;
  responder?: string;
  unread: number;
  last?: { who: string; at: string; text: string };
}

export interface Progress {
  value: number;
  label: string;
  what: "acceptance" | "tasks" | "projects" | "not planned";
  secondary?: number;
  growing?: boolean;
}

/** An ancestor in the breadcrumb (root first). */
export interface Crumb {
  ref: string;
  level: string;
  /** The level's name from the definition ("Feature"); the tree's label at a root. (client) */
  level_name?: string;
  title: string;
  state: NodeState;
  /** A small code next to the title ("T-16"), on crumbs of row levels only. (client) */
  tag?: string;
  implicit?: boolean;
  /** An implicit parent with one child, shown as that child (italic in the strip). */
  folded?: boolean;
}

export interface NodeSummary {
  ref: string;
  /** The level key from `/levels`; a root's level is its tree's root key ("product", "fx"). */
  level: string;
  /** The level's name ("Feature"). (client) */
  level_name?: string;
  kind: string;
  /** "Feature", "Study", "Pull request". (client) */
  kind_name?: string;
  /** A short glyph for nodes without a ring ("R", "T", "PR"). (client) */
  glyph?: string;
  /** A small code next to the title ("I-1", "#9876", "P0"). (client) */
  tag?: string;
  title: string;
  short?: string;
  /** The base a project merges into when it is not the factory's. (client) */
  base?: string;
  implicit?: boolean;
  folded_from?: string[];
  state: NodeState;
  col?: number | "later";
  held_for?: string;
  progress?: Progress;
  status_compact: StatusLine;
  needs: { open: number; blocking: number };
  cost?: number;
  burn?: number;
  budget?: number;
  in_base_env?: boolean;
  /** Something here can be tried in the base env (the "Try it" badge). (client) */
  try_ready?: boolean;
  /** Auto-merge covers this node (its own setting or an ancestor's). (client) */
  auto_merge?: boolean;
  changed?: string[];
  grandchildren?: {
    count: number;
    by_state: Partial<Record<NodeState, number>>;
    later: number;
    /** "projects", "task". (client) */
    noun?: string;
  };
  owner?: Actor;
  working?: Actor[];
  idle?: number;
  /** The node's one change, shown inline in a task row ("#9876 · merged"). (client) */
  change?: { ref: string; label: string };
}

export interface AcceptanceStep {
  text: string;
  result: "pass" | "fail" | "none";
  source: string;
  ref?: string;
}

/** A thing to try in the base env (api.md `NodeFull.try_it`; the Try it sheet reads it). */
export interface TryStep {
  text: string;
  how?: string;
  acceptance_step?: number;
  api?: boolean;
}

export interface NodeFull extends NodeSummary {
  /** Product words; may hold `<b>` and `<a data-ref>` (see `RichText`). */
  why_html: string;
  budget?: number;
  try_it?: TryStep[];
  acceptance?: { steps: AcceptanceStep[]; note?: string; run_at?: string };
  evidence?: {
    summary: string;
    items: {
      kind: "request" | "signals" | "finding" | "gap";
      text: string;
      who?: string;
      quote?: boolean;
    }[];
  };
}

/** A root's count of one level or kind, named from the definition ("3 features"). */
export interface RollupCount {
  /** A level key ("feature") or a kind at a level ("study"). */
  key: string;
  /** The singular name ("Feature"), for a count of one. */
  name: string;
  /** The plural name ("Features"). */
  plural: string;
  count: number;
  /** Held items of this level, not in `count`. */
  later?: number;
}

export interface Rollup {
  progress?: Progress;
  /** At a root: what the tree holds, level by level (no zero entries). */
  counts?: RollupCount[];
  cost: number;
  budget?: number;
  burn?: number;
  eta?: string;
  team?: { working: Actor[]; idle: number };
  in_base_env?: { sha: string; try_it: boolean };
  since?: { at: string; items: string[]; more: number };
  moving_anywhere: boolean;
  alarms_closed?: { count: number; ref: string };
}

/** One level below a node, for the strip: only levels with at least one node. */
export interface LevelCount {
  level: string;
  /** The level's plural name ("Projects"). */
  name: string;
  count: number;
  later: number;
  needs: number;
  by_state: Partial<Record<NodeState, number>>;
  /** Shown only while debugging (Runs). (client) */
  debug?: boolean;
}

export interface Results {
  merged: number;
  in_base_env: number;
  open: string[];
  readouts: "not measured" | { day7?: string; day28?: string };
}

/** A node's direct children, with their order among siblings. */
export interface ChildPage {
  level: string;
  /** The plural name of the children's level ("Projects"). (client) */
  level_name?: string;
  /** First / Then / …, from dependencies among siblings; null when unordered. */
  columns: { title: string; col: number }[] | null;
  /** The title of the held column ("Later (held)"). (client) */
  later_title?: string;
  /** Why held items wait, once under the list. (client) */
  later_note?: string;
  items: NodeSummary[];
  later: NodeSummary[];
  ghosts: NodeSummary[];
  /** Cursor of the next 60 (`GET …/n/{ref}?children_page=`). */
  next?: string;
  total: number;
  grouped_by_state?: boolean;
  /** This level is drawn as tiles (levels above Task) or only as rows; tiles when absent. (client) */
  tiles?: boolean;
  /** Children shown in place of an implicit parent. (client) */
  via_fold?: number;
}

export interface NodePage {
  node: NodeFull;
  breadcrumb: Crumb[];
  status: StatusLine;
  rollup: Rollup;
  chips: Chip[];
  more_chips: number;
  /** The chips past the first three, grouped (what "+n" opens). (client) */
  chips_more?: ChipGroup[];
  verbs: VerbAvail[];
  levels_below: LevelCount[];
  results?: Results;
  children: ChildPage;
  thread: ThreadRef;
  version: string;
  /** At a root, the level the page shows by default ("feature": Home). (client) */
  home?: string;
}

export interface LevelGroup {
  parent: Crumb & { ancestors: string[]; evidence?: string };
  items: NodeSummary[];
  folded_done?: boolean;
  later?: string[];
  /** Cursor of this group's next 60 (`GET …/level/{level}?cursor=`). */
  next?: string;
}

/** One level under a node, grouped under each item's parent. */
export interface LevelView {
  scope: string;
  level: string;
  /** The level's plural name ("Tasks"). (client) */
  level_name?: string;
  /** The parents' plural name ("projects"). (client) */
  parent_level_name?: string;
  total: number;
  groups: LevelGroup[];
  done_groups?: { count: number; titles: string[]; in_base_env: boolean; noun?: string };
  /** Tiles or rows only, as on `ChildPage`; tiles when absent. (client) */
  tiles?: boolean;
  /** (client) */
  via_fold?: number;
}
