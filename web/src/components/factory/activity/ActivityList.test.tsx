import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EventPage, EventView } from "@/lib/factoryApi";
import { LIVE_RUN_FACTORY_ID, liveRunSnapshot } from "../__fixtures__/liveRun";
import { boardActivityEvents } from "../board/__fixtures__/boardFixtures";
import { renderBoard } from "../board/__fixtures__/renderBoard";
import {
  DEFAULT_KIND_GROUPS,
  type KindGroupId,
  buildActivityRows,
  kindGroup,
  kindsQuery,
} from "./activityModel";
import { ActivityList } from "./ActivityList";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
}

function serve(page: EventPage = boardActivityEvents()) {
  authenticatedFetch.mockImplementation(async () => json(page));
}

/** The query of the most recent events request. */
function lastQuery(): URLSearchParams {
  const path = authenticatedFetch.mock.calls.at(-1)![0] as string;
  return new URL(path, "http://x").searchParams;
}

function renderList(project?: string) {
  const snapshot = liveRunSnapshot();
  return renderBoard(
    <ActivityList
      factoryId={LIVE_RUN_FACTORY_ID}
      projects={snapshot.projects}
      members={snapshot.members}
      project={project}
    />,
  );
}

function event(id: string, kind: string, extra: Partial<EventView> = {}): EventView {
  return {
    id,
    ts: 1_000_000 - Number(id.replace(/\D/g, "")),
    kind,
    severity: "info",
    project_slug: null,
    from_id: null,
    to_id: null,
    from_title: null,
    to_title: null,
    subject: null,
    summary: kind,
    data: null,
    ...extra,
  };
}

beforeEach(() => authenticatedFetch.mockReset());

describe("activity model", () => {
  it("folds each run of heartbeats into one row and counts what it held", () => {
    const rows = buildActivityRows(
      [
        event("e1", "pr.ready"),
        event("e2", "heartbeat.delivered"),
        event("e3", "heartbeat.skipped", { data: { counts: { a: 2, b: 1 } } }),
        event("e4", "heartbeat.failed"),
        event("e5", "decision.opened"),
        event("e6", "heartbeat.delivered"),
      ],
      { foldHeartbeats: true },
    );
    expect(rows.map((r) => r.type)).toEqual(["event", "heartbeats", "event", "heartbeats"]);
    expect(rows[1]).toMatchObject({ delivered: 1, skipped: 3, failed: 1 });
    expect(rows[1]!.type === "heartbeats" && rows[1]!.events).toHaveLength(3);
  });

  it("keeps heartbeats as their own rows when asked for", () => {
    const rows = buildActivityRows([event("e1", "heartbeat.delivered")], { foldHeartbeats: false });
    expect(rows).toMatchObject([{ type: "event" }]);
  });

  it("folds a message's receipt into the message row", () => {
    const rows = buildActivityRows(
      [
        event("e1", "message.delivered", { subject: "mail:1" }),
        event("e2", "message.queued", { subject: "mail:1" }),
        event("e3", "message.queued", { subject: "mail:2" }),
        event("e4", "message.bounced", { subject: "mail:9" }),
      ],
      { foldHeartbeats: true },
    );
    expect(rows.map((r) => (r.type === "event" ? `${r.event.id}:${r.delivery}` : r.type))).toEqual([
      "e2:delivered",
      "e3:queued",
      "e4:bounced",
    ]);
  });

  it("asks the server for everything unless the kinds are narrowed", () => {
    expect(kindsQuery(DEFAULT_KIND_GROUPS)).toBeUndefined();
    expect(kindsQuery(new Set<KindGroupId>(["decisions", "alerts"]))).toEqual([
      "decision.*",
      "alert.*",
      "needs_human",
      "needs_human.*",
    ]);
    expect(kindGroup("dispatch")).toBe("dispatch");
    expect(kindGroup("preview.reported")).toBe("previews");
    expect(kindGroup("something.new")).toBeNull();
  });
});

describe("ActivityList", () => {
  it("renders one row per event with time, actors, kind and delivery", async () => {
    serve();
    renderList();
    const rows = await screen.findAllByTestId("activity-row");
    const first = rows[0]!;
    expect(first).toHaveAttribute("data-kind", "message.queued");
    expect(first).toHaveTextContent("PL · db-version");
    expect(first).toHaveTextContent("ic:db-version-route");
    expect(first).toHaveTextContent("info");
    expect(first.querySelector("[data-delivery]")).toHaveAttribute("data-delivery", "delivered");
    expect(rows.some((r) => r.getAttribute("data-kind") === "message.delivered")).toBe(false);
  });

  it("aggregates heartbeats into one ♥ row by default and shows them on request", async () => {
    serve();
    renderList();
    const folded = await screen.findByTestId("activity-heartbeats");
    expect(folded).toHaveAttribute("data-count", "2");
    expect(folded).toHaveTextContent("1 delivered · 3 skipped · no change");
    expect(lastQuery().get("kinds")).toBeNull();

    fireEvent.click(screen.getByTestId("activity-kind-heartbeats"));
    await waitFor(() =>
      expect(
        screen.getAllByTestId("activity-row").some((r) => r.dataset.kind === "heartbeat.skipped"),
      ).toBe(true),
    );
    expect(screen.queryByTestId("activity-heartbeats")).toBeNull();
  });

  it("turns the kind filters into the events query", async () => {
    serve();
    renderList();
    await screen.findAllByTestId("activity-row");
    fireEvent.click(screen.getByTestId("activity-kind-messages"));
    await waitFor(() => expect(lastQuery().get("kinds")).not.toBeNull());
    const kinds = lastQuery().get("kinds")!.split(",");
    expect(kinds).toContain("decision.*");
    expect(kinds).not.toContain("message.*");
    expect(kinds).not.toContain("heartbeat.*");
  });

  it("turns the project and member filters into the events query", async () => {
    serve();
    renderList();
    await screen.findAllByTestId("activity-row");
    const project = screen.getByTestId("activity-filter-project");
    project.focus();
    fireEvent.keyDown(project, { key: "Enter" });
    fireEvent.click(await screen.findByRole("option", { name: "db-version" }));
    await waitFor(() => expect(lastQuery().get("project")).toBe("db-version"));

    const member = screen.getByTestId("activity-filter-member");
    member.focus();
    fireEvent.keyDown(member, { key: "Enter" });
    fireEvent.click(await screen.findByRole("option", { name: "Atlas" }));
    await waitFor(() => expect(lastQuery().get("member")).toBe(LIVE_RUN_FACTORY_ID));
    expect(lastQuery().get("project")).toBe("db-version");
  });

  it("pins the project and offers only its agents", async () => {
    serve();
    renderList("api-version");
    await screen.findAllByTestId("activity-row");
    expect(lastQuery().get("project")).toBe("api-version");
    expect(screen.queryByTestId("activity-filter-project")).toBeNull();
    const member = screen.getByTestId("activity-filter-member");
    member.focus();
    fireEvent.keyDown(member, { key: "Enter" });
    const options = await screen.findAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([
      "All agents",
      "PL · api-version",
      "env_keeper",
      "demo-version-endpoint",
      "ic_codex:version-endpoint",
    ]);
  });

  it("expands a row into its excerpt and data", async () => {
    serve();
    renderList();
    const row = (await screen.findAllByTestId("activity-row"))[0]!;
    fireEvent.click(within(row).getByRole("button"));
    const details = within(row).getByTestId("activity-row-details");
    expect(details).toHaveTextContent("keep going on the route; D-2 is with the human");
    expect(details).toHaveTextContent('"mail_id": "mail_7a8b9c0d"');
  });

  it("says when there is no activity", async () => {
    serve({ data: [], has_more: false });
    renderList();
    expect(await screen.findByTestId("activity-empty")).toHaveTextContent("No activity yet.");
  });
});
