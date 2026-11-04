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
  /** The decision's row id, which the answer route takes; absent on a group of merges. */
  row_id?: string;
}

/** An answer's Undo: the proposal it applied, which can be taken back until `until_s`. (client) */
export interface AnswerUndo {
  /** `C-16`. */
  proposal: string;
  /** Epoch seconds on the server's clock (the proposal's `undo_until`). */
  until_s: number;
}

/** What an answer did: its Undo while the proposal it applied keeps one. (client) */
export interface AnswerOutcome {
  undo?: AnswerUndo | null;
}

/** What taking an answer back left: the proposal asks a person again in `decision` (`D-52`). (client) */
export interface AnswerUndone {
  decision?: string | null;
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

/** What a verb's preview starts from, when words in the chat named it ("plan Google Ads"). (client) */
export interface VerbArgs {
  /** A new child's title. */
  title?: string;
  /** "swap luz to codex": the member, and what to swap to. */
  member?: string;
  to?: string;
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

/** The base env on the root: its branch, the commit it runs and its links (api.md §3.8). */
export interface BaseEnv {
  name: string;
  branch: string;
  sha?: string;
  deployed_at?: string;
  state?: string;
  smoke?: { ok: boolean; checks?: string };
  app_url?: string;
  api_url?: string;
  /** How to sign in there (api.md also names it `signin_note`). */
  note?: string;
  signin_note?: string;
  /** What it follows and how fast ("moves about 72 s after each merge"). */
  follows?: string;
  /** The project that keeps it, in the factory tree. */
  ref?: string;
  /** The factory area that shows it (`fx:environments`). */
  area?: string;
}

/** An alarm the server closed by itself: what it asked, why it closed, the real ask. */
export interface ClosedAlarm {
  id: string;
  was: string;
  cause: string;
  real_ask?: string;
  /** What the real ask has a person do ("Apply C-16"). */
  real_ask_verb?: string;
  /** The node it was about. */
  ref?: string;
}

export interface NodeFull extends NodeSummary {
  /** Product words; may hold `<b>` and `<a data-ref>` (see `RichText`). */
  why_html: string;
  budget?: number;
  try_it?: TryStep[];
  /** The flag a person turns on to try what passes ("assistant, for a flag-on user"). (client) */
  try_flag?: string;
  /** On the product root. */
  env?: BaseEnv;
  /** On the factory tree's Health area: today's alarms the server closed. */
  closed_alarms?: ClosedAlarm[];
  /** On the factory tree's Definition area: the page shows the levels (`GET …/levels`). */
  levels?: boolean;
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
  /** Merged changes the base env runs; absent when the factory has no base env. */
  in_base_env?: number;
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

// ── Steering: verb previews, apply and Undo (api.md §4) ─────────

/** What a verb's preview and apply send: the verb, its arguments and the facts version read. */
/** One argument of a verb: a word, a number, a switch, a list of refs, or a reason per ref. */
export type VerbArg = string | number | boolean | null | string[] | Record<string, string>;

export interface VerbRequest {
  verb: string;
  /**
   * The verb's own arguments (a new child's title and column, a parent to move
   * under …). Add child names what it waits on as `depends_on: [ref]` with
   * `depends_why: {ref: reason}`, as the server's projects op does.
   */
  args: Record<string, VerbArg>;
  /** The page's `version`: a write based on older facts is refused (409 stale). */
  based_on: string;
}

/** One line of a preview, in words: "Burn" · "about +$19/h while its team works". */
export interface PreviewLine {
  label: string;
  /** Limited markup, as `why_html` (see `RichText`). */
  html: string;
}

/** A verb's dry run (`POST …/n/{ref}/preview`): what would change, and who approves. Nothing changed yet. */
export interface VerbPreview {
  preview_id: string;
  /** The heading ("Start assistant-panel-ux now"). (client) */
  title?: string;
  /** The parent's children after the change; the changed node is a ghost (`state: "ghost"`). */
  columns_after?: ChildPage;
  /** Where the changed node sits now: struck through there in the mini columns. (client) */
  moved_from?: { ref: string; col: number | "later" };
  /** How the roll-ups change, line by line (api.md's object form is read too, see `nodeWire`). */
  rollup_delta: PreviewLine[];
  /** What else changes: the wait, the order, the team, the burn, what it unlocks, the risk. */
  effects: PreviewLine[];
  /** Who approves: "you", "owner", "atlas" … */
  approver: string;
  /** Who approves, in words ("You, as owner. Applies now; Undo for 10 min."). (client) */
  approver_note?: string;
  warnings: string[];
  /** The held child a new child's title matched: adding pulls it forward instead of copying it. (client) */
  pulls_forward?: { ref: string; title: string; held_for?: string; note?: string };
  /** Parents one level up this node may go under (Move, Name it / file under). (client) */
  parents?: NodeLink[];
  /** The apply button's words ("Pull forward and plan"). (client) */
  apply_label?: string;
  /** False when this viewer may only look (an owner's verb for an editor; a form's first read). */
  can_apply?: boolean;
  /** Who may apply it, in words ("an owner"). */
  who_may?: string;
}

/** What `POST …/n/{ref}/apply` returns: the page after, and the Undo for 10 minutes. */
export interface VerbApplied {
  result?: NodePage;
  undo?: { token: string; expires_at: string };
  /** The proposal filed for the record ("C-19"). */
  filed?: string;
  /** What happened, in one line (the toast). (client) */
  message?: string;
}

// ── Sheets: Needs you, Find (api.md §3.8) ───────────────────────

/** One ask in Needs you: its breadcrumb, what it holds, its weight (the rank). */
export interface NeedsYouItem {
  decision: DecisionRef;
  /** Root first, ending at the node the ask is on. */
  breadcrumb: Crumb[];
  /** What answering it frees ("T-16", "assistant-panel-ux"). */
  holds: string[];
  weight: number;
}

/** An ask that blocks nothing, with the node it is on and its breadcrumb (they are grouped). */
export type QuietAsk = DecisionRef & { node?: NodeLink; breadcrumb?: Crumb[] };

/** `GET /factories/{fid}/needs-you?tree=`: ranked by what each ask holds. */
export interface NeedsYouList {
  items: NeedsYouItem[];
  blocks_nothing: QuietAsk[];
  closed_alarms: ClosedAlarm[];
  /** Asks in the other tree (the Factory's, from the Product). */
  other_tree: number;
}

/** One node `GET /factories/{fid}/find?q=` found: its ref, title and breadcrumb. */
export interface FindHit {
  ref: string;
  title: string;
  level: string;
  level_name?: string;
  state?: NodeState;
  tag?: string;
  /** The ancestors' titles, root first (the root left out). */
  breadcrumb: string[];
}

// ── The team behind the Team verb (sample source; live reads the team editor's routes) ──

/** One member of a node's team, as the Team panel lists it. */
export interface TeamSeat {
  id: string;
  name: string;
  role: string;
  vendor: string;
  model: string | null;
  /** "idle · held", "not started". */
  state?: string;
  cost?: number;
}

/** A node's team with what each member could run instead (the Team panel). */
export interface TeamSheet {
  members: TeamSeat[];
  /** Vendor → its models, for every member. */
  vendors: Record<string, string[]>;
  /** $/h while the team works, now. */
  rate?: number;
}

// ── Lenses: lineage, timeline, money (api.md §3.7) ──────────────

/** A link outside the breadcrumb on a lineage row ("approved by C-11"). */
export interface LineageLink {
  kind: string;
  ref?: string;
  title: string;
  state?: string;
}

/** One row of a lineage: a node with its one line (and a need's evidence, a feature's approval). */
export interface LineageRow extends Crumb {
  line: StatusLine;
  /** At the root: what it builds, in product words. */
  why_html?: string;
  progress?: Progress;
  /** A need's evidence in one phrase ("an ask · no user evidence yet"). */
  evidence?: string;
  links?: LineageLink[];
}

/** `GET …/n/{ref}/lineage`: ancestors top to bottom, the node, the levels below, results. */
export interface LineageView {
  scope: string;
  ancestors: LineageRow[];
  here: LineageRow;
  below: LevelCount[];
  /** Product tree only. */
  results?: Results;
  version: string;
}

/** One stretch of a lane: work, work on a node now done, a wait on another node, a wait on a person. */
export interface TimelineBar {
  from: string;
  to: string;
  kind: "work" | "done" | "wait" | "you";
  label: string;
}

export interface TimelineLane {
  ref: string;
  title: string;
  state: NodeState;
  bars: TimelineBar[];
}

/** `GET …/n/{ref}/timeline`: one lane per child over a window (ISO instants). */
export interface TimelineView {
  scope: string;
  axis: [string, string];
  now: string;
  /** This viewer's last look: shaded from here to now. */
  last_look?: string;
  lanes: TimelineLane[];
  version: string;
}

export interface MoneyRow {
  ref: string;
  title: string;
  tag?: string;
  state: NodeState;
  cost: number;
  budget?: number;
  burn?: number;
}

/** `GET …/n/{ref}/money`: the children's cost against their budgets, most first. */
export interface MoneyView {
  scope: string;
  rows: MoneyRow[];
  total: number;
  budget?: number;
  /** What the node spent that no child holds (its leads and agents). */
  rest?: number;
  version: string;
}

// ── The levels block (`GET /factories/{fid}/levels`) ───────────

export interface LevelInfo {
  key: string;
  name: string;
  plural: string;
  /** `when_any`, `when_many`, `inline`; absent: always. */
  show?: string;
  debug?: boolean;
  /** Hidden in this factory now (no node on it yet). */
  hidden?: boolean;
}

/** Both trees' levels as the definition names them, and the definition version. */
export interface LevelsInfo {
  trees: Record<string, { root: string; name?: string; home?: string; levels: LevelInfo[] }>;
  tiles_max?: number;
  fold_single_child?: string;
  version: number;
}
