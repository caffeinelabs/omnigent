/* oxlint-disable no-await-in-loop -- messages are typed and pages opened one after another */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChatMessageView } from "@/lib/factoryApi";
import { type FakeFetch, renderChat } from "../fixture/renderChat";
import { sampleNodeSource } from "../fixture/sampleNodeSource";
import { sampleRoomMessages } from "../fixture/sampleRoom";
import { CHAT_WIDTH_KEY, createNodeChatSlot } from "./chatLayout";

let fake: FakeFetch | null = null;
vi.mock("@/lib/identity", async (original) => ({
  ...(await original<Record<string, unknown>>()),
  authenticatedFetch: (path: string, init?: RequestInit) => fake!(path, init),
}));

const install = (f: FakeFetch) => {
  fake = f;
};

/** A phone (below 768 px) or a desktop window (1024 px and up). */
function setWindow(kind: "phone" | "desktop") {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches:
        kind === "phone"
          ? query.includes("max-width: 767.98px")
          : query.includes("min-width: 1024px"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
  fake = null;
});

/** Requests that change something, other than posting to the chat and marking it read. */
function changes(view: { fetch: FakeFetch }): string[] {
  return view.fetch.writes.filter((w) => !w.endsWith("/chat") && !w.endsWith("/chat/reads"));
}

/** Set the window's width (the chat column clamps to it). */
function resizeTo(width: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  act(() => {
    window.dispatchEvent(new Event("resize"));
  });
}

async function composer(): Promise<HTMLTextAreaElement> {
  return (await screen.findByTestId(
    "room-composer-input",
    {},
    { timeout: 4000 },
  )) as HTMLTextAreaElement;
}

async function threadOf(key: string) {
  await waitFor(() =>
    expect(screen.getByTestId("room-thread")).toHaveAttribute("data-thread", key),
  );
}

/**
 * The sample's messages with D-45's card also posted in connectors-core's
 * thread: there it is a live card that needs the viewer, not that page's ask.
 */
function withOtherAsk(): ChatMessageView[] {
  const all = sampleRoomMessages();
  const card = all.find((m) => m.card?.type === "decision")!;
  return [...all, { ...card, id: "m-other-ask", seq: 900, thread: "project:connectors-core" }];
}

describe("cockpit v3 · the node chat column", () => {
  it("is the project's own thread, a conversation: events behind Activity, its ask in Your move", async () => {
    setWindow("desktop");
    renderChat("assistant-core", { install });
    await threadOf("project:assistant-core");
    const column = screen.getByTestId("v3-chat");
    const title = within(column).getByTestId("v3-chat-title");
    expect(title).toHaveTextContent("Chat · Assistant core");
    expect(title).toHaveTextContent(/with Juno$/);
    expect(title).toHaveAttribute("title", expect.stringContaining("#assistant-core"));
    // Juno's words stay; the alarm the server closed is Activity; the ask is Your move's only.
    const list = within(column).getByTestId("room-message-list");
    await waitFor(() => expect(list).toHaveTextContent("T-16 (debit each turn) is done locally"));
    expect(within(list).queryByTestId("v3-closed-alarm")).toBeNull();
    expect(within(column).queryByTestId("decision-embed-D-45")).toBeNull();
    expect(
      within(list)
        .queryAllByRole("listitem")
        .filter((li) => li.dataset.kind === "system"),
    ).toEqual([]);
    const activity = within(column).getByTestId("v3-chat-activity");
    expect(activity).toHaveTextContent(/^Activity · 1/);
    await userEvent.setup().click(activity);
    const sheet = await screen.findByTestId("v3-activity-list");
    expect(within(sheet).getByTestId("v3-closed-alarm")).toHaveTextContent(
      "Closed by the server · D-48",
    );
    expect(within(column).getByTestId("room-composer-to")).toHaveTextContent("To Juno");
    expect(within(column).queryByTestId("room-bus-toggle")).toBeNull();
  });

  it("puts the caret in the box on a mouse click, on the text and on the box around it", async () => {
    setWindow("desktop");
    renderChat("assistant-core", { install });
    const box = await composer();
    const user = userEvent.setup();
    await user.click(box);
    expect(document.activeElement).toBe(box);
    act(() => box.blur());
    await user.click(screen.getByTestId("room-composer-to"));
    expect(document.activeElement).toBe(box);
  });

  it("never lets typed letters steer while the box has focus", async () => {
    setWindow("desktop");
    const onVerb = vi.fn();
    const onAnswer = vi.fn();
    const view = renderChat("assistant-core", {
      install,
      actions: { factoryId: "", onVerb, onAnswer },
    });
    const box = await composer();
    const user = userEvent.setup();
    await user.click(box);
    await user.keyboard("jkhlwcastupmn");
    expect(box.value).toBe("jkhlwcastupmn");
    expect(view.path()).toContain("/n/assistant-core");
    expect(screen.queryByTestId("v3-why")).toBeNull();
    expect(onVerb).not.toHaveBeenCalled();
    expect(onAnswer).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(box);
  });

  it("focuses the box with c from the page, and the shell's slot with / and r", async () => {
    setWindow("desktop");
    const slot = createNodeChatSlot();
    renderChat("assistant-core", { install, slot });
    const box = await composer();
    fireEvent.keyDown(document.body, { key: "c" });
    expect(document.activeElement).toBe(box);
    act(() => box.blur());
    act(() => {
      expect(slot.focus()).toBe(true);
    });
    expect(document.activeElement).toBe(box);
  });

  it("answers no decision on Enter: typed words are posted and show the ask's card, the answer is a click", async () => {
    setWindow("desktop");
    const onAnswer = vi.fn();
    const view = renderChat("assistant-core", { install, actions: { factoryId: "", onAnswer } });
    const box = await composer();
    const user = userEvent.setup();
    await user.click(box);
    await user.keyboard("apply c-16{Enter}");
    const card = await screen.findByTestId("v3-ask-card");
    expect(card).toHaveAttribute("data-decision", "D-45");
    expect(onAnswer).not.toHaveBeenCalled();
    expect(view.fetch.posts).toEqual([
      expect.objectContaining({ thread: "project:assistant-core", body: "apply c-16" }),
    ]);
    expect(changes(view)).toEqual([]);
    // Enter on the card's answer does nothing either; a click answers.
    const apply = within(card).getByRole("button", { name: "Apply" });
    apply.focus();
    await user.keyboard("{Enter}");
    expect(onAnswer).not.toHaveBeenCalled();
    await user.click(apply);
    expect(onAnswer).toHaveBeenCalledWith(
      expect.objectContaining({ id: "D-45" }),
      expect.objectContaining({ label: "Apply" }),
    );
  });

  it("swallows Enter on the Room's decision card in the thread", async () => {
    setWindow("desktop");
    renderChat("connectors-core", { install, messages: withOtherAsk() });
    const embed = await screen.findByTestId("decision-embed-D-45", {}, { timeout: 4000 });
    const button = within(embed).getAllByRole("button")[0]!;
    const event = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
    button.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it("answers nothing on 1-5 anywhere in the Room's decision card: answers are a click", async () => {
    setWindow("desktop");
    const view = renderChat("connectors-core", { install, messages: withOtherAsk() });
    const embed = await screen.findByTestId("decision-embed-D-45", {}, { timeout: 4000 });
    const card = within(embed).getByTestId("decision-card-D-45");
    for (const target of [card, ...within(card).getAllByRole("button")]) {
      for (const key of ["1", "2", "3"]) {
        const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
        act(() => {
          target.dispatchEvent(event);
        });
        expect(event.defaultPrevented).toBe(true);
      }
    }
    await act(async () => {
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 50);
      });
    });
    expect(changes(view)).toEqual([]);
  });

  it("posts “why” as the numbered card, computed now; nothing is sent", async () => {
    setWindow("desktop");
    const view = renderChat("assistant-panel-ux", { install });
    const box = await composer();
    const user = userEvent.setup();
    await user.click(box);
    await user.keyboard("why{Enter}");
    const local = await screen.findByTestId("v3-chat-local");
    expect(local).toHaveAttribute("data-kind", "why");
    expect(within(local).getByTestId("v3-why")).toHaveTextContent(
      "Why assistant-panel-ux has not started",
    );
    expect(view.fetch.posts).toEqual([]);
    expect(box.value).toBe("");
  });

  it("posts plain words, then opens the preview they name and never applies one", async () => {
    setWindow("desktop");
    const onVerb = vi.fn();
    const view = renderChat("assistant-panel-ux", { install, actions: { factoryId: "", onVerb } });
    const box = await composer();
    const user = userEvent.setup();
    await user.click(box);
    await user.keyboard("start it now{Enter}");
    // The page opens the preview itself; only Talk goes to the page's actions.
    expect(await screen.findByTestId("v3-preview")).toHaveAttribute("data-label", "Start now");
    expect(onVerb).not.toHaveBeenCalled();
    expect(view.fetch.posts.map((p) => p.body)).toEqual(["start it now"]);
    expect(changes(view)).toEqual([]);
    expect(screen.getAllByTestId("v3-chat-local").at(-1)).toHaveTextContent(
      "opens a preview first",
    );
  });

  it("posts every sentence to the agent, even one that names a verb, and opens nothing", async () => {
    setWindow("desktop");
    const onVerb = vi.fn();
    const sentences: [string, string][] = [
      ["Please don't auto-merge this, I want to review the migration first", "assistant-core"],
      ["add logging to the payment handler before you open the PR", "assistant-core"],
      ["hold on, I think the spec for T-16 is wrong", "assistant-core"],
      ["can you change it to use the v2 billing endpoint", "assistant-core"],
      ["why does the debit test fail on staging? please fix it", "assistant-core"],
      ["plan the rollout carefully and tell me the risks", "assistant-core"],
      ["this study looks too expensive, what else could we do?", "R-1"],
    ];
    for (const ref of ["assistant-core", "R-1"]) {
      const said = sentences.filter(([, at]) => at === ref).map(([text]) => text);
      const view = renderChat(ref, { install, actions: { factoryId: "", onVerb } });
      const box = await composer();
      const user = userEvent.setup();
      await user.click(box);
      for (const text of said) {
        await user.keyboard(`${text}{Enter}`);
        await waitFor(() => expect(box.value).toBe(""));
      }
      await waitFor(() => expect(view.fetch.posts).toHaveLength(said.length));
      expect(view.fetch.posts.map((p) => p.body.replace(/^About [^:]+: /, ""))).toEqual(said);
      expect(screen.queryByTestId("v3-chat-local")).toBeNull();
      expect(changes(view)).toEqual([]);
      view.unmount();
    }
    expect(onVerb).not.toHaveBeenCalled();
  });

  it("sends a child's preview to its page: “start the panel now” on the feature", async () => {
    setWindow("desktop");
    const view = renderChat("I-1", { install });
    const box = await composer();
    const user = userEvent.setup();
    await user.click(box);
    await user.keyboard("start the panel now{Enter}");
    await waitFor(() => expect(view.path()).toContain("/n/assistant-panel-ux"));
    expect(view.search()).toBe("?verb=start");
    expect(view.fetch.posts.map((p) => p.body)).toEqual([
      "About I-1 · Agent assistant (sample): start the panel now",
    ]);
  });

  it("names who answers the thread it posts to: the sample's nodes chat with #factory's Atlas", async () => {
    setWindow("desktop");
    const view = renderChat("assistant-core", { install, sampleNodes: true });
    await threadOf("factory");
    expect(screen.getByTestId("room-composer-to")).toHaveTextContent("To Atlas");
    expect(screen.getByTestId("v3-chat-title")).toHaveTextContent("with Atlas");
    expect(screen.getByTestId("v3-chat-title")).not.toHaveTextContent("Juno");
    view.unmount();
    // A need without Scout's thread posts in #factory: Atlas answers it.
    renderChat("ask:m-0555", { install });
    await threadOf("factory");
    expect(screen.getByTestId("room-composer-to")).toHaveTextContent("To Atlas");
    expect(screen.getByTestId("v3-chat-title")).not.toHaveTextContent("Scout");
  });

  it("names the node in a shared thread: a feature posts in #factory about itself", async () => {
    setWindow("desktop");
    const view = renderChat("I-1", { install });
    await threadOf("factory");
    const box = await composer();
    expect(screen.getByTestId("room-composer-to")).toHaveTextContent(
      "To Atlas · about I-1 · Agent assistant (sample)",
    );
    const user = userEvent.setup();
    await user.click(box);
    await user.keyboard("what does wave 2 need?{Enter}");
    await waitFor(() => expect(view.fetch.posts).toHaveLength(1));
    expect(view.fetch.posts[0]).toMatchObject({
      thread: "factory",
      body: "About I-1 · Agent assistant (sample): what does wave 2 need?",
    });
  });

  it("puts a task in its project's thread, named, and relays to its builder", async () => {
    setWindow("desktop");
    renderChat("T-16", { install });
    await threadOf("project:assistant-core");
    expect(screen.getByTestId("v3-chat-title")).toHaveTextContent("with Juno (relays to Luz)");
    expect(screen.getByTestId("room-composer-to")).toHaveTextContent(
      "about T-16 · Debit each turn",
    );
  });

  it("shows a run its task's thread, and says so", async () => {
    setWindow("desktop");
    renderChat("run:luz-t16", { install });
    await threadOf("project:assistant-core");
    expect(await screen.findByTestId("v3-chat-via")).toHaveTextContent(
      "This run has no thread of its own: this is Debit each turn’s thread.",
    );
    expect(screen.getByTestId("v3-chat-title")).toHaveTextContent("Chat · Debit each turn");
  });

  it("lists children with news at the root, deeper threads folded into their level", async () => {
    setWindow("desktop");
    const view = renderChat("product", { install });
    await threadOf("factory");
    const news = await screen.findByTestId("room-thread-news");
    const lines = within(news).getAllByRole("link");
    expect(lines.map((l) => l.textContent)).toEqual([
      "Projects3 newin 2 threads",
      expect.stringContaining("build-tier-drafts-31 new"),
    ]);
    await userEvent.setup().click(lines[1]!);
    await waitFor(() => expect(view.path()).toContain("/n/build-tier-drafts-3"));
  });

  it("lists a feature's projects with news, each opening its node", async () => {
    setWindow("desktop");
    const view = renderChat("I-1", { install });
    await threadOf("factory");
    const news = await screen.findByTestId("room-thread-news");
    const lines = within(news).getAllByRole("link");
    expect(lines.map((l) => l.textContent)).toEqual([
      expect.stringContaining("connectors-core2 new"),
      expect.stringContaining("skill-discovery1 new"),
    ]);
    await userEvent.setup().click(lines[0]!);
    await waitFor(() => expect(view.path()).toContain("/n/connectors-core"));
  });

  it("shows a card it cannot draw as its message's line, never an empty row", async () => {
    setWindow("desktop");
    const base = sampleRoomMessages();
    const like = base.find((m) => m.thread === "project:assistant-core")!;
    const extra: ChatMessageView[] = [
      {
        ...like,
        id: "m-no-meta",
        seq: 900,
        author_kind: "system",
        author: "server",
        author_title: null,
        body: "D-99 closed: the project merged.",
        card: { type: "closed_alarm", ref: "D-99", id: null },
      },
      {
        ...like,
        id: "m-gone",
        seq: 901,
        author_kind: "system",
        author: "server",
        author_title: null,
        body: "Why gone-node is not moving",
        card: { type: "why", ref: "gone-node", id: null },
      },
    ];
    renderChat("assistant-core", { install, messages: [...base, ...extra] });
    const list = await screen.findByTestId("room-message-list", {}, { timeout: 4000 });
    await waitFor(() => expect(list).toHaveTextContent("Why gone-node is not moving"));
    // A closed alarm is Activity: its line shows in that list.
    await userEvent.setup().click(screen.getByTestId("v3-chat-activity"));
    const activity = await screen.findByTestId("v3-activity-list");
    await waitFor(() => expect(activity).toHaveTextContent("D-99 closed: the project merged."));
    for (const shown of [list, activity]) {
      const empty = [...shown.querySelectorAll(":scope > li")].filter(
        (li) => !(li.textContent ?? "").trim(),
      );
      expect(empty).toEqual([]);
    }
  });

  it("on a phone, a verb from the full-screen chat closes it and opens its preview on the page", async () => {
    setWindow("phone");
    const view = renderChat("assistant-panel-ux", { install, search: "?chat=1" });
    const full = await screen.findByTestId("v3-chat-full", {}, { timeout: 4000 });
    await userEvent.setup().click(await within(full).findByTestId("v3-chat-quick-start"));
    await waitFor(() => expect(screen.queryByTestId("v3-chat-full")).toBeNull());
    expect(view.search()).not.toContain("chat=1");
    expect(await screen.findByTestId("v3-preview")).toHaveAttribute("data-label", "Start now");
  });

  it("reads the per-node threads for its children's news (`?nodes=1`)", async () => {
    setWindow("desktop");
    const paths: string[] = [];
    renderChat("I-1", {
      install: (f) => {
        install(((path: string, init?: RequestInit) => {
          paths.push(path);
          return f(path, init);
        }) as FakeFetch);
      },
      nodeThreads: true,
    });
    await waitFor(() => expect(paths.some((p) => p.endsWith("/chat/threads?nodes=1"))).toBe(true));
  });

  it("draws a verb's card with its Undo while its token is open and unused", async () => {
    setWindow("desktop");
    const base = sampleRoomMessages();
    const like = base.find((m) => m.thread === "project:assistant-core")!;
    const now = Math.floor(Date.now() / 1000);
    const card = (id: string, seq: number, body: string, meta: Record<string, unknown>) => ({
      ...like,
      id,
      seq,
      author_kind: "system" as const,
      author: "server",
      author_title: null,
      body,
      card: {
        type: "preview_applied" as const,
        ref: "assistant-core",
        id: null,
        meta: { kind: "preview_applied" as const, verb: "add", ...meta },
      },
    });
    const extra: ChatMessageView[] = [
      card("m-old", 900, "Lena added Usage (C-30).", {
        undo_token: "tok-old",
        undo_until: now + 400,
      }),
      card("m-old-undone", 901, "Undone by Lena: Lena added Usage (C-30).", {
        undone: true,
        undo_token: "tok-old",
      }),
      card("m-new", 902, "Lena added Docs (C-31).", {
        label: "Add a task under assistant-core",
        undo_token: "tok-new",
        undo_until: now + 400,
      }),
      card("m-closed", 903, "Lena added Notes (C-32).", {
        undo_token: "tok-x",
        undo_until: now - 5,
      }),
    ];
    const sample = sampleNodeSource();
    const undo = vi.fn(async () => undefined);
    renderChat("assistant-core", {
      install,
      messages: [...base, ...extra],
      source: { ...sample, undo },
    });
    const list = await screen.findByTestId("room-message-list", {}, { timeout: 4000 });
    // Only the newest is live (the first was undone, the last one's ten minutes are over);
    // the others are Activity.
    await waitFor(() => expect(within(list).getAllByTestId("v3-applied")).toHaveLength(1));
    const open = within(list).getAllByTestId("v3-applied-undo");
    expect(open).toHaveLength(1);
    expect(open[0]).toHaveTextContent("Undo (7 min left)");
    expect(open[0]!.closest('[data-testid="v3-applied"]')).toHaveTextContent(
      "Lena added Docs (C-31).",
    );
    await userEvent.setup().click(open[0]!);
    await waitFor(() => expect(undo).toHaveBeenCalledWith(expect.any(String), "tok-new"));
  });

  it("offers a verb card's Undo only to the person who applied it, or an owner", async () => {
    setWindow("desktop");
    const base = sampleRoomMessages();
    const like = base.find((m) => m.thread === "project:assistant-core")!;
    const now = Math.floor(Date.now() / 1000);
    const card = (id: string, seq: number, body: string, by_user: string) => ({
      ...like,
      id,
      seq,
      author_kind: "system" as const,
      author: "server",
      author_title: null,
      body,
      card: {
        type: "preview_applied" as const,
        ref: "assistant-core",
        id: null,
        meta: {
          kind: "preview_applied" as const,
          verb: "add",
          by_user,
          undo_token: `tok-${id}`,
          undo_until: now + 400,
        },
      },
    });
    const can = {
      post: true,
      answer: true,
      review: true,
      control: true,
      intervene: true,
      apply_projects: true,
      apply_tuning: false,
      restart: true,
      stop: true,
      invite: false,
      settings: false,
      terminal: false,
      start: true,
    };
    renderChat("assistant-core", {
      install,
      messages: [
        ...base,
        card("mine", 900, "Ana added Usage (C-30).", "ana@example.com"),
        card("theirs", 901, "Bob added Docs (C-31).", "bob@example.com"),
      ],
      viewer: {
        user_id: "ana@example.com",
        role: "operator",
        level: 3,
        is_creator: false,
        can,
      },
    });
    const list = await screen.findByTestId("room-message-list", {}, { timeout: 4000 });
    await waitFor(() => expect(within(list).getAllByTestId("v3-applied")).toHaveLength(2));
    const open = within(list).getAllByTestId("v3-applied-undo");
    expect(open).toHaveLength(1);
    expect(open[0]!.closest('[data-testid="v3-applied"]')).toHaveTextContent("Ana added Usage");
  });

  it("lists the node's verbs after / (the answer opens the card); no verb buttons above the box", async () => {
    setWindow("desktop");
    const onVerb = vi.fn();
    renderChat("assistant-core", { install, actions: { factoryId: "", onVerb } });
    const box = await composer();
    expect(screen.queryByTestId("v3-chat-quick")).toBeNull();
    expect(screen.queryByTestId("v3-chat-hint")).toBeNull();
    const user = userEvent.setup();
    await user.click(box);
    await user.keyboard("/");
    const menu = await screen.findByTestId("room-composer-menu");
    const labels = within(menu)
      .getAllByRole("option")
      .map((o) => o.textContent ?? "");
    expect(labels[0]).toContain("Apply C-16");
    expect(labels[1]).toContain("Why is it not moving?");
    // Enter picks the first row: the ask's card, never the answer.
    await user.keyboard("{Enter}");
    expect(await screen.findByTestId("v3-ask-card")).toBeInTheDocument();
    await user.keyboard("/tea{Enter}");
    expect(await screen.findByTestId("v3-preview")).toHaveAttribute("data-label", "Team editor");
    expect(onVerb).not.toHaveBeenCalled();
  });

  it("never runs the Room's structural commands: /pause opens the node's Pause preview", async () => {
    setWindow("desktop");
    const onVerb = vi.fn();
    const view = renderChat("assistant-core", { install, actions: { factoryId: "", onVerb } });
    const box = await composer();
    const user = userEvent.setup();
    await user.click(box);
    await user.keyboard("/");
    const menu = await screen.findByTestId("room-composer-menu");
    for (const name of ["project", "task", "pause", "resume", "charter", "cadence"]) {
      expect(within(menu).queryByTestId(`room-composer-option-/${name}`)).toBeNull();
    }
    expect(within(menu).getByTestId("room-composer-option-/status")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await user.clear(box);
    await user.keyboard("/pause #assistant-core");
    expect(screen.getByTestId("room-composer-hint")).toHaveTextContent(
      "In this chat /pause opens Pause: a preview first",
    );
    await user.keyboard("{Enter}");
    expect(await screen.findByTestId("v3-preview")).toHaveAttribute("data-label", "Pause");
    await user.click(box);
    await user.keyboard("/task #assistant-core Retry the debit{Enter}");
    await waitFor(() =>
      expect(screen.getByTestId("v3-preview")).toHaveAttribute("data-label", "Add child"),
    );
    expect(onVerb).not.toHaveBeenCalled();
    await user.keyboard("/cadence demo{Enter}");
    expect(await screen.findByTestId("room-composer-hint")).toHaveTextContent(
      "/cadence is not a verb of this node",
    );
    expect(view.fetch.posts).toEqual([]);
    expect(changes(view)).toEqual([]);
  });

  it("draws the panel's offer and the posted why card from live state", async () => {
    setWindow("desktop");
    const onVerb = vi.fn();
    renderChat("assistant-panel-ux", { install, actions: { factoryId: "", onVerb } });
    const offer = await screen.findByTestId("v3-offer", {}, { timeout: 4000 });
    expect(offer).toHaveTextContent("Atlas offers");
    expect(offer).toHaveTextContent("It can start now");
    const user = userEvent.setup();
    await user.click(within(offer).getByTestId("v3-offer-preview"));
    expect(await screen.findByTestId("v3-preview")).toHaveAttribute("data-label", "Start now");
    expect(onVerb).not.toHaveBeenCalled();
    await user.click(within(offer).getByRole("button", { name: "Keep waiting" }));
    expect(offer).toHaveAttribute("data-state", "kept");
    const thread = screen.getByTestId("room-message-list");
    const why = within(thread).getByTestId("v3-why");
    expect(why).toHaveTextContent("Why assistant-panel-ux has not started");
    // The ask it ends at is the page's Your move, so the card offers no second answer.
    expect(within(why).queryByRole("button", { name: "Apply" })).toBeNull();
    expect(within(why).getByRole("button", { name: "Start it now instead" })).toBeInTheDocument();
  });

  it("keeps the column at most 60 % of the window, also after the window narrows", async () => {
    setWindow("desktop");
    resizeTo(1600);
    window.localStorage.setItem(CHAT_WIDTH_KEY, "960");
    renderChat("assistant-core", { install });
    const column = await screen.findByTestId("v3-chat");
    const handle = screen.getByRole("separator", { name: "Resize the chat" });
    expect(column.style.width).toBe("960px");
    resizeTo(1100);
    expect(column.style.width).toBe("660px");
    expect(Number(handle.getAttribute("aria-valuenow"))).toBe(660);
    // A wider window gives the kept width back.
    resizeTo(1600);
    expect(column.style.width).toBe("960px");
    resizeTo(1024);
  });

  it("keeps the column's width in this browser", async () => {
    setWindow("desktop");
    const view = renderChat("assistant-core", { install });
    const handle = await screen.findByRole("separator", { name: "Resize the chat" });
    const before = Number(handle.getAttribute("aria-valuenow"));
    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    const after = Number(handle.getAttribute("aria-valuenow"));
    expect(after).toBe(before + 16);
    expect(window.localStorage.getItem(CHAT_WIDTH_KEY)).toBe(String(after));
    view.unmount();
    renderChat("assistant-core", { install });
    const again = await screen.findByRole("separator", { name: "Resize the chat" });
    expect(Number(again.getAttribute("aria-valuenow"))).toBe(after);
  });
});

describe("cockpit v3 · the node chat on a phone", () => {
  it("names #factory's Atlas on the pinned box of a sample node", async () => {
    setWindow("phone");
    renderChat("assistant-core", { install, sampleNodes: true });
    const open = await screen.findByTestId("v3-chat-open");
    await waitFor(() => expect(open).toHaveTextContent("Message Atlas about Assistant core"));
  });

  it("pins the box with the conversation's newest line; a tap opens the thread full screen", async () => {
    setWindow("phone");
    const view = renderChat("assistant-core", { install });
    const pinned = await screen.findByTestId("v3-chat-pinned");
    // The node's ask is the page's Your move; the box shows the newest words.
    expect(await within(pinned).findByTestId("v3-chat-last")).toHaveTextContent(
      "Juno: T-16 (debit each turn) is done locally",
    );
    expect(screen.queryByTestId("room-thread")).toBeNull();
    const user = userEvent.setup();
    await user.click(within(pinned).getByTestId("v3-chat-open"));
    const full = await screen.findByTestId("v3-chat-full");
    expect(view.search()).toBe("?chat=1");
    expect(within(full).getByTestId("room-thread")).toHaveAttribute(
      "data-thread",
      "project:assistant-core",
    );
    // On a phone the status line's answer leads the quick verbs.
    expect(within(full).getByTestId("v3-chat-quick-answer")).toHaveTextContent("Apply C-16");
    const box = (await within(full).findByTestId("room-composer-input")) as HTMLTextAreaElement;
    await waitFor(() => expect(document.activeElement).toBe(box));
    await user.click(within(full).getByTestId("v3-chat-back"));
    await waitFor(() => expect(screen.queryByTestId("v3-chat-full")).toBeNull());
  });

  it("opens full screen from the address (`?chat=1`) and closes on Esc", async () => {
    setWindow("phone");
    renderChat("product", { install, search: "?chat=1" });
    expect(await screen.findByTestId("v3-chat-full")).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("v3-chat-full")).toBeNull());
  });
});
