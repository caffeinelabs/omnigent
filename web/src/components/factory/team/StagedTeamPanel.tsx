/**
 * The staged team changes and what they would do, from the server's preview:
 * each change (before → after, its effect in words, whether it is an owner
 * change) with × to drop it; the rate while working and the cost to finish
 * against the budget; who applies it; the definition version it becomes and
 * the TEAM UPDATE the project lead gets; what never rolls back; when it
 * applies (after current tasks, or now); its scope (this project, or the
 * template too); why it cannot run as a trial; then Discard and Apply (or
 * Propose to the owners, when only they can apply it).
 */

import { FlaskConicalIcon, LockIcon, XIcon } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type {
  CatalogResponse,
  DefinitionEffect,
  DefinitionPreviewResponse,
  TeamChangeWhen,
} from "@/lib/factoryApi";
import type { BenchResponse } from "@/lib/factoryTeamsApi";
import { cn } from "@/lib/utils";
import { plural } from "../factoryFormat";
import { FOCUS_RING } from "../ui/statusTokens";
import {
  type StagedTeam,
  type TeamBase,
  type TeamScope,
  type TeamStaging,
  changeWords,
} from "./teamModel";

export interface StagedTeamPanelProps {
  slug: string;
  base: TeamBase;
  staging: TeamStaging;
  team: StagedTeam;
  catalog: CatalogResponse | null | undefined;
  bench: BenchResponse | null | undefined;
  names: ReadonlyMap<string, string>;
  preview: DefinitionPreviewResponse | undefined;
  /** The preview answers for an older set of changes (one is on its way). */
  previewStale: boolean;
  previewError: Error | null;
  onRetryPreview: () => void;
  /** Why nothing can be applied here at all (the editor is read-only), else null. */
  readOnly: string | null;
  busy: boolean;
  onDrop: (index: number) => void;
  onDiscard: () => void;
  onWhen: (when: TeamChangeWhen) => void;
  onScope: (scope: TeamScope) => void;
  onApply: () => void;
  onPropose: (rationale: string) => void;
}

function effect(preview: DefinitionPreviewResponse | undefined, kind: DefinitionEffect["kind"]) {
  return preview?.effects.filter((e) => e.kind === kind) ?? [];
}

function usd(n: number | undefined, digits = 2): string {
  return n == null ? "—" : `$${n.toFixed(digits)}`;
}

function Row({ term, children }: { term: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{term}</dt>
      <dd className="min-w-0">{children}</dd>
    </>
  );
}

function Radio({
  name,
  checked,
  onChange,
  disabled,
  children,
  testId,
}: {
  name: string;
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-2 text-sm",
        disabled && "cursor-not-allowed opacity-60",
      )}
    >
      <input
        type="radio"
        name={name}
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        data-testid={testId}
        className={cn("mt-1 accent-status-violet", FOCUS_RING)}
      />
      <span className="min-w-0">{children}</span>
    </label>
  );
}

export function StagedTeamPanel({
  slug,
  base,
  staging,
  team,
  catalog,
  bench,
  names,
  preview,
  previewStale,
  previewError,
  onRetryPreview,
  readOnly,
  busy,
  onDrop,
  onDiscard,
  onWhen,
  onScope,
  onApply,
  onPropose,
}: StagedTeamPanelProps) {
  const radioName = useId();
  const [rationale, setRationale] = useState("");
  const changes = staging.changes;
  const headingId = `${radioName}-heading`;
  if (!changes.length) {
    return (
      <section
        aria-labelledby={headingId}
        data-testid="staged-team-panel"
        className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4"
      >
        <h2 id={headingId} className="text-base font-semibold">
          Staged changes
        </h2>
        <p className="text-sm text-muted-foreground">
          Pick a vendor and model on any member, add a role with the dashed tiles, or change the
          budget. Each change shows here with its cost, its effect and who must approve, before
          anything applies.
        </p>
        <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
          <LockIcon aria-hidden className="mt-0.5 size-3 shrink-0" />
          <span>
            Some removals are locked because a gate needs the role. Gate policy is Tier 0: it is
            platform code, not a setting.
          </span>
        </p>
        {readOnly && <p className="text-xs text-muted-foreground">{readOnly}</p>}
      </section>
    );
  }
  const fresh = preview && !previewStale ? preview : undefined;
  const rate = effect(preview, "rate")[0];
  const finish = effect(preview, "finish")[0];
  const update = effect(preview, "team_update")[0];
  // Each change already says its restart and review route; "now" adds what it interrupts.
  const restart = staging.when === "now" ? effect(preview, "restart")[0] : undefined;
  const named = (text: string) => text.replace(/\bM-\d+\b/g, (id) => names.get(id) ?? id);
  const blocked = preview?.blocked ?? [];
  const templateLabel =
    catalog?.templates.find((t) => t.id === team.template)?.label ?? team.template;
  const others = catalog?.templates.find((t) => t.id === team.template);
  const canApply = fresh?.you_can_apply === true && !readOnly && blocked.length === 0;
  const propose =
    fresh != null &&
    !fresh.you_can_apply &&
    fresh.you_can_propose &&
    !readOnly &&
    blocked.length === 0;
  const classOf = (path: string | null, kind: string): "A" | "B" | null => {
    if (kind === "add") return "B";
    // A switch or a restore adds and removes members: the change's class is the whole diff's.
    if (kind === "restore" || kind === "template") return preview?.class ?? null;
    if (!path) return null;
    return preview?.diff.find((e) => e.path === path)?.class ?? null;
  };
  return (
    <section
      aria-labelledby={headingId}
      data-testid="staged-team-panel"
      aria-busy={previewStale || undefined}
      className="flex flex-col gap-3 rounded-xl border border-dashed border-status-violet bg-card p-4"
    >
      <header className="flex items-center gap-2">
        <h2 id={headingId} className="grow text-base font-semibold">
          {plural(changes.length, "staged change")}
        </h2>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          componentId="factory.team.discard_all"
          onClick={onDiscard}
        >
          Discard all
        </Button>
      </header>
      {readOnly && (
        <p
          className="rounded-lg bg-muted/60 px-3 py-2 text-xs text-foreground/80"
          data-testid="team-read-only"
        >
          {readOnly}
        </p>
      )}
      <ol className="flex flex-col gap-2">
        {changes.map((change, index) => {
          const words = changeWords(change, {
            slug,
            base,
            catalog,
            bench,
            when: staging.when,
            names,
          });
          const klass = classOf(words.path, change.kind);
          return (
            <li
              // Changes have no ids; their position is what × drops.
              // oxlint-disable-next-line react/no-array-index-key
              key={`${change.kind}-${index}`}
              data-testid={`staged-change-${index}`}
              className="relative rounded-lg border border-dashed border-status-violet/70 bg-status-violet/5 p-3 pr-9"
            >
              <button
                type="button"
                aria-label={`Drop change ${index + 1}: ${words.title}`}
                onClick={() => onDrop(index)}
                className={cn(
                  "absolute top-2 right-2 grid size-6 cursor-pointer place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground",
                  FOCUS_RING,
                )}
              >
                <XIcon aria-hidden className="size-3.5" />
              </button>
              <p className="text-sm font-semibold">
                {index + 1}. {words.title}
              </p>
              {(words.before || words.after) && (
                <p className="mt-0.5 font-mono text-xs">
                  {words.before && (
                    <s className="text-muted-foreground" aria-label={`was ${words.before}`}>
                      {words.before}
                    </s>
                  )}
                  {words.before && words.after && <span aria-hidden> → </span>}
                  {words.after && (
                    <ins className="text-status-green-fg no-underline">{words.after}</ins>
                  )}
                </p>
              )}
              <p className="mt-1 text-sm text-foreground/85">{words.effect}</p>
              {klass && (
                <p className="mt-1 text-xs text-muted-foreground">
                  {klass === "B" ? "owner change" : "editors can apply it"}
                </p>
              )}
            </li>
          );
        })}
      </ol>
      {blocked.length > 0 && (
        <div
          role="alert"
          data-testid="staged-team-blocked"
          className="flex flex-col gap-1 rounded-lg border border-status-red/55 bg-status-red/10 p-3 text-sm"
        >
          <p className="font-semibold">This cannot apply as staged</p>
          {blocked.map((b) => (
            <p key={`${b.code}-${b.path}-${b.message}`} className="flex items-start gap-1.5">
              <LockIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
              <span>{b.message}</span>
            </p>
          ))}
        </div>
      )}
      {previewError && !preview ? (
        <div className="rounded-lg border border-border p-3 text-sm" role="alert">
          <p>The server could not preview these changes: {previewError.message}</p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-2"
            componentId="factory.team.preview_retry"
            onClick={onRetryPreview}
          >
            Try again
          </Button>
        </div>
      ) : !preview ? (
        <div aria-busy="true" className="h-28 animate-pulse rounded-lg bg-muted" />
      ) : (
        <dl
          data-testid="staged-team-effects"
          className={cn(
            "grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-sm",
            previewStale && "opacity-60",
          )}
        >
          {rate && (
            <Row term="While working">
              <span className="font-mono">
                {usd(rate.before_usd_h)}/h → {usd(rate.after_usd_h)}/h
              </span>
            </Row>
          )}
          {finish && (
            <Row term="To finish">
              <span className="font-mono">
                {usd(finish.before_usd, 0)} → {usd(finish.after_usd, 0)} of{" "}
                {usd(finish.budget_usd, 0)}
              </span>
            </Row>
          )}
          <Row term="Who applies">{preview.who}</Row>
          <Row term="Version">
            Becomes definition v{preview.version_next}. {update ? named(update.text) : ""}
          </Row>
          {restart && <Row term="Restarts">{named(restart.text)}</Row>}
          <Row term="Does not undo">Work already pushed, money already spent.</Row>
        </dl>
      )}
      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1 text-xs font-semibold text-muted-foreground">When</legend>
        <Radio
          name={`${radioName}-when`}
          checked={staging.when === "after_current"}
          onChange={() => onWhen("after_current")}
          testId="team-when-after"
        >
          <b className="font-semibold">After current tasks</b>{" "}
          <span className="text-muted-foreground">(recommended: nothing in flight is lost)</span>
        </Radio>
        <Radio
          name={`${radioName}-when`}
          checked={staging.when === "now"}
          onChange={() => onWhen("now")}
          testId="team-when-now"
        >
          <b className="font-semibold">Now</b>{" "}
          <span className="text-muted-foreground">
            interrupts the affected members; about 20 minutes of work restarts
          </span>
        </Radio>
      </fieldset>
      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1 text-xs font-semibold text-muted-foreground">Scope</legend>
        <Radio
          name={`${radioName}-scope`}
          checked={staging.scope === "project"}
          onChange={() => onScope("project")}
        >
          This project only
        </Radio>
        <Radio
          name={`${radioName}-scope`}
          checked={staging.scope === "template"}
          onChange={() => onScope("template")}
          disabled={!team.template}
          testId="team-scope-template"
        >
          {team.template ? (
            <>
              Also update the {templateLabel} template{" "}
              <span className="text-muted-foreground">
                (new projects that pick it; projects that already use it keep their team)
              </span>
            </>
          ) : (
            <>
              Also update a template{" "}
              <span className="text-muted-foreground">
                (this project follows today&apos;s bundle, not a template)
              </span>
            </>
          )}
        </Radio>
        {others && staging.scope === "template" && (
          <p className="text-xs text-muted-foreground">Writing a template is an owner change.</p>
        )}
      </fieldset>
      {preview && preview.trialable.code === "not_trialable" && (
        <p
          className="flex items-start gap-1.5 rounded-lg bg-muted/60 px-3 py-2 text-xs text-foreground/80"
          data-testid="team-no-trial"
        >
          <FlaskConicalIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          <span>
            <b className="font-semibold text-foreground">No trial for one project.</b>{" "}
            {preview.trialable.reason ? sentenceCase(preview.trialable.reason) : null}
          </span>
        </p>
      )}
      {preview && preview.lines.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">The exact change</summary>
          <pre className="mt-1 overflow-x-auto rounded-md bg-muted p-2 font-mono whitespace-pre-wrap">
            {preview.lines.join("\n")}
          </pre>
        </details>
      )}
      {propose && (
        <div className="flex flex-col gap-1.5">
          <label htmlFor={`${radioName}-why`} className="text-xs font-semibold">
            Why (the owners read it)
          </label>
          <Textarea
            id={`${radioName}-why`}
            value={rationale}
            maxLength={2000}
            onChange={(e) => setRationale(e.target.value)}
            placeholder="What this change is for"
            className="min-h-16"
          />
        </div>
      )}
      <footer className="flex items-center gap-2 pt-1">
        <Button
          type="button"
          variant="ghost"
          componentId="factory.team.discard"
          onClick={onDiscard}
        >
          Discard
        </Button>
        <span className="grow" />
        {propose ? (
          <Button
            type="button"
            componentId="factory.team.propose"
            disabled={busy || blocked.length > 0 || !rationale.trim()}
            loading={busy}
            onClick={() => onPropose(rationale.trim())}
          >
            Propose to owners
          </Button>
        ) : (
          <Button
            type="button"
            data-testid="team-apply"
            componentId="factory.team.apply"
            disabled={!canApply || busy}
            loading={busy}
            onClick={onApply}
            className="bg-status-violet text-[#160f2b] hover:bg-status-violet/85"
          >
            Apply {plural(changes.length, "change")}
          </Button>
        )}
      </footer>
    </section>
  );
}

function sentenceCase(text: string): string {
  const t = text.trim();
  const s = t.charAt(0).toUpperCase() + t.slice(1);
  return /[.!?]$/.test(s) ? s : `${s}.`;
}
