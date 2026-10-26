/**
 * The review drawer (`?review=<pr number>`): everything a human needs to
 * review one PR.
 *
 * Placeholder until the review package lands: the PR's raw facts (head,
 * gates, preview URLs, demo steps and files) and links to GitHub. Its
 * replacement keeps these props.
 */

import type { ReactNode } from "react";
import { ExternalLinkIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { DemoView, PrView } from "@/lib/factoryApi";
import { demoFileUrl } from "@/lib/factoryApi";
import { GATE_KEY_LABEL, shownGates } from "../board/GateChip";
import { mergeUrl } from "../board/PrRow";
import { shortSha } from "../factoryFormat";
import { ReadyForMergeBanner } from "./ReadyForMergeBanner";

export interface ReviewDrawerProps {
  /** The factory (root session) id. */
  factoryId: string;
  /** The PR under review; `null` when `?review=` names a PR this factory doesn't track. */
  pr: PrView | null;
  /** The demo to show (normally `pr.demo`). */
  demo: DemoView | null;
  /** Close the drawer (clears `?review=`). */
  onClose: () => void;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-sm font-medium text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

export function ReviewDrawer({ pr, demo, onClose }: ReviewDrawerProps) {
  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent size="xl" data-testid="review-drawer">
        <SheetHeader>
          <SheetTitle>{pr ? `Review · PR #${pr.number}` : "Review"}</SheetTitle>
          <SheetDescription className="truncate">
            {pr ? pr.title : "This PR isn’t tracked by this factory."}
          </SheetDescription>
        </SheetHeader>
        {pr && (
          <SheetBody className="flex flex-col gap-5">
            <ReadyForMergeBanner pr={pr} />
            <Section title="Head">
              <p className="text-sm">
                <span className="font-mono">{pr.head_ref}</span> @{" "}
                <span className="font-mono">{shortSha(pr.head_sha)}</span> · +{pr.additions} −
                {pr.deletions} · {pr.changed_files} files
              </p>
            </Section>
            <Section title="Gates">
              <ul className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-sm sm:grid-cols-4">
                {shownGates(pr).map((gate) => (
                  <li key={gate}>
                    {GATE_KEY_LABEL[gate]}:{" "}
                    <span className="font-mono">
                      {gate === "demo" ? pr.gates.demo : pr.gates[gate]}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
            {pr.preview && (
              <Section title={`Preview · ${pr.preview.state}`}>
                <ul className="text-sm">
                  {(["app", "api", "minio"] as const).map((key) =>
                    pr.preview?.urls[key] ? (
                      <li key={key} className="truncate font-mono">
                        {key}: {pr.preview.urls[key]}
                      </li>
                    ) : null,
                  )}
                </ul>
              </Section>
            )}
            {demo ? (
              <Section title={`Demo · ${demo.status} at ${shortSha(demo.head_sha)}`}>
                <p className="text-sm">{demo.summary}</p>
                <ol className="list-decimal pl-5 text-sm">
                  {demo.steps.map((step) => (
                    <li key={step.n}>
                      {step.title} · {step.status}
                      {step.note ? ` (${step.note})` : ""}
                    </li>
                  ))}
                </ol>
                <ul className="flex flex-wrap gap-x-3 text-sm">
                  {demo.files.map((file) => (
                    <li key={file.id}>
                      <a
                        href={file.url || demoFileUrl(demo.id, file.id)}
                        target="_blank"
                        rel="noreferrer"
                        className="underline-offset-2 hover:underline"
                      >
                        {file.name}
                      </a>
                    </li>
                  ))}
                </ul>
              </Section>
            ) : (
              <Section title="Demo">
                <p className="text-sm text-muted-foreground">No demo yet.</p>
              </Section>
            )}
          </SheetBody>
        )}
        {pr && (
          <SheetFooter>
            <Button asChild variant="outline" size="sm">
              <a href={pr.url} target="_blank" rel="noreferrer">
                Open PR
                <ExternalLinkIcon className="size-3.5" />
              </a>
            </Button>
            {pr.ready && (
              <Button asChild size="sm">
                <a href={mergeUrl(pr)} target="_blank" rel="noreferrer">
                  Merge on GitHub
                  <ExternalLinkIcon className="size-3.5" />
                </a>
              </Button>
            )}
          </SheetFooter>
        )}
      </SheetContent>
    </Sheet>
  );
}
