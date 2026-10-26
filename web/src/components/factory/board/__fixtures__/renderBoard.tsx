// Test-only rendering for board components: a router (links), a query client
// (the activity list) and a pinned live state; `rerender` keeps them.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { FactoryLiveContext } from "../factoryLive";

export function renderBoard(ui: ReactElement, options: { route?: string; live?: boolean } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[options.route ?? "/factory/f"]}>
        <FactoryLiveContext.Provider value={options.live ?? true}>
          {children}
        </FactoryLiveContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>
  );
  return { client, ...render(ui, { wrapper }) };
}
