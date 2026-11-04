// Story-only kit for cockpit v3: a node page (or one part of it) over the
// sample factory, routed so links move between node pages, in light or dark.

import { type ReactNode, useState } from "react";
import { Route, Routes } from "react-router-dom";
import { DocumentTheme } from "../../board/boardStoryKit";
import { LIVE_RUN_FACTORY_ID } from "../../__fixtures__/liveRun";
import { FactoryStoryFrame } from "../../factoryStoryFixtures";
import { FactoryToastProvider } from "../../ui/Toast";
import { NodeContextProvider } from "../NodeContext";
import { NodePage } from "../NodePage";
import { nodeHref } from "../nodeRoutes";
import { NodeSourceProvider } from "../nodeSource";
import { V3Sheets } from "../sheets/V3Sheets";
import { useParams } from "@/lib/routing";
import { type SampleOptions, sampleNodeSource } from "./sampleNodeSource";

export const STORY_FID = LIVE_RUN_FACTORY_ID;

export interface V3StoryOptions extends SampleOptions {
  theme?: "light" | "dark";
  /** The node the story opens on. */
  nodeRef?: string;
  level?: string;
  /** The address's query (`?verb=add&title=…`, `?peek=needs`). */
  search?: string;
}

function RoutedNodePage() {
  const { ref = "product", level } = useParams<{ ref: string; level?: string }>();
  return (
    <div className="h-full overflow-y-auto px-4 pt-1 pb-10 md:px-6">
      <NodePage
        key={`${ref}/${level ?? ""}`}
        factoryId={STORY_FID}
        nodeRef={ref}
        level={level ?? null}
      />
    </div>
  );
}

/** A full node page over the sample (links move between pages). */
export function V3PageStory({
  theme = "light",
  nodeRef = "product",
  level,
  search = "",
  ...sample
}: V3StoryOptions) {
  const [source] = useState(() => sampleNodeSource(sample));
  return (
    <FactoryStoryFrame
      mode="viewport"
      theme={theme}
      route={`${nodeHref(STORY_FID, nodeRef, level)}${search}`}
    >
      <DocumentTheme theme={theme} />
      <NodeSourceProvider source={source}>
        <FactoryToastProvider>
          <Routes>
            <Route path="/factory/:factoryId/n/:ref" element={<RoutedNodePage />} />
            <Route path="/factory/:factoryId/n/:ref/:level" element={<RoutedNodePage />} />
          </Routes>
          <V3Sheets factoryId={STORY_FID} keys />
        </FactoryToastProvider>
      </NodeSourceProvider>
    </FactoryStoryFrame>
  );
}

/** One part of a page (a header, a strip) in a padded column no wider than `width`. */
export function V3PartStory({
  theme = "light",
  width = 880,
  children,
}: {
  theme?: "light" | "dark";
  width?: number;
  children: ReactNode;
}) {
  return (
    <FactoryStoryFrame mode="viewport" theme={theme} route={nodeHref(STORY_FID, "product")}>
      <DocumentTheme theme={theme} />
      <NodeContextProvider value={{ factoryId: STORY_FID }}>
        <div className="h-full overflow-y-auto p-4">
          <div className="mx-auto flex w-full flex-col gap-4" style={{ maxWidth: width }}>
            {children}
          </div>
        </div>
      </NodeContextProvider>
    </FactoryStoryFrame>
  );
}
