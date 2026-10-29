/**
 * A project's dependencies, edited on the Graph. The edit isn't applied
 * here: it becomes a projects proposal (`C-n`) in #factory that anyone
 * allowed applies from its card, so a hold on a project is never silent. A
 * cycle between projects blocks it before it is sent.
 */

import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CircleAlertIcon, Loader2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { proposeProjects } from "@/lib/factoryRoomApi";
import { cn } from "@/lib/utils";
import { controlErrorMessage } from "../controls/controlModel";
import { DialogBody, NoteTextarea, STICKY_DIALOG } from "../formParts";

/** A cycle the edit would make among project `depends_on` (`a → b → a`), or null. */
export function projectCycle(
  snapshot: FactorySnapshot,
  slug: string,
  dependsOn: readonly string[],
): string[] | null {
  const deps = new Map(snapshot.projects.map((p) => [p.slug, p.depends_on ?? []]));
  deps.set(slug, [...dependsOn]);
  const path: string[] = [];
  const onPath = new Set<string>();
  const done = new Set<string>();
  const visit = (key: string): string[] | null => {
    if (onPath.has(key)) return [...path.slice(path.indexOf(key)), key];
    if (done.has(key)) return null;
    onPath.add(key);
    path.push(key);
    for (const dep of deps.get(key) ?? []) {
      const found = visit(dep);
      if (found) return found;
    }
    path.pop();
    onPath.delete(key);
    done.add(key);
    return null;
  };
  return visit(slug);
}

interface DependsRow {
  slug: string;
  title: string | null;
  /** Why a current dependency is listed though it isn't a candidate. */
  note: string | null;
}

/**
 * What `slug` may depend on: the other active work projects, plus whatever it
 * depends on now (closed, integration or gone), so those can be unchecked.
 */
export function dependsRows(snapshot: FactorySnapshot, slug: string): DependsRow[] {
  const current = snapshot.projects.find((p) => p.slug === slug)?.depends_on ?? [];
  const rows: DependsRow[] = [];
  for (const p of snapshot.projects) {
    if (p.slug === slug) continue;
    const status = p.status ?? "active";
    const candidate = status === "active" && p.kind !== "integration";
    if (!candidate && !current.includes(p.slug)) continue;
    rows.push({
      slug: p.slug,
      title: p.title,
      note: candidate ? null : status !== "active" ? status : "integration",
    });
  }
  for (const dep of current) {
    if (!rows.some((r) => r.slug === dep)) rows.push({ slug: dep, title: null, note: "not found" });
  }
  return rows;
}

export function ProjectDependsDialog({
  snapshot,
  slug,
  open,
  onOpenChange,
  onProposed,
}: {
  snapshot: FactorySnapshot;
  slug: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onProposed?: (ref: string) => void;
}) {
  const queryClient = useQueryClient();
  const project = snapshot.projects.find((p) => p.slug === slug);
  const [picked, setPicked] = useState<string[]>(() => [...(project?.depends_on ?? [])]);
  const [why, setWhy] = useState("");
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const candidates = useMemo(() => dependsRows(snapshot, slug), [snapshot, slug]);
  const cycle = useMemo(() => projectCycle(snapshot, slug, picked), [snapshot, slug, picked]);
  const unchanged =
    [...picked].sort().join(",") === [...(project?.depends_on ?? [])].sort().join(",");
  const submit = async () => {
    if (sending || cycle || unchanged) return;
    setSending(true);
    setFailure(null);
    try {
      const res = await proposeProjects(snapshot.factory.id, {
        changes: [{ op: "update", slug, patch: { depends_on: picked } }],
        rationale: why.trim() || `Dependencies of #${slug} edited on the Graph`,
      });
      for (const key of factoryInvalidationKeys(snapshot.factory.id)) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
      onProposed?.(res.proposal.ref);
      onOpenChange(false);
    } catch (error) {
      setFailure(controlErrorMessage(error));
    } finally {
      setSending(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="project-depends-dialog"
        className={`${STICKY_DIALOG} gap-4 sm:max-w-lg`}
      >
        <DialogHeader>
          <DialogTitle>
            <span className="font-mono">#{slug}</span> depends on
          </DialogTitle>
          <DialogDescription>
            Proposed as a change in #factory; it applies when someone applies it there. A project
            waits until the ones it depends on merge into the base.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <ul className="flex flex-col gap-1">
            {candidates.length === 0 && (
              <li className="text-sm text-muted-foreground">No other active projects.</li>
            )}
            {candidates.map((p) => {
              const on = picked.includes(p.slug);
              return (
                <li key={p.slug}>
                  <label
                    className={cn(
                      "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted",
                      on && "bg-muted/60",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={on}
                      data-testid={`project-depends-${p.slug}`}
                      onChange={() =>
                        setPicked((prev) =>
                          on ? prev.filter((s) => s !== p.slug) : [...prev, p.slug],
                        )
                      }
                      className="size-3.5 accent-[var(--primary)]"
                    />
                    <span className="font-mono">#{p.slug}</span>
                    {p.title && (
                      <span className="min-w-0 truncate text-muted-foreground">{p.title}</span>
                    )}
                    {p.note && (
                      <span className="ml-auto shrink-0 rounded-full bg-muted px-1.5 text-xs leading-5 text-muted-foreground">
                        {p.note}
                      </span>
                    )}
                  </label>
                </li>
              );
            })}
          </ul>
          <NoteTextarea
            value={why}
            onChange={(e) => setWhy(e.target.value)}
            placeholder="Why (shown on the proposal)"
            rows={2}
            componentId="factory.graph.depends_why"
          />
          {cycle && (
            <p
              data-testid="project-depends-cycle"
              className="flex items-start gap-1.5 text-sm text-destructive"
            >
              <CircleAlertIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
              That makes a cycle: {cycle.map((s) => `#${s}`).join(" → ")}
            </p>
          )}
          {failure && (
            <p role="alert" className="text-sm text-destructive">
              {failure}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => void submit()}
            disabled={sending || cycle != null || unchanged}
            data-testid="project-depends-submit"
            componentId="factory.graph.depends_propose"
          >
            {sending && <Loader2Icon className="animate-spin" />}
            Propose change
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
