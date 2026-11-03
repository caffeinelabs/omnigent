import { describe, expect, it } from "vitest";
import type { ThreadView } from "@/lib/factoryApi";
import { sampleRoomSnapshot, sampleRoomThreads } from "../fixture/sampleRoom";
import { sampleEngine } from "../fixture/sampleNodeSource";
import {
  aboutLabel,
  childNews,
  keyRef,
  namedBody,
  nodeThread,
  threadNodeRef,
  threadOwnerRef,
} from "./nodeThread";

const engine = sampleEngine();
const snapshot = sampleRoomSnapshot();
const threads = sampleRoomThreads(snapshot);
const ctx = { snapshot, threads, perNode: false, sample: false };

function page(ref: string) {
  const p = engine.node(ref);
  if (!p) throw new Error(`no node ${ref}`);
  return p;
}

describe("cockpit v3 · which Room thread a node's chat uses (until per-node threads)", () => {
  it("gives a root its own thread and a project its project thread, naming nothing", () => {
    expect(nodeThread(page("product"), ctx)).toEqual({ key: "factory", about: null });
    expect(nodeThread(page("fx"), ctx)).toEqual({ key: "improvements", about: null });
    expect(nodeThread(page("assistant-core"), ctx)).toEqual({
      key: "project:assistant-core",
      about: null,
    });
  });

  it("puts features and needs in #factory, named", () => {
    expect(nodeThread(page("I-1"), ctx)).toEqual({
      key: "factory",
      about: { ref: "I-1", label: "I-1 · Agent assistant (sample)" },
    });
    expect(nodeThread(page("ask:m-0555"), ctx).key).toBe("factory");
    expect(nodeThread(page("ask:m-0555"), ctx).about?.label).toBe(
      "Agents that also do discovery, sales and promotion",
    );
  });

  it("gives needs and studies Scout's thread while it is listed", () => {
    const scout: ThreadView = { ...threads[0]!, key: "discovery", kind: "discovery" };
    const withScout = { ...ctx, threads: [...threads, scout] };
    expect(nodeThread(page("R-1"), withScout).key).toBe("discovery");
    expect(nodeThread(page("ask:m-0555"), withScout).key).toBe("discovery");
    expect(nodeThread(page("I-1"), withScout).key).toBe("factory");
  });

  it("puts a task in its project's thread, named", () => {
    expect(nodeThread(page("T-16"), ctx)).toEqual({
      key: "project:assistant-core",
      about: { ref: "T-16", label: "T-16 · Debit each turn" },
    });
  });

  it("gives a change its PR thread when the Room has one, else its project's", () => {
    expect(nodeThread(page("PR-9876"), ctx)).toEqual({ key: "pr:pr-9876", about: null });
    const other = nodeThread(page("PR-9882"), ctx);
    expect(other.key).toBe("project:connectors-core");
    expect(other.about?.label).toMatch(/^!9882 · /);
  });

  it("falls back to the root's thread for a project this factory does not have", () => {
    const thin = { ...ctx, snapshot: { ...snapshot, projects: [] } };
    expect(nodeThread(page("T-16"), thin)).toEqual({
      key: "factory",
      about: { ref: "T-16", label: "T-16 · Debit each turn" },
    });
  });

  it("uses the root's thread and names nothing on sample data", () => {
    const sample = { ...ctx, sample: true };
    expect(nodeThread(page("T-16"), sample)).toEqual({ key: "factory", about: null });
    expect(nodeThread(page("fx:health"), sample)).toEqual({ key: "improvements", about: null });
  });

  it("uses the node's own key once the server routes a thread per node", () => {
    expect(nodeThread(page("I-1"), { ...ctx, perNode: true })).toEqual({
      key: "feature:I-1",
      about: null,
    });
  });
});

describe("cockpit v3 · thread keys and refs", () => {
  it("reads the node a per-node key names", () => {
    expect(keyRef("factory")).toBe("product");
    expect(keyRef("improvements")).toBe("fx");
    expect(keyRef("task:T-16")).toBe("T-16");
    expect(keyRef("need:O-3")).toBe("O-3");
    expect(keyRef("need:ask:m-0555")).toBe("ask:m-0555");
    expect(keyRef("feature:from-C-12")).toBe("feature:from-C-12");
    expect(keyRef("project:assistant-core")).toBe("assistant-core");
    expect(keyRef("pr:abc")).toBeNull();
    expect(keyRef("nonsense")).toBeNull();
  });

  it("shows a run its task's thread", () => {
    expect(threadOwnerRef(page("run:luz-t16"))).toBe("T-16");
    expect(threadOwnerRef(page("T-16"))).toBe("T-16");
    expect(threadOwnerRef(page("product"))).toBe("product");
  });

  it("opens the node a Room thread is about", () => {
    expect(threadNodeRef("factory", snapshot)).toBe("product");
    expect(threadNodeRef("project:assistant-core", snapshot)).toBe("assistant-core");
    expect(threadNodeRef("pr:pr-9876", snapshot)).toBe("PR-9876");
    expect(threadNodeRef("pr:unknown", snapshot)).toBeNull();
  });

  it("names a node by its code and title, and prefixes a message once", () => {
    expect(aboutLabel({ ref: "T-16", title: "Debit each turn" })).toBe("T-16 · Debit each turn");
    expect(aboutLabel({ ref: "PR-9876", title: "Core" })).toBe("!9876 · Core");
    expect(aboutLabel({ ref: "ask:m-1", title: "An ask" })).toBe("An ask");
    const about = { ref: "T-16", label: "T-16 · Debit each turn" };
    expect(namedBody("why held?", about)).toBe("About T-16 · Debit each turn: why held?");
    expect(namedBody("About T-16: why held?", about)).toBe("About T-16: why held?");
    expect(namedBody("why held?", null)).toBe("why held?");
  });
});

describe("cockpit v3 · children with news", () => {
  it("lists every other thread of its tree with news at a root, most news first", () => {
    const news = childNews(page("product"), threads, "factory", snapshot);
    expect(news.map((n) => [n.ref, n.unread])).toEqual([
      ["connectors-core", 2],
      ["build-tier-drafts-3", 1],
      ["skill-discovery", 1],
    ]);
    expect(news[0]!.mention).toContain("connectors-core merged");
  });

  it("lists only direct children below a root, by their node titles", () => {
    expect(childNews(page("I-1"), threads, "factory", snapshot).map((n) => n.title)).toEqual([
      "connectors-core",
      "skill-discovery",
    ]);
    expect(childNews(page("assistant-core"), threads, "project:assistant-core", snapshot)).toEqual(
      [],
    );
    expect(childNews(page("fx"), threads, "improvements", snapshot)).toEqual([]);
  });
});
