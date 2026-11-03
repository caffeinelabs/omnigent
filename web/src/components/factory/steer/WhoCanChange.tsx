/**
 * "Who can change this": the approval class in words, whether the viewer's
 * Apply applies it or it waits for an owner, that the version in force can be
 * restored any time, and that agents can only propose it. The server's
 * preview words it; before the preview answers, the viewer's role does.
 */

import { UsersIcon } from "lucide-react";
import type { DefinitionPreviewResponse } from "@/lib/factoryApi";

export function whoText(
  preview: DefinitionPreviewResponse | null | undefined,
  { owner, version }: { owner: boolean; version: number },
): string {
  const restore = ` Restore v${version} any time.`;
  const agents = " Agents can only propose it.";
  if (preview?.who) {
    return preview.who.endsWith(agents)
      ? `${preview.who.slice(0, -agents.length)}${restore}${agents}`
      : `${preview.who}${restore}`;
  }
  return owner
    ? `Owners (class B). You are an owner, so it applies when you press Apply.${restore} Agents can only propose it.`
    : `Owners (class B). It waits for an owner: propose it to them.${restore}`;
}

export function WhoCanChange({
  preview,
  owner,
  version,
}: {
  preview: DefinitionPreviewResponse | null | undefined;
  owner: boolean;
  version: number;
}) {
  return (
    <p
      data-testid="steer-who"
      className="flex items-start gap-2 rounded-lg bg-muted/60 px-2.5 py-2 text-sm"
    >
      <UsersIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
      <span>
        <b className="font-semibold">Who can change this:</b> {whoText(preview, { owner, version })}
      </span>
    </p>
  );
}
