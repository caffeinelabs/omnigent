import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { liveRunOpenDecisions, liveRunSnapshot } from "@/components/factory/__fixtures__/liveRun";
import { showNotification } from "@/lib/browserNotifications";
import type { FactorySnapshot, ViewerBlock } from "@/lib/factoryApi";
import {
  type FactoryNotice,
  decisionNotices,
  isForViewer,
  snapshotNotices,
  useFactoryNotifications,
} from "./useFactoryNotifications";

vi.mock("@/lib/browserNotifications", () => ({ showNotification: vi.fn() }));

let hidden = false;

function wrapper({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>;
}

function notice(key: string): FactoryNotice {
  return { key, title: `title ${key}`, body: `body ${key}`, path: `/factory/f?x=${key}` };
}

beforeEach(() => {
  hidden = false;
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  vi.mocked(showNotification).mockClear();
});
afterEach(() => {
  Reflect.deleteProperty(document, "hidden");
});

describe("useFactoryNotifications", () => {
  it("never notifies for what was there at mount, only for new items while hidden", () => {
    hidden = true;
    const { rerender } = renderHook(({ list }) => useFactoryNotifications(list), {
      wrapper,
      initialProps: { list: undefined as FactoryNotice[] | undefined },
    });
    rerender({ list: [notice("a")] });
    expect(showNotification).not.toHaveBeenCalled();
    rerender({ list: [notice("a"), notice("b")] });
    expect(showNotification).toHaveBeenCalledTimes(1);
    expect(showNotification).toHaveBeenCalledWith(
      expect.objectContaining({ title: "title b", tag: "b", navigatePath: "/factory/f?x=b" }),
    );
  });

  it("stays quiet while the page is visible, and doesn't replay those items later", () => {
    const { rerender } = renderHook(({ list }) => useFactoryNotifications(list), {
      wrapper,
      initialProps: { list: [notice("a")] },
    });
    rerender({ list: [notice("a"), notice("c")] });
    expect(showNotification).not.toHaveBeenCalled();
    hidden = true;
    rerender({ list: [notice("a"), notice("c")] });
    rerender({ list: [notice("c")] });
    expect(showNotification).not.toHaveBeenCalled();
    rerender({ list: [notice("c"), notice("d")] });
    expect(showNotification).toHaveBeenCalledTimes(1);
  });
});

describe("factory notices", () => {
  it("lists blocking decisions, ready PRs and demos from a snapshot", () => {
    const keys = snapshotNotices(liveRunSnapshot()).map((n) => n.key);
    expect(keys).toEqual([
      "decision:a9d44647df3050199c1577ba21f16b67",
      "decision:176d6b5479215203bd985355ae160221",
      "ready:f74d5967c53455139e85ba1b402a5bd4:7767f0d1c2a94b8e3f50d6a71b2c9e84f03d5a16",
      "demo:df2503ab72c051c6bdfa1e15f1570e31",
    ]);
    const ready = snapshotNotices(liveRunSnapshot())[2]!;
    expect(ready.path).toBe("/factory/f4f4ba9c77725331b12f0fa7072dc4d3?review=9630");
  });

  it("lists the Inbox's blocking decisions with their factory", () => {
    const [first] = decisionNotices(liveRunOpenDecisions().data);
    expect(first).toEqual({
      key: "decision:a9d44647df3050199c1577ba21f16b67",
      title: "Atlas factory · D-2 needs you",
      body: "Which table is the source of truth for the DB version: knex_migrations or schema_migrations?",
      path: "/factory/f4f4ba9c77725331b12f0fa7072dc4d3?decision=D-2",
    });
  });
});

function viewer(
  role: ViewerBlock["role"],
  level: number,
  userId = "opal@caffeine.ai",
): ViewerBlock {
  const can = {
    post: level >= 2,
    answer: level >= 2,
    review: level >= 2,
    control: level >= 2,
    intervene: level >= 2,
    apply_projects: level >= 2,
    apply_tuning: level >= 3,
    restart: level >= 3,
    stop: level >= 3,
    invite: level >= 3,
    settings: level >= 3,
    terminal: level >= 4,
    start: level >= 4,
  };
  return { user_id: userId, role, level, is_creator: role === "creator", can };
}

function sharedSnapshot(me: ViewerBlock): FactorySnapshot {
  const snapshot = liveRunSnapshot();
  const [first, second] = snapshot.decisions.filter((d) => d.status === "open" && d.blocking);
  first!.assignee = "owners";
  second!.assignee = "mia@caffeine.ai";
  snapshot.viewer = me;
  return snapshot;
}

describe("notices in a shared factory", () => {
  it("an operator hears only decisions for operators or for them", () => {
    const snapshot = sharedSnapshot(viewer("operator", 2));
    const decisions = snapshotNotices(snapshot).filter((n) => n.key.startsWith("decision:"));
    expect(decisions).toEqual([]);
    const mine = sharedSnapshot(viewer("operator", 2, "mia@caffeine.ai"));
    expect(snapshotNotices(mine).filter((n) => n.key.startsWith("decision:"))).toHaveLength(1);
  });

  it("an owner hears owner decisions and dead members; a viewer hears nothing", () => {
    const owner = sharedSnapshot(viewer("owner", 3));
    owner.members[0]!.health = "dead";
    const keys = snapshotNotices(owner).map((n) => n.key);
    expect(keys.filter((k) => k.startsWith("decision:"))).toHaveLength(1);
    expect(keys).toContain(`dead:${owner.members[0]!.session_id}`);
    expect(snapshotNotices(sharedSnapshot(viewer("viewer", 1)))).toEqual([]);
  });

  it("a claim makes a decision its claimer's", () => {
    const snapshot = liveRunSnapshot();
    const decision = { ...snapshot.decisions[0]!, assignee: null, claimed_by: "mia@caffeine.ai" };
    expect(isForViewer(decision, viewer("operator", 2))).toBe(false);
    expect(isForViewer(decision, viewer("operator", 2, "mia@caffeine.ai"))).toBe(true);
    expect(isForViewer({ ...decision, claimed_by: null }, viewer("operator", 2))).toBe(true);
    expect(isForViewer({ ...decision, claimed_by: null }, null)).toBe(true);
  });

  it("the Inbox drops decisions the server says are not mine", () => {
    const items = liveRunOpenDecisions().data;
    const marked = items.map((item, i) => ({ ...item, mine: i !== 0 }));
    const keys = decisionNotices(marked).map((n) => n.key);
    expect(keys).not.toContain(`decision:${items[0]!.decision.id}`);
    expect(decisionNotices(items).length).toBeGreaterThan(keys.length);
  });
});
