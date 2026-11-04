// A recorded demo in the node chat (calm rule 10: conversation, never
// Activity). Its poster plays it in place under the card, like the page's proof
// row, so watching never leaves the cockpit; "Watch on the PR page" stays as the
// second way. Its head reads "Demo #9876 PASS"; the commit is in its tooltip.

import { useState } from "react";
import { ArrowRightIcon, ClapperboardIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DemoView, PrView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { DemoStatusTag, DemoThumb } from "../../board/DemoThumb";
import { stepsLine } from "../../cards/DemoCard";
import { useRoomCardData } from "../../cards/RoomCardContext";
import { prPageHref } from "../../factoryRoutes";
import { DemoPlayer } from "./ProofRow";

export function ChatDemoCard({ pr, demo }: { pr: PrView; demo: DemoView }) {
  const { factoryId } = useRoomCardData();
  const [playing, setPlaying] = useState(false);
  const href = prPageHref(factoryId, pr.id);
  const steps = stepsLine(demo);
  const sha = demo.head_sha.slice(0, 7);
  return (
    <article
      data-testid={`demo-card-${pr.number}`}
      data-status={demo.status}
      data-playing={playing ? "" : undefined}
      className="@container flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-card p-3 text-sm"
    >
      <div className="flex min-w-0 flex-col gap-3 @sm:flex-row">
        <DemoThumb
          prNumber={pr.number}
          demo={demo}
          onOpen={() => setPlaying((on) => !on)}
          className="w-full max-w-60 @sm:w-32"
        />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <p
            className="flex min-w-0 flex-wrap items-center gap-1.5"
            title={sha ? `Recorded at commit ${sha}` : undefined}
          >
            <ClapperboardIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="font-medium">Demo</span>
            <span className="font-mono text-[12.5px]">#{pr.number}</span>
            <DemoStatusTag status={demo.status} />
            {!demo.is_current_head && (
              <span className="text-xs text-muted-foreground">earlier commit</span>
            )}
          </p>
          <p className="line-clamp-2 break-words" title={demo.summary || demo.title}>
            {demo.title || demo.summary}
          </p>
          {steps && <p className="text-muted-foreground">{steps}</p>}
          <Button
            asChild
            size="xs"
            variant="ghost"
            className="-ml-2 self-start text-muted-foreground"
          >
            <Link
              to={href}
              componentId="factory.v3.chat.demo_watch"
              data-testid={`demo-card-watch-${pr.number}`}
            >
              Watch on the PR page
              <ArrowRightIcon className="size-3.5" />
            </Link>
          </Button>
        </div>
      </div>
      {playing && <DemoPlayer view={demo} testId={`demo-card-player-${pr.number}`} />}
    </article>
  );
}
