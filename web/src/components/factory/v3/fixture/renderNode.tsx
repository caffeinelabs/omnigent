// Test-only: a node page over the sample factory, routed so links, `l` and
// `h` move between node pages. The returned `at()` says which node is shown.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { NodePage, type NodePageProps } from "../NodePage";
import { nodeHref } from "../nodeRoutes";
import { type NodeSource, NodeSourceProvider } from "../nodeSource";
import { useLocation, useParams } from "@/lib/routing";
import { type SampleOptions, sampleNodeSource } from "./sampleNodeSource";

export const TEST_FID = "f-sample";

let path = "";
function Probe() {
  path = useLocation().pathname;
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
  } = {},
) {
  const { level, actions, source: given, ...sample } = options;
  const source = given ?? sampleNodeSource(sample);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <NodeSourceProvider source={source}>
        <MemoryRouter initialEntries={[nodeHref(TEST_FID, nodeRef, level)]}>
          <Routes>
            <Route path="/factory/:factoryId/n/:ref" element={<Routed actions={actions} />} />
            <Route
              path="/factory/:factoryId/n/:ref/:level"
              element={<Routed actions={actions} />}
            />
          </Routes>
          <Probe />
        </MemoryRouter>
      </NodeSourceProvider>
    </QueryClientProvider>,
  );
  return { ...view, source, client, path: () => path };
}
