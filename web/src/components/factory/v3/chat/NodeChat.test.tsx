import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type FakeFetch, renderChat } from "../fixture/renderChat";
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

describe("cockpit v3 · the node chat column", () => {
  it("is the project's own thread, its cards from live state, beside the page", async () => {
    setWindow("desktop");
    renderChat("assistant-core", { install });
    await threadOf("project:assistant-core");
    const column = screen.getByTestId("v3-chat");
    expect(within(column).getByTestId("v3-chat-title")).toHaveTextContent("Chat · assistant-core");
    expect(within(column).getByTestId("v3-chat-title")).toHaveTextContent(
      "with Juno · #assistant-core",
    );
    // The demo, the alarm the server closed, and the ask with its answers.
    expect(await within(column).findByTestId("v3-closed-alarm")).toHaveTextContent(
      "Closed by the server · D-48",
    );
    expect(within(column).getByTestId("v3-closed-alarm")).toHaveTextContent(
      "The real ask is D-45 (Apply C-16).",
    );
    expect(within(column).getByTestId("decision-embed-D-45")).toBeInTheDocument();
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

  it("answers no decision on Enter: typed words show the ask's card, the answer is a click", async () => {
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
    expect(view.fetch.posts).toEqual([]);
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
    renderChat("assistant-core", { install });
    const embed = await screen.findByTestId("decision-embed-D-45", {}, { timeout: 4000 });
    const button = within(embed).getAllByRole("button")[0]!;
    const event = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
    button.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
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

  it("opens previews from plain words and never applies one", async () => {
    setWindow("desktop");
    const onVerb = vi.fn();
    const view = renderChat("assistant-panel-ux", { install, actions: { factoryId: "", onVerb } });
    const box = await composer();
    const user = userEvent.setup();
    await user.click(box);
    await user.keyboard("start it now{Enter}");
    await waitFor(() => expect(onVerb).toHaveBeenCalledTimes(1));
    expect(onVerb.mock.calls[0]![0]).toMatchObject({ verb: "start" });
    expect(view.fetch.posts).toEqual([]);
    expect(screen.getAllByTestId("v3-chat-local").at(-1)).toHaveTextContent(
      "opens a preview first",
    );
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
      "This run has no thread of its own: this is T-16’s thread.",
    );
    expect(screen.getByTestId("v3-chat-title")).toHaveTextContent("Chat · Debit each turn");
  });

  it("lists children with news at the root, each opening its node", async () => {
    setWindow("desktop");
    const view = renderChat("product", { install });
    await threadOf("factory");
    const news = await screen.findByTestId("room-thread-news");
    const lines = within(news).getAllByRole("link");
    expect(lines.map((l) => l.textContent)).toEqual([
      expect.stringContaining("connectors-core2 new"),
      expect.stringContaining("build-tier-drafts-31 new"),
      expect.stringContaining("skill-discovery1 new"),
    ]);
    await userEvent.setup().click(lines[0]!);
    await waitFor(() => expect(view.path()).toContain("/n/connectors-core"));
  });

  it("lists the node's verbs after / (the answer opens the card) and quick verbs above the box", async () => {
    setWindow("desktop");
    const onVerb = vi.fn();
    renderChat("assistant-core", { install, actions: { factoryId: "", onVerb } });
    const box = await composer();
    const quick = screen.getByTestId("v3-chat-quick");
    expect(
      within(quick)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["Why?", "Add child", "Team", "Autonomy & merge", "Pause"]);
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
    expect(onVerb).toHaveBeenCalledWith(expect.objectContaining({ verb: "team" }), undefined);
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
    expect(onVerb).toHaveBeenCalledWith(expect.objectContaining({ verb: "start" }), undefined);
    await user.click(within(offer).getByRole("button", { name: "Keep waiting" }));
    expect(offer).toHaveAttribute("data-state", "kept");
    const thread = screen.getByTestId("room-message-list");
    const why = within(thread).getByTestId("v3-why");
    expect(why).toHaveTextContent("Why assistant-panel-ux has not started");
    // In the chat the why card carries the answer and "Start it now instead".
    expect(within(why).getByRole("button", { name: "Apply" })).toBeInTheDocument();
    expect(within(why).getByRole("button", { name: "Start it now instead" })).toBeInTheDocument();
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
  it("pins the box with the node's ask above it; a tap opens the thread full screen", async () => {
    setWindow("phone");
    const view = renderChat("assistant-core", { install });
    const pinned = await screen.findByTestId("v3-chat-pinned");
    expect(within(pinned).getByTestId("v3-chat-last")).toHaveTextContent(
      "D-45 Atlas proposes C-16: update assistant-core (constraints, DoD)",
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
