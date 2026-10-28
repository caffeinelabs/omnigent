import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { InterventionDialog } from "./InterventionDialog";
import {
  type InterventionKind,
  type InterventionTarget,
  canConfirm,
  otherVendor,
  planIntervention,
} from "./interventionModel";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

const FID = "f4f4ba9c77725331b12f0fa7072dc4d3";
const R2: InterventionTarget = {
  sessionId: "9ba28e67c11a5a2fba4b7e0053ab6150",
  title: "ic:route-r2",
  role: "ic",
  project: "db-version",
  taskRef: "T-4",
  harness: "claude-native",
};
const R1: InterventionTarget = {
  ...R2,
  sessionId: "3780cf813483558abf347456c74160c2",
  title: "ic:route-r1",
};
const PL: InterventionTarget = {
  sessionId: "a2fc64b9e92e54c8b2f0f78038df202d",
  title: "PL · db-version",
  role: "project_lead",
  project: "db-version",
  taskRef: null,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function posted(): unknown[] {
  return authenticatedFetch.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === "POST")
    .map(([path, init]) => ({ path, body: JSON.parse((init as RequestInit).body as string) }));
}

function renderDialog(
  kind: InterventionKind,
  targets: InterventionTarget[],
  onOpenChange = vi.fn(),
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <InterventionDialog
        factoryId={FID}
        kind={kind}
        targets={targets}
        onOpenChange={onOpenChange}
      />
    </QueryClientProvider>,
  );
  return onOpenChange;
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  authenticatedFetch.mockImplementation(async () => json({ events: [{ id: "ev1", ts: 1 }] }, 202));
});

describe("planIntervention", () => {
  it("sends message and interrupt to each agent as member targets", () => {
    const plan = planIntervention("interrupt", [R2, PL], { note: " stop " });
    expect(plan.requests).toEqual([
      { action: "interrupt", target: `member:${R2.sessionId}`, note: "stop" },
      { action: "interrupt", target: `member:${PL.sessionId}`, note: "stop" },
    ]);
    expect(plan.labels).toEqual(["ic:route-r2", "PL · db-version"]);
  });

  it("sends task actions once per task and skips agents without one", () => {
    const plan = planIntervention("redispatch", [R2, R1, PL]);
    expect(plan.requests).toEqual([{ action: "redispatch", target: "task:T-4" }]);
    expect(plan.skipped).toEqual([{ title: "PL · db-version", reason: "works on no task" }]);
    expect(planIntervention("reassign", [R2], { vendor: "ic" }).requests).toEqual([
      { action: "reassign", target: "task:T-4", options: { vendor: "ic" } },
    ]);
  });

  it("needs text for a message and a target for anything", () => {
    expect(canConfirm("message", planIntervention("message", [R2]), "  ")).toBe(false);
    expect(canConfirm("message", planIntervention("message", [R2]), "hi")).toBe(true);
    expect(canConfirm("redispatch", planIntervention("redispatch", [PL]), "")).toBe(false);
    expect(otherVendor("codex-native")).toBe("ic");
    expect(otherVendor("claude-native")).toBe("ic_codex");
  });
});

describe("InterventionDialog", () => {
  it("interrupts every selected agent with one human-origin control each", async () => {
    const onOpenChange = renderDialog("interrupt", [R2, R1]);
    expect(screen.getByTestId("intervention-targets")).toHaveTextContent("ic:route-r2ic:route-r1");
    fireEvent.change(screen.getByTestId("intervention-note"), {
      target: { value: "same failing test" },
    });
    fireEvent.click(screen.getByTestId("intervention-confirm"));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(posted()).toEqual([
      {
        path: `/v1/factory/factories/${FID}/control`,
        body: { action: "interrupt", target: `member:${R2.sessionId}`, note: "same failing test" },
      },
      {
        path: `/v1/factory/factories/${FID}/control`,
        body: { action: "interrupt", target: `member:${R1.sessionId}`, note: "same failing test" },
      },
    ]);
  });

  it("sends a message only once it has text", async () => {
    renderDialog("message", [R2]);
    const confirm = screen.getByTestId("intervention-confirm");
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByTestId("intervention-note"), {
      target: { value: "seed knex_migrations in the test setup" },
    });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(posted()).toHaveLength(1));
    expect(posted()[0]).toMatchObject({
      body: {
        action: "message",
        target: `member:${R2.sessionId}`,
        note: "seed knex_migrations in the test setup",
      },
    });
  });

  it("reassigns the task to the chosen vendor", async () => {
    renderDialog("reassign", [R2, PL]);
    expect(screen.getByTestId("intervention-skipped")).toHaveTextContent("PL · db-version");
    fireEvent.click(screen.getByTestId("intervention-vendor-ic"));
    fireEvent.click(screen.getByTestId("intervention-confirm"));
    await waitFor(() => expect(posted()).toHaveLength(1));
    expect(posted()[0]).toMatchObject({
      body: { action: "reassign", target: "task:T-4", options: { vendor: "ic" } },
    });
  });

  it("stays open and says so when a target refuses", async () => {
    authenticatedFetch.mockImplementation(async (_path: string, init?: RequestInit) => {
      const body = JSON.parse(init!.body as string) as { target: string };
      return body.target.endsWith(R1.sessionId)
        ? json({ error: { code: "session_closed", message: "ic:route-r1 is closed" } }, 409)
        : json({ events: [] }, 202);
    });
    const onOpenChange = renderDialog("interrupt", [R2, R1]);
    fireEvent.click(screen.getByTestId("intervention-confirm"));
    await waitFor(() => expect(posted()).toHaveLength(2));
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});
