// The one "Needs you" list: `GET /v1/factory/factories/<id>/asks`.
//
// The types mirror `omnigent/server/factory/asks.py` field for field (snake
// case wire shape). One row per subject, sorted by the cost of waiting: ready
// PRs fold into one merge row, an incident folds into the row of what it is
// about, incidents the factory heals itself are `handled` sentences. Times
// are epoch seconds; every `href` is a cockpit path (`/factory/<id>/…`).

import { factoryErrorFromResponse } from "./factoryApi";
import { authenticatedFetch } from "./identity";

export type AskKind =
  "decision" | "merge" | "bet" | "source" | "readout" | "broken" | "profile" | "goal" | "proposal";

/** One answer a person can give in place (a decision's option). */
export interface AskOption {
  id: string;
  label: string;
  recommended: boolean;
  description?: string | null;
  /** A link-only option: it opens a page instead of answering. */
  href?: string | null;
  /** The answer must name one of these (a required picker). */
  reasons?: string[] | null;
}

/**
 * The row's one main button: `answer` sends `option_id` to the decision,
 * `open` goes to `href`, `peek` opens Home's side panel `peek` (e.g. `merge`).
 */
export interface AskAction {
  kind: "answer" | "open" | "peek";
  label: string;
  href?: string | null;
  option_id?: string | null;
  peek?: string | null;
}

/** One sub-item of a row (a ready PR of a merge row). */
export interface AskItem {
  ref: string;
  /** What users get from it (its project's outcome), else its title. */
  title: string;
  project?: string | null;
  pr_id?: string | null;
  url?: string | null;
  since?: number | null;
  href: string;
}

/** What waiting costs, by fixed rules: a score, its level and why in words. */
export interface AskCost {
  score: number;
  level: "high" | "medium" | "low";
  text: string;
  holds: number;
}

/** One thing that waits on a person. */
export interface Ask {
  /** `D-41`, `C-31`, `INC-26`, `merge`, `merge:<name>`. */
  id: string;
  kind: AskKind;
  subject: string;
  /** The small id link after the title, when it has one. */
  ref?: string | null;
  title: string;
  context?: string | null;
  meta: string[];
  since: number;
  age_s: number;
  cost_of_waiting: AskCost;
  primary_action: AskAction;
  /** Answers given in place (decisions only). */
  options?: AskOption[] | null;
  allow_text: boolean;
  /** At most 5; `more_count` the rest. */
  items: AskItem[];
  more_count: number;
  /** `you`, `operators`, `owners` or a person's short name. */
  assignee: string;
  /** Assigned to the viewer by name. */
  mine: boolean;
  project?: string | null;
  decision_id?: string | null;
  deadline_at?: number | null;
  deadline_effect?: string | null;
  /** Incidents folded into this row (`INC-23`). */
  folded: string[];
  covers: string[];
  critical: boolean;
  /** The opportunity a bet row comes from (`O-15`). */
  opportunity?: string | null;
  href: string;
}

export interface AskCounts {
  /** Rows that need the viewer: the one Needs you count. */
  total: number;
  mine: number;
  /** Rows that wait on other people. */
  others: number;
  /** Agents' definition proposals that wait in Steer instead. */
  in_steer: number;
  folded: number;
}

export interface AsksResponse {
  factory_id: string;
  generated_at: number;
  asks: Ask[];
  /** "Handled by the factory" sentences. */
  handled: string[];
  counts: AskCounts;
}

export async function fetchFactoryAsks(
  factoryId: string,
  signal?: AbortSignal,
): Promise<AsksResponse> {
  const resp = await authenticatedFetch(
    `/v1/factory/factories/${encodeURIComponent(factoryId)}/asks`,
    { signal },
  );
  if (!resp.ok) throw await factoryErrorFromResponse(resp);
  return (await resp.json()) as AsksResponse;
}

/**
 * Send a decision's answer. `keepalive` lets it finish while the page goes
 * away (an answer whose Undo window was still open when the tab closed).
 */
export async function sendAskAnswer(
  decisionId: string,
  answer: { option_id?: string; text?: string },
  { keepalive = false }: { keepalive?: boolean } = {},
): Promise<void> {
  const resp = await authenticatedFetch(
    `/v1/factory/decisions/${encodeURIComponent(decisionId)}/answer`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(answer),
      keepalive,
    },
  );
  if (!resp.ok) throw await factoryErrorFromResponse(resp);
}
