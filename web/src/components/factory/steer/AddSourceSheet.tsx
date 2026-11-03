/**
 * Add a source, in four steps: the kind; its credential (none for the kinds
 * this server reads without a secret; kinds that need a read-only key wait for
 * secret-backed sources and are shown, disabled, with why); the read tools it
 * may use and its parameters; then who may read it, its privacy, budget and
 * retention, ending in a plain-words preview. "Stage this source" stages a
 * definition change: an owner applies it, and it starts with a 14-day trial.
 */

import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { PrivacyLevel, SourceDef, SourceKind } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import { useSteer } from "./SteerContext";
import { slugOf, splitList } from "./steerModel";

/** A kind this cockpit can add, what it reads, and whether it needs a key. */
export interface KindInfo {
  kind: SourceKind;
  label: string;
  reads: string;
  /** Why it cannot be added here yet, or null. */
  blocked: string | null;
  /** Its read tools (all read-only). */
  tools: readonly { name: string; does: string }[];
  privacy: PrivacyLevel;
  keepDays: number;
}

const NEEDS_KEY =
  "Needs a read-only key. Keys are set write-only on the source and kept by the server; that arrives with secret-backed sources.";

export const SOURCE_KINDS: readonly KindInfo[] = [
  {
    kind: "repo",
    label: "Code host issues, discussions and releases",
    reads: "the factory's own repos, or the ones you list",
    blocked: null,
    tools: [
      { name: "issues", does: "issues and their comments" },
      { name: "discussions", does: "discussions" },
      { name: "releases", does: "release notes" },
      { name: "search", does: "search issues and discussions" },
    ],
    privacy: "internal",
    keepDays: 180,
  },
  {
    kind: "web",
    label: "Web search and public pages",
    reads: "public pages and search results, and competitors' changelogs",
    blocked: null,
    tools: [
      { name: "search", does: "a web search" },
      { name: "fetch", does: "one public page, in chunks" },
    ],
    privacy: "public",
    keepDays: 365,
  },
  {
    kind: "team",
    label: "The factory's people",
    reads: "hunches and requests people post in the Room",
    blocked: null,
    tools: [{ name: "recent", does: "the team's recent hunches and requests" }],
    privacy: "internal",
    keepDays: 180,
  },
  {
    kind: "tracker",
    label: "Issue tracker",
    reads: "tickets through read-only tools",
    blocked: NEEDS_KEY,
    tools: [],
    privacy: "internal",
    keepDays: 180,
  },
  {
    kind: "analytics",
    label: "Product analytics (aggregates)",
    reads: "saved aggregate queries: funnels and rates, no event rows",
    blocked: NEEDS_KEY,
    tools: [],
    privacy: "internal",
    keepDays: 180,
  },
  {
    kind: "chat",
    label: "Community chat channels",
    reads: "public channels",
    blocked:
      "Needs name detection first: personal sources stay off until the server can find names in them.",
    tools: [],
    privacy: "personal",
    keepDays: 90,
  },
];

const PRIVACY: readonly PrivacyLevel[] = ["public", "internal"];
const KEEP = [30, 90, 180, 365] as const;
const STEPS = [
  "Pick a kind",
  "Credential",
  "Tools and parameters",
  "Who reads it, privacy and budget",
];

export interface SourceDraft {
  kind: SourceKind;
  label: string;
  tools: string[];
  repos: string;
  sinceDays: number;
  domains: string;
  changelogs: boolean;
  scout: boolean;
  lead: boolean;
  privacy: PrivacyLevel;
  keepDays: number;
  callsPerDay: number;
}

export function draftFor(info: KindInfo): SourceDraft {
  return {
    kind: info.kind,
    label: info.label,
    tools: info.tools.map((t) => t.name),
    repos: "",
    sinceDays: 90,
    domains: "",
    changelogs: true,
    scout: true,
    lead: false,
    privacy: info.privacy,
    keepDays: info.keepDays,
    callsPerDay: info.kind === "web" ? 30 : 100,
  };
}

/** The source a draft stages (`params.tools` only when some tools are left out). */
export function sourceOf(draft: SourceDraft, taken: readonly string[]): Partial<SourceDef> {
  const info = SOURCE_KINDS.find((k) => k.kind === draft.kind)!;
  const params: Record<string, unknown> = {};
  if (draft.tools.length < info.tools.length) params.tools = draft.tools;
  if (draft.kind === "repo") {
    const repos = splitList(draft.repos);
    if (repos.length) params.repos = repos;
    params.since_days = draft.sinceDays;
  }
  if (draft.kind === "web") {
    const domains = splitList(draft.domains);
    if (domains.length) params.domains = domains;
    params.changelogs = draft.changelogs;
  }
  return {
    id: slugOf(draft.kind === "team" ? "team" : draft.label, taken),
    label: draft.label.trim() || info.label,
    kind: draft.kind,
    params,
    roles: [...(draft.scout ? ["scout" as const] : []), ...(draft.lead ? ["lead" as const] : [])],
    privacy: {
      level: draft.privacy,
      redact: ["email", "phone", "token", "ip", "url_secret", "card"],
      quote_max: 280,
      keep_days: draft.keepDays,
    },
    budget: { calls_per_day: draft.callsPerDay },
    interval_min: draft.kind === "repo" ? 360 : 0,
    state: "trial",
  };
}

/** What the source will do, in plain words (step 4 and the change column). */
export function sourceWords(draft: SourceDraft): string {
  const who =
    [draft.scout ? "Scout" : null, draft.lead ? "the leads" : null].filter(Boolean).join(" and ") ||
    "nobody";
  return `${who} can call ${draft.tools.join(", ") || "no tool"} on this source, up to ${draft.callsPerDay} times a day. Emails, phone numbers, tokens and addresses are removed before anyone reads it. Text is kept ${draft.keepDays} days. It starts with a 14-day trial.`;
}

function Radio({
  name,
  checked,
  disabled,
  onChange,
  children,
}: {
  name: string;
  checked: boolean;
  disabled?: boolean;
  onChange: () => void;
  children: React.ReactNode;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-2 rounded-lg border border-border p-2.5 text-sm",
        disabled && "cursor-not-allowed opacity-60",
        checked && "border-status-blue",
      )}
    >
      <input
        type="radio"
        name={name}
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        className={cn("mt-1 accent-status-blue", FOCUS_RING)}
      />
      <span className="min-w-0">{children}</span>
    </label>
  );
}

export function AddSourceSheet({
  open,
  onOpenChange,
  initialStep = 1,
  initialKind = "repo",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Stories open a step directly. */
  initialStep?: 1 | 2 | 3 | 4;
  initialKind?: SourceKind;
}) {
  const steer = useSteer();
  const name = useId();
  const [step, setStep] = useState<number>(initialStep);
  const [draft, setDraft] = useState<SourceDraft>(() =>
    draftFor(SOURCE_KINDS.find((k) => k.kind === initialKind)!),
  );
  const info = SOURCE_KINDS.find((k) => k.kind === draft.kind)!;
  const taken = (steer.effective.discovery?.sources ?? []).map((s) => s.id);
  const set = (patch: Partial<SourceDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const close = () => {
    onOpenChange(false);
    setStep(1);
  };
  const stage = () => {
    const source = sourceOf(draft, taken);
    steer.addItem(
      "sources",
      "discovery.sources",
      `Source: ${source.label}`,
      source as Record<string, unknown>,
      sourceWords(draft),
    );
    close();
  };
  const canNext = step === 1 ? info.blocked == null : step === 3 ? draft.tools.length > 0 : true;
  return (
    <Dialog open={open} onOpenChange={(o) => (o ? onOpenChange(true) : close())}>
      <DialogContent className="sm:max-w-lg" data-testid="steer-add-source" data-step={step}>
        <DialogHeader>
          <DialogTitle>Add a source · step {step} of 4</DialogTitle>
          <DialogDescription>{STEPS[step - 1]}</DialogDescription>
        </DialogHeader>
        <div className="flex gap-1" aria-hidden>
          {[1, 2, 3, 4].map((i) => (
            <span
              key={i}
              className={cn("h-1 flex-1 rounded-full", i <= step ? "bg-status-blue" : "bg-muted")}
            />
          ))}
        </div>
        {step === 1 && (
          <div role="radiogroup" aria-label="Kind" className="flex flex-col gap-1.5">
            {SOURCE_KINDS.map((k) => (
              <Radio
                key={k.kind}
                name={name}
                checked={draft.kind === k.kind}
                disabled={k.blocked != null}
                onChange={() => setDraft(draftFor(k))}
              >
                <b className="font-medium">{k.label}</b>
                <br />
                <span className="text-xs text-muted-foreground">
                  {k.blocked ?? `reads ${k.reads} · no secret`}
                </span>
              </Radio>
            ))}
          </div>
        )}
        {step === 2 && (
          <div className="flex flex-col gap-2 text-sm">
            <p className="rounded-lg bg-muted/60 p-2.5">
              No key needed: the server reads {info.reads} with the factory's own access. Nothing
              secret goes into the definition, and no agent ever holds a key.
            </p>
            <p className="text-muted-foreground">
              Once it is applied, Test on its card makes one call and shows what came back.
            </p>
          </div>
        )}
        {step === 3 && (
          <div className="flex flex-col gap-3 text-sm">
            <fieldset className="flex flex-col gap-1">
              <legend className="mb-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                Read tools (all read-only; none can write)
              </legend>
              {info.tools.map((t) => (
                <label key={t.name} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={draft.tools.includes(t.name)}
                    onChange={(e) =>
                      set({
                        tools: e.target.checked
                          ? [...draft.tools, t.name]
                          : draft.tools.filter((x) => x !== t.name),
                      })
                    }
                    className={cn("accent-status-blue", FOCUS_RING)}
                  />
                  <span className="font-mono">{t.name}</span>
                  <span className="text-muted-foreground">{t.does}</span>
                </label>
              ))}
            </fieldset>
            {draft.kind === "repo" && (
              <>
                <label className="flex flex-col gap-1" htmlFor={`${name}-repos`}>
                  Repos (owner/name; empty reads the factory's own)
                  <Input
                    id={`${name}-repos`}
                    value={draft.repos}
                    placeholder="sample-org/sample-app"
                    onChange={(e) => set({ repos: e.target.value })}
                    componentId="factory.steer.source_repos"
                  />
                </label>
                <label className="flex flex-col gap-1" htmlFor={`${name}-since`}>
                  Last days
                  <Input
                    id={`${name}-since`}
                    value={String(draft.sinceDays)}
                    inputMode="numeric"
                    onChange={(e) =>
                      set({ sinceDays: Math.max(1, Math.min(365, Number(e.target.value) || 90)) })
                    }
                    componentId="factory.steer.source_since"
                    className="w-24"
                  />
                </label>
              </>
            )}
            {draft.kind === "web" && (
              <>
                <label className="flex flex-col gap-1" htmlFor={`${name}-domains`}>
                  Only these domains (empty: any public page)
                  <Input
                    id={`${name}-domains`}
                    value={draft.domains}
                    placeholder="example.com, docs.example.com"
                    onChange={(e) => set({ domains: e.target.value })}
                    componentId="factory.steer.source_domains"
                  />
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={draft.changelogs}
                    onChange={(e) => set({ changelogs: e.target.checked })}
                    className={cn("accent-status-blue", FOCUS_RING)}
                  />
                  Read competitors' changelogs from the product profile
                </label>
              </>
            )}
          </div>
        )}
        {step === 4 && (
          <div className="flex flex-col gap-3 text-sm">
            <label className="flex flex-col gap-1" htmlFor={`${name}-label`}>
              Name
              <Input
                id={`${name}-label`}
                value={draft.label}
                maxLength={80}
                onChange={(e) => set({ label: e.target.value })}
                componentId="factory.steer.source_label"
              />
            </label>
            <fieldset className="flex flex-wrap items-center gap-3">
              <legend className="mb-1 w-full text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                Who may read it
              </legend>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={draft.scout}
                  onChange={(e) => set({ scout: e.target.checked })}
                  className={cn("accent-status-blue", FOCUS_RING)}
                />
                Scout
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={draft.lead}
                  onChange={(e) => set({ lead: e.target.checked })}
                  className={cn("accent-status-blue", FOCUS_RING)}
                />
                Leads
              </label>
            </fieldset>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <label className="flex flex-col gap-1" htmlFor={`${name}-calls`}>
                Calls per day
                <Input
                  id={`${name}-calls`}
                  value={String(draft.callsPerDay)}
                  inputMode="numeric"
                  onChange={(e) =>
                    set({ callsPerDay: Math.max(0, Math.min(10000, Number(e.target.value) || 0)) })
                  }
                  componentId="factory.steer.source_calls"
                />
              </label>
              <label className="flex flex-col gap-1" htmlFor={`${name}-privacy`}>
                Privacy class
                <select
                  id={`${name}-privacy`}
                  value={draft.privacy}
                  onChange={(e) => set({ privacy: e.target.value as PrivacyLevel })}
                  className={cn(
                    "h-8 rounded-md border border-input bg-background px-2",
                    FOCUS_RING,
                  )}
                >
                  {PRIVACY.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1" htmlFor={`${name}-keep`}>
                Keep text for
                <select
                  id={`${name}-keep`}
                  value={draft.keepDays}
                  onChange={(e) => set({ keepDays: Number(e.target.value) })}
                  className={cn(
                    "h-8 rounded-md border border-input bg-background px-2",
                    FOCUS_RING,
                  )}
                >
                  {KEEP.map((d) => (
                    <option key={d} value={d}>
                      {d} days
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                In plain words
              </span>
              <p
                data-testid="steer-add-source-words"
                className={cn("rounded-lg border p-2.5", MEANING.staged.border)}
              >
                {sourceWords(draft)}
              </p>
            </div>
          </div>
        )}
        <DialogFooter className="flex-row items-center">
          {step > 1 && (
            <Button
              type="button"
              variant="ghost"
              onClick={() => setStep(step - 1)}
              componentId="factory.steer.source_back"
            >
              Back
            </Button>
          )}
          <span className="flex-1" />
          {step < 4 ? (
            <Button
              type="button"
              onClick={() => setStep(step + 1)}
              disabled={!canNext}
              data-testid="steer-add-source-next"
              componentId="factory.steer.source_next"
            >
              Next
            </Button>
          ) : (
            <Button
              type="button"
              onClick={stage}
              disabled={!draft.scout && !draft.lead}
              data-testid="steer-add-source-stage"
              componentId="factory.steer.source_stage"
              className="bg-status-violet font-semibold text-[#160f2b] hover:bg-status-violet/85"
            >
              Stage this source
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
