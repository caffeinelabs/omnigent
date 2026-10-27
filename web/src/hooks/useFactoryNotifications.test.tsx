import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { liveRunOpenDecisions, liveRunSnapshot } from "@/components/factory/__fixtures__/liveRun";
import { showNotification } from "@/lib/browserNotifications";
import {
  type FactoryNotice,
  decisionNotices,
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
