import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EventPage, EventView } from "@/lib/factoryApi";
import { LIVE_RUN_FACTORY_ID, liveRunSnapshot } from "../__fixtures__/liveRun";
import { boardActivityEvents } from "../board/__fixtures__/boardFixtures";
import { renderBoard } from "../board/__fixtures__/renderBoard";
import {
  ACTIVITY_KIND_GROUPS,
  type ActivityRow,
  DEFAULT_KIND_GROUPS,
  type KindGroupId,
  buildActivityRows,
  kindGroup,
  kindsQuery,
} from "./activityModel";
import { ActivityList, heartbeatText } from "./ActivityList";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
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
    expect(rows[1]).toMatchObject({ beats: 5, delivered: 1, skipped: 3, failed: 1 });
    expect(rows[1]!.type === "heartbeats" && rows[1]!.events).toHaveLength(3);
  });

  it("counts beats, not events, in the ♥ row", () => {
    const [mixed] = buildActivityRows(
      [
        event("e1", "heartbeat.delivered"),
        event("e2", "heartbeat.skipped", { data: { counts: { a: 2, b: 1 } } }),
      ],
      { foldHeartbeats: true },
    );
    expect(heartbeatText(mixed as Extract<ActivityRow, { type: "heartbeats" }>)).toBe(
      "4 heartbeats · 1 delivered · 3 skipped · no change",
    );
    const [skipped] = buildActivityRows(
      [event("e3", "heartbeat.skipped", { data: { counts: { a: 3 } } })],
      { foldHeartbeats: true },
    );
    expect(heartbeatText(skipped as Extract<ActivityRow, { type: "heartbeats" }>)).toBe(
      "3 heartbeats skipped · no change",
    );
    const [delivered] = buildActivityRows([event("e4", "heartbeat.delivered")], {
      foldHeartbeats: true,
    });
    expect(heartbeatText(delivered as Extract<ActivityRow, { type: "heartbeats" }>)).toBe(
      "1 heartbeat delivered",
    );
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
    expect(kindGroup("chat.posted")).toBe("chat");
    expect(kindGroup("proposal.applied")).toBe("proposals");
    expect(kindGroup("version.changed")).toBe("proposals");
    expect(kindGroup("people.invited")).toBe("people");
    expect(kindGroup("base.moved")).toBe("previews");
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
    expect(folded).toHaveAttribute("data-count", "4");
    expect(folded).toHaveTextContent("4 heartbeats · 1 delivered · 3 skipped · no change");
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

  it("asks for nothing and says so when every kind is switched off", async () => {
    serve();
    renderList();
    await screen.findAllByTestId("activity-row");
    const calls = authenticatedFetch.mock.calls.length;
    for (const group of ACTIVITY_KIND_GROUPS) {
      const toggle = screen.getByTestId(`activity-kind-${group.id}`);
      if (toggle.getAttribute("aria-pressed") === "true") fireEvent.click(toggle);
    }
    expect(await screen.findByTestId("activity-no-kinds")).toBeInTheDocument();
    expect(screen.queryByTestId("activity-row")).toBeNull();
    expect(screen.queryByTestId("activity-scroll")).toBeNull();
    const requested = authenticatedFetch.mock.calls.slice(calls).map((c) => c[0] as string);
    // Each narrowing on the way asks for its kinds; nothing asks for "everything".
    for (const path of requested) {
      expect(new URL(path, "http://x").searchParams.get("kinds")).not.toBeNull();
    }
  });

  it("stops loading older pages on its own when they add no rows", async () => {
    let page = 0;
    authenticatedFetch.mockImplementation(async () => {
      page += 1;
      const base = 1_000_000 - page * 1000;
      return json({
        data: [0, 1, 2].map((i) =>
          event(`p${page}e${i}`, "heartbeat.skipped", { ts: base - i, id: `p${page}e${i}` }),
        ),
        has_more: true,
        next_before: base - 3,
        next_before_id: `p${page}e2`,
      });
    });
    renderList();
    const loadOlder = await screen.findByTestId("activity-load-older");
    // The first page and three automatic ones, then it waits for the reader.
    expect(authenticatedFetch).toHaveBeenCalledTimes(4);
    expect(screen.getAllByTestId("activity-heartbeats")).toHaveLength(1);
    fireEvent.click(loadOlder);
    await waitFor(() => expect(authenticatedFetch).toHaveBeenCalledTimes(5));
    expect(await screen.findByTestId("activity-load-older")).toBeInTheDocument();
    expect(authenticatedFetch).toHaveBeenCalledTimes(5);
  });

  it("shows a load error with its status and no empty list box", async () => {
    authenticatedFetch.mockImplementation(async () =>
      json({ error: { message: "forbidden" } }, 403),
    );
    renderList();
    const error = await screen.findByTestId("activity-error");
    expect(error).toHaveTextContent("Couldn’t load activity(HTTP 403)Retry");
    expect(screen.queryByTestId("activity-scroll")).toBeNull();
    expect(screen.queryByTestId("activity-empty")).toBeNull();
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
