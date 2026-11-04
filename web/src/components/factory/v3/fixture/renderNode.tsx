// Test-only: a node page over the sample factory, routed so links, `l` and
// `h` move between node pages, with its sheets and the cockpit toast. The
// returned `path()` and `search()` say which node and sheet are shown.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { FactoryToastProvider } from "../../ui/Toast";
import { NodePage, type NodePageProps } from "../NodePage";
import { nodeHref } from "../nodeRoutes";
import { type NodeSource, NodeSourceProvider } from "../nodeSource";
import { V3Sheets } from "../sheets/V3Sheets";
import { useLocation, useParams } from "@/lib/routing";
import { type SampleOptions, sampleNodeSource } from "./sampleNodeSource";
import type { SampleSteerEngine } from "./sampleSteer";

export const TEST_FID = "f-sample";

let path = "";
let search = "";
function Probe() {
  const location = useLocation();
  path = location.pathname;
  search = location.search;
  return null;
}

function Routed({ actions }: { actions?: NodePageProps["actions"] }) {
  const { ref = "product", level } = useParams<{ ref: string; level?: string }>();
  return (
    <NodePage
      key={`${ref}/${level ?? ""}`}
      factoryId={TEST_FID}
      nodeRef={ref}
      level={level ?? null}
      actions={actions}
    />
  );
}

export function renderNode(
  nodeRef: string,
  options: SampleOptions & {
    level?: string;
    actions?: NodePageProps["actions"];
    source?: NodeSource;
    /** The address's query (`?verb=add&title=…`, `?peek=needs`). */
    query?: string;
  } = {},
) {
  const { level, actions, source: given, query = "", ...sample } = options;
  const source = given ?? sampleNodeSource(sample);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <NodeSourceProvider source={source}>
        <MemoryRouter initialEntries={[`${nodeHref(TEST_FID, nodeRef, level)}${query}`]}>
          <FactoryToastProvider>
            <Routes>
              <Route path="/factory/:factoryId/n/:ref" element={<Routed actions={actions} />} />
              <Route
                path="/factory/:factoryId/n/:ref/:level"
                element={<Routed actions={actions} />}
              />
            </Routes>
            <V3Sheets factoryId={TEST_FID} />
            <Probe />
          </FactoryToastProvider>
        </MemoryRouter>
      </NodeSourceProvider>
    </QueryClientProvider>,
  );
  /** The sample's engine (what it read and wrote), when the page reads the sample. */
  const engine = (source as { engine?: SampleSteerEngine }).engine as SampleSteerEngine;
  return { ...view, source, engine, client, path: () => path, search: () => search };
}
