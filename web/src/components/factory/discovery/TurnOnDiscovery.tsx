/**
 * Discovery while it is off: what it does, what Scout would read and what it
 * costs, and what an owner must approve. On a server that runs discovery an
 * owner reviews the discovery starter (the switch and three sources that need
 * no secret) as a staged change and applies it as a new definition version,
 * with Undo. The repos and the web reach outside the factory, so the owner
 * ticks each one Scout may read now; one left unticked is added waiting for
 * an owner (nothing reads it) and shows in Needs you until it is approved.
 * Everyone else reads who turns it on; nothing is ever on by default.
 */

import { CompassIcon, Loader2Icon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  type DefinitionPreviewResponse,
  type DefinitionResponse,
  pinFactoryDefinition,
  previewFactoryDefinition,
  putFactoryDefinition,
} from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { useFactoryToast } from "../ui/Toast";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import { cadenceWord, starterChanges, starterSummary, starterWaiting } from "./discoveryModel";
import { useRefreshDiscovery } from "./discoveryQueries";
import { STARTER_NEEDS_APPROVAL, type StarterSourceId } from "./discoveryStarter";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function TurnOnDiscovery({
  factoryId,
  definition,
  serverOn,
  isOwner,
}: {
  factoryId: string;
  definition: DefinitionResponse | null | undefined;
  /** This server runs discovery (`OMNIGENT_FACTORY_DISCOVERY`). */
  serverOn: boolean;
  /** The viewer owns the factory (turning discovery on is an owner's change). */
  isOwner: boolean;
}) {
  const toast = useFactoryToast();
  const refresh = useRefreshDiscovery(factoryId);
  const effective = definition?.effective ?? null;
  const summary = starterSummary(effective);
  // The sources the owner ticked: approved in the same apply, the rest wait for an owner.
  const [approved, setApproved] = useState<ReadonlySet<string>>(() => new Set());
  const changes = starterChanges(effective, approved);
  const waiting = starterWaiting(effective, approved);
  const [preview, setPreview] = useState<DefinitionPreviewResponse | null>(null);
  const canApprove = serverOn && isOwner;
  const toggle = (id: string, on: boolean) => {
    setPreview(null);
    setApproved((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  };
  const [busy, setBusy] = useState<"preview" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cost = summary.usdPerDay != null ? `$${summary.usdPerDay} a day` : "its daily budget";

  const review = async () => {
    setBusy("preview");
    setError(null);
    try {
      setPreview(
        await previewFactoryDefinition(factoryId, {
          changes,
          base_version: definition?.def_version,
        }),
      );
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(null);
    }
  };

  const apply = async () => {
    const before = definition?.def_version;
    setBusy("apply");
    setError(null);
    try {
      const result = await putFactoryDefinition(factoryId, {
        changes,
        note: "Turn on discovery with the starter",
        base_version: before,
      });
      setPreview(null);
      refresh();
      const waits = waiting.map((s) => s.label).join(" and ");
      toast.show({
        message: `Discovery is on${result.def_version != null ? ` (definition v${result.def_version})` : ""}.${waits ? ` ${waits} ${waiting.length === 1 ? "waits" : "wait"} for your approval in Needs you.` : ""}`,
        undo:
          before != null
            ? () => {
                pinFactoryDefinition(factoryId, before).then(
                  () => {
                    refresh();
                    toast.show({ message: `Discovery is off again (definition v${before}).` });
                  },
                  (e: unknown) => toast.show({ message: `Could not undo: ${message(e)}` }),
                );
              }
            : undefined,
      });
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section
      aria-labelledby="discovery-off-h"
      data-testid="discovery-off"
      data-server={serverOn ? "on" : "off"}
      className="flex min-w-0 flex-col gap-4 rounded-xl border border-border bg-card p-5"
    >
      <div className="flex items-start gap-3">
        <CompassIcon aria-hidden className="mt-0.5 size-6 shrink-0 text-muted-foreground" />
        <div className="flex min-w-0 flex-col gap-1">
          <h2 id="discovery-off-h" className="text-lg font-semibold">
            {serverOn ? "Turn on discovery" : "Discovery is off on this server"}
          </h2>
          <p className="text-sm text-muted-foreground">
            With discovery on, the factory reads where its users talk about the product, groups what
            it hears into opportunities, scores them with the evidence shown, and proposes what to
            build. People steer it with hunches, votes, pins and the strategy, and a person approves
            every bet before work starts.
          </p>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <section className="flex flex-col gap-1.5">
          <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            What Scout would read
          </h3>
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm">
            {summary.reads.map((r) =>
              r.added && STARTER_NEEDS_APPROVAL.has(r.id as StarterSourceId) ? (
                <li key={r.id} data-testid={`discovery-starter-source-${r.id}`}>
                  <label className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      checked={approved.has(r.id)}
                      disabled={!canApprove || busy != null}
                      onChange={(e) => toggle(r.id, e.target.checked)}
                      aria-describedby={`discovery-starter-${r.id}-state`}
                      data-testid={`discovery-starter-approve-${r.id}`}
                      className={cn("mt-1 accent-status-blue", FOCUS_RING)}
                    />
                    <span>
                      <b>{r.label}</b>: {r.text}.{" "}
                      <span
                        id={`discovery-starter-${r.id}-state`}
                        className={cn(
                          "text-xs",
                          approved.has(r.id) ? "text-muted-foreground" : MEANING.needsYou.text,
                        )}
                      >
                        {approved.has(r.id)
                          ? "Approved: Scout reads it from the start."
                          : "Not approved: added waiting for an owner; nothing reads it."}
                      </span>
                    </span>
                  </label>
                </li>
              ) : (
                <li key={r.id}>
                  <b>{r.label}</b>: {r.text}.
                </li>
              ),
            )}
          </ul>
          <p className="text-sm text-muted-foreground">
            None of these needs a secret. Tick each outside source Scout may read now. Text is
            redacted before anyone reads it, and signal text is treated as untrusted data.
          </p>
        </section>
        <section className="flex flex-col gap-1.5">
          <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            What it costs and how often it runs
          </h3>
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm">
            <li>Scout, the discovery lead, spends at most {cost} (editable in Steer).</li>
            <li>
              A {cadenceWord(summary.sweepH) ?? "daily"} sweep labels new signals; a{" "}
              {cadenceWord(summary.synthesisH) ?? "weekly"} synthesis groups them into
              opportunities.
            </li>
            <li>
              Asking Scout to research one opportunity costs up to ${summary.perAsk ?? 2} per ask.
            </li>
            <li>Every bet is measured on day 7 and day 28 after release.</li>
          </ul>
        </section>
      </div>

      <section
        className="flex flex-col gap-1.5 rounded-lg border border-border px-3 py-2.5"
        aria-label="What an owner approves"
      >
        <h3 className="text-sm font-semibold">What an owner approves</h3>
        <ul className="m-0 flex list-disc flex-col gap-1 pl-5 text-sm">
          <li>
            Turning discovery on is an owner's change: one new definition version, with Undo for 10
            minutes and restore at any time.
          </li>
          <li>
            Every source needs an owner. The repos and the web start in a 14-day trial once you tick
            them; one you leave unticked waits in Needs you for an owner, and nothing reads it until
            then. A source that needs a read-only key (product analytics, a tracker) is approved one
            at a time later.
          </li>
          <li>
            Pause any source at once, or turn discovery off again: Scout stops and sources pause.
          </li>
        </ul>
      </section>

      {!serverOn ? (
        <p data-testid="discovery-off-server" className="text-sm text-muted-foreground">
          This server does not run discovery yet, so the factory gathers no evidence and proposes
          nothing from it, whatever its definition says. Whoever runs the server switches it on;
          then an owner of this factory turns it on here.
        </p>
      ) : !isOwner ? (
        <p data-testid="discovery-off-owner" className="text-sm text-muted-foreground">
          Only an owner of this factory can turn discovery on. Ask an owner to turn it on here or in
          Steer.
        </p>
      ) : preview ? (
        <section
          data-testid="discovery-starter-preview"
          className={cn("flex flex-col gap-2 rounded-lg border p-3", MEANING.staged.border)}
        >
          <h3 className="text-sm font-semibold">Staged: definition v{preview.version_next}</h3>
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0 font-mono text-xs">
            {(preview.lines.length ? preview.lines : preview.diff.map((d) => d.line)).map(
              (line) => (
                <li key={line} className="break-words">
                  {line}
                </li>
              ),
            )}
          </ul>
          {preview.effects.length > 0 && (
            <ul className="m-0 flex list-disc flex-col gap-0.5 pl-5 text-sm">
              {preview.effects.map((e) => (
                <li key={`${e.kind}:${e.text}`}>{e.text}</li>
              ))}
            </ul>
          )}
          {waiting.length > 0 && (
            <p
              data-testid="discovery-starter-waiting"
              className={cn("text-sm", MEANING.needsYou.text)}
            >
              Waits for an owner's approval: {waiting.map((s) => s.label).join(", ")}. Nothing reads{" "}
              {waiting.length === 1 ? "it" : "them"} until an owner approves in Needs you.
            </p>
          )}
          <p className="text-sm text-muted-foreground">{preview.who}</p>
          {preview.blocked.length > 0 && (
            <p role="alert" className={cn("text-sm", MEANING.broken.text)}>
              {preview.blocked.map((b) => b.message).join(" ")}
            </p>
          )}
          <div className="flex flex-wrap gap-1.5">
            <Button
              size="sm"
              disabled={!preview.you_can_apply || preview.blocked.length > 0 || busy != null}
              onClick={() => void apply()}
              data-testid="discovery-starter-apply"
            >
              {busy === "apply" && <Loader2Icon className="animate-spin" />}
              Turn on discovery
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setPreview(null)}>
              Discard
            </Button>
          </div>
        </section>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            onClick={() => void review()}
            disabled={busy != null || changes.length === 0}
            data-testid="discovery-starter-review"
          >
            {busy === "preview" && <Loader2Icon className="animate-spin" />}
            Review the starter
          </Button>
          <span className="text-sm text-muted-foreground">
            Shows the change and who applies it before anything happens.
          </span>
        </div>
      )}
      {error && (
        <p role="alert" className={cn("text-sm", MEANING.broken.text)}>
          {error}
        </p>
      )}
    </section>
  );
}
