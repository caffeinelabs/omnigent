/**
 * "Ready for human merge" banner for a PR whose gates all pass.
 *
 * Placeholder until the review package lands: one line with the gate count
 * and Sentinel's verdict. Its replacement keeps these props.
 */

import { StarIcon } from "lucide-react";
import type { PrView } from "@/lib/factoryApi";
import { READY_GATES } from "@/lib/factoryApi";
import { useFactoryNow } from "../factoryClock";
import { ageText } from "../board/boardModel";

export interface ReadyForMergeBannerProps {
  /** The PR; renders nothing unless it is open and ready. */
  pr: PrView;
}

export function ReadyForMergeBanner({ pr }: ReadyForMergeBannerProps) {
  const nowS = Math.floor(useFactoryNow().getTime() / 1000);
  if (pr.state !== "open" || !pr.ready) return null;
  const gates = [...READY_GATES, ...(pr.gates.demo != null ? (["demo"] as const) : [])];
  const passing = gates.filter((gate) => pr.gates[gate] === "pass" || pr.gates[gate] === "skip");
  const sentinel = pr.sentinel?.verdict === "verified" && pr.sentinel.head_sha === pr.head_sha;
  return (
    <p
      data-testid={`ready-banner-${pr.number}`}
      className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 rounded-lg bg-success/10 px-3 py-1.5 text-sm text-success"
    >
      <StarIcon aria-hidden className="size-3.5 fill-current" />
      <span className="font-medium">Ready for human merge</span>
      <span className="tabular-nums">
        · {passing.length}/{gates.length} gates
      </span>
      {sentinel && pr.sentinel?.at != null && (
        <span>· Sentinel verified {ageText(nowS - pr.sentinel.at)}</span>
      )}
    </p>
  );
}
