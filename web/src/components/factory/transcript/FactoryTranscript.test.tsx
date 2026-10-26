// Rendering contract of the factory transcript cards: the user-bubble hook
// (cards, markers, fallback), outgoing posts with and without delivery
// receipts, the tool one-liners, the heartbeat group, and the BlockRenderer
// dispatch that keeps posts and outcomes visible after a turn folds.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { type ReactNode, useMemo } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { BlockRenderer } from "@/components/blocks/BlockRenderer";
import { BubbleView } from "@/components/chat/chatBubbleParts";
import factoryFixture from "@/lib/__fixtures__/factory-transcript.json";
import { itemsToBlocks } from "@/lib/itemsToBlocks";
import {
  type Bubble,
  type RenderItem,
  buildBubbles,
  foldFactoryHeartbeats,
} from "@/lib/renderItems";
import { FileViewerContext } from "@/shell/FileViewerContext";
import { FactoryPostCard } from "./FactoryPostCard";
import { FactoryToolLine, inAppPath } from "./FactoryToolLine";
import {
  FactoryLink,
  type FactoryTranscriptEnv,
  FactoryTranscriptContext,
  linkTargetKind,
} from "./FactoryTranscriptContext";
import { parseToolOutputJson } from "./factoryToolSummary";

afterEach(cleanup);

const FILE_VIEWER_NOOP = {
  openFile: () => {},
  openGithubTab: () => {},
  isChangedPath: () => false,
  conversationId: undefined,
  workspaceRoot: null,
  workspaceHome: null,
};

const { atlas: ATLAS, pl_api: PL_API, sentinel: SENTINEL } = factoryFixture.sessions;
const TITLES: Record<string, string> = {
  [ATLAS.id]: ATLAS.title,
  [PL_API.id]: PL_API.title,
  [SENTINEL.id]: SENTINEL.title,
  [factoryFixture.sessions.pl_db.id]: factoryFixture.sessions.pl_db.title,
};

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function Env({ children, env }: { children: ReactNode; env?: Partial<FactoryTranscriptEnv> }) {
  const value = useMemo(
    () => ({
      factoryId: ATLAS.id,
      resolveSession: (id: string) => (TITLES[id] ? { title: TITLES[id]! } : null),
      ...env,
    }),
    [env],
  );
  return (
    <QueryClientProvider client={queryClient}>
      <FileViewerContext.Provider value={FILE_VIEWER_NOOP}>
        <FactoryTranscriptContext.Provider value={value}>
          {children}
        </FactoryTranscriptContext.Provider>
      </FileViewerContext.Provider>
    </QueryClientProvider>
  );
}

function userBubble(text: string): Bubble {
  return {
    kind: "user",
    itemId: "u1",
    content: [{ type: "input_text", text }],
    createdAtS: 1793019600,
  };
}

const caseText = (name: string) => factoryFixture.parser_cases.find((c) => c.name === name)!.text;

type Items = Parameters<typeof itemsToBlocks>[0];

function bubblesOf(name: keyof typeof factoryFixture.transcripts): Bubble[] {
  return buildBubbles(itemsToBlocks(factoryFixture.transcripts[name] as unknown as Items), null);
}

/** Every tool render item of a fixture transcript, by bare tool name. */
function toolItems(name: keyof typeof factoryFixture.transcripts) {
  const out: Extract<RenderItem, { kind: "tool" }>[] = [];
  for (const bubble of bubblesOf(name)) {
    if (bubble.kind !== "assistant") continue;
    for (const item of bubble.items) if (item.kind === "tool") out.push(item);
  }
  return out;
}

function toolItem(transcript: keyof typeof factoryFixture.transcripts, tool: string, nth = 0) {
  return toolItems(transcript).filter((t) => t.execution.name.endsWith(tool))[nth]!;
}

describe("UserBubble factory hook", () => {
  it("renders an envelope as a message card with sender, kind and link", () => {
    render(
      <Env>
        <BubbleView bubble={userBubble(caseText("message wrapped without project"))} />
      </Env>,
    );
    const card = screen.getByTestId("factory-msg-card");
    expect(card).toHaveAttribute("data-msg-kind", "request");
    expect(within(card).getByText("Atlas")).toBeInTheDocument();
    expect(within(card).getByText("request")).toBeInTheDocument();
    expect(within(card).getByText("verify #9630")).toBeInTheDocument();
    expect(screen.getByTestId("factory-msg-open-sender")).toHaveAttribute("href", `/c/${ATLAS.id}`);
    expect(card.textContent).not.toContain("pasted_content");
    expect(card.textContent).not.toContain("[Factory message");
  });

  it("folds a long body past 8 lines", () => {
    const text = factoryFixture.transcripts.atlas
      .filter((i) => i.type === "message" && i.role === "user")
      .map((i) => (i.content as { text: string }[])[0]!.text)
      .find((t) => t.includes("Plan for **api-version**"))!;
    render(
      <Env>
        <BubbleView bubble={userBubble(text)} />
      </Env>,
    );
    const toggle = screen.getByRole("button", { name: /Show all \d+ lines/ });
    expect(
      screen.getByTestId("factory-msg-body").querySelector("[data-folded='true']"),
    ).not.toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByTestId("factory-msg-body").querySelector("[data-folded='true']")).toBeNull();
    // The spoof-quoted line renders as a blockquote, not as a marker.
    expect(screen.getByTestId("factory-msg-body").querySelector("blockquote")).not.toBeNull();
    expect(screen.queryByTestId("factory-marker")).toBeNull();
  });

  it.each([
    ["notice reply", "reply"],
    ["alert failed", "alert"],
    ["delivery failure", "delivery"],
    ["needs human", "needs_human"],
    ["unknown factory header", "generic"],
    ["heartbeat with delta", "heartbeat"],
  ])("renders %s as a factory marker", (name, topic) => {
    render(
      <Env>
        <BubbleView bubble={userBubble(caseText(name))} />
      </Env>,
    );
    expect(screen.getByTestId("factory-marker")).toHaveAttribute("data-topic", topic);
    expect(screen.queryByTestId("message-bubble")).toBeNull();
  });

  it("shows heartbeat delta bullets", () => {
    render(
      <Env>
        <BubbleView bubble={userBubble(caseText("heartbeat with delta"))} />
      </Env>,
    );
    const delta = screen.getByTestId("factory-heartbeat-delta");
    expect(within(delta).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("Heartbeat #14")).toBeInTheDocument();
  });

  it("expands a reply notice to its last output and links the session and cockpit", () => {
    render(
      <Env>
        <BubbleView bubble={userBubble(caseText("notice reply"))} />
      </Env>,
    );
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByText("done [x]")).toBeInTheDocument();
    expect(screen.getByTestId("factory-marker-open-session")).toHaveAttribute(
      "href",
      `/c/${factoryFixture.sessions.pl_db.id}`,
    );
  });

  it("links a marker that names a decision to that decision in the cockpit", () => {
    render(
      <Env>
        <BubbleView bubble={userBubble(caseText("needs human decision ref"))} />
      </Env>,
    );
    expect(screen.getByTestId("factory-marker-open-cockpit")).toHaveAttribute(
      "href",
      `/factory/${ATLAS.id}?decision=D-7`,
    );
  });

  it.each([
    ["control pause", "factory-control-card"],
    ["control preview", "factory-control-card"],
    ["decision answered", "factory-decision-card"],
    ["decision withdrawn", "factory-decision-card"],
    ["review changes requested", "factory-review-card"],
    ["review approved same line", "factory-review-card"],
    ["brief via cockpit with id", "factory-brief-card"],
  ])("renders %s as %s", (name, testId) => {
    render(
      <Env>
        <BubbleView bubble={userBubble(caseText(name))} />
      </Env>,
    );
    const card = screen.getByTestId(testId);
    expect(card.closest("[data-role='user']")).toHaveAttribute("data-user-message-id", "u1");
  });

  it("shows the human and links a preview control to the PR review", () => {
    render(
      <Env>
        <BubbleView bubble={userBubble(caseText("control preview"))} />
      </Env>,
    );
    expect(screen.getByText("Preview PR #9631")).toBeInTheDocument();
    expect(screen.getByText("Luka")).toBeInTheDocument();
    expect(screen.getByTestId("factory-control-open-review")).toHaveAttribute(
      "href",
      `/factory/${ATLAS.id}?review=9631`,
    );
  });

  it("shows the option, note and question of an answered decision", () => {
    render(
      <Env>
        <BubbleView bubble={userBubble(caseText("decision answered"))} />
      </Env>,
    );
    expect(screen.getByTestId("factory-decision-option")).toHaveTextContent("“Short sha”");
    expect(screen.getByText("Show 7 chars.")).toBeInTheDocument();
    expect(screen.getByText(/Full or short sha\?/)).toBeInTheDocument();
    expect(screen.getByTestId("factory-decision-open")).toHaveAttribute(
      "href",
      `/factory/${ATLAS.id}?decision=D-3`,
    );
  });

  it("omits cockpit links without a factory id", () => {
    render(
      <Env env={{ factoryId: null }}>
        <BubbleView bubble={userBubble(caseText("decision answered"))} />
      </Env>,
    );
    expect(screen.queryByTestId("factory-decision-open")).toBeNull();
  });

  it.each(factoryFixture.malformed.map((c) => [c.name, c.text] as const))(
    "falls back to the normal bubble for %s",
    (_name, text) => {
      render(
        <Env>
          <BubbleView bubble={userBubble(text)} />
        </Env>,
      );
      expect(screen.queryByTestId("factory-msg-card")).toBeNull();
      expect(screen.queryByTestId("factory-marker")).toBeNull();
    },
  );

  it("renders without any provider (no router, no query client)", () => {
    render(
      <FileViewerContext.Provider value={FILE_VIEWER_NOOP}>
        <BubbleView bubble={userBubble(caseText("message raw with project"))} />
      </FileViewerContext.Provider>,
    );
    expect(screen.getByTestId("factory-msg-card")).toBeInTheDocument();
  });
});

describe("Factory marker and card text", () => {
  it("wraps a marker's label instead of truncating it, and shows ISO stamps as times", () => {
    render(
      <Env>
        <BubbleView bubble={userBubble(caseText("needs human decision ref"))} />
      </Env>,
    );
    const label = screen.getByTestId("factory-marker-label");
    expect(label.className).not.toContain("truncate");
    expect(label).toHaveTextContent(/waiting on D-7 since \d/);
    expect(label.textContent).not.toContain("2026-10-26T");
  });

  it("renders repeated delta bullets once each", () => {
    const text =
      "[Factory heartbeat #15 · 2026-10-26T14:15:00Z] 2 changes since #14: • PR #1 checks passed • PR #1 checks passed";
    render(
      <Env>
        <BubbleView bubble={userBubble(text)} />
      </Env>,
    );
    expect(
      within(screen.getByTestId("factory-heartbeat-delta")).getAllByRole("listitem"),
    ).toHaveLength(2);
  });

  it("keeps the server's note to the agent out of an approval's quote", () => {
    render(
      <Env>
        <BubbleView bubble={userBubble(caseText("review approved same line"))} />
      </Env>,
    );
    const card = screen.getByTestId("factory-review-card");
    expect(card).toHaveTextContent("Approved");
    expect(card.textContent).not.toContain("The human will merge on GitHub");
  });
});

describe("link safety", () => {
  it.each([
    ["javascript:alert(1)", null],
    ["JavaScript:alert(1)", null],
    [" javascript:alert(1)", null],
    ["data:text/html,<script>alert(1)</script>", null],
    ["vbscript:msgbox(1)", null],
    ["//evil.example/x", null],
    ["/\\evil.example/x", null],
    ["/\t/evil.example/x", null],
    ["https:evil.example", null],
    ["relative/path", null],
    ["https://app-1.envs.sandbox.caffeinelabs.io/", "external"],
    ["http://localhost:5173/x", "external"],
    ["/c/abc", "in-app"],
    ["/factory/f1?review=7", "in-app"],
  ] as const)("classifies %j as %s", (to, kind) => {
    expect(linkTargetKind(to)).toBe(kind);
  });

  it.each(["javascript:alert(1)", "data:text/html,x", "//evil.example/x"])(
    "renders %j as text, with or without a router",
    (to) => {
      const { container, unmount } = render(<FactoryLink to={to}>Open</FactoryLink>);
      expect(container.querySelector("a")).toBeNull();
      expect(container).toHaveTextContent("Open");
      unmount();
      const routed = render(
        <MemoryRouter>
          <FactoryLink to={to}>Open</FactoryLink>
        </MemoryRouter>,
      );
      expect(routed.container.querySelector("a")).toBeNull();
    },
  );

  it("keeps same-origin demo links in the app and refuses other schemes", () => {
    const origin = window.location.origin;
    expect(inAppPath(`${origin}/factory/f1?review=7#demo`)).toBe("/factory/f1?review=7#demo");
    expect(inAppPath("https://other.example/d")).toBe("https://other.example/d");
    expect(inAppPath("/factory/f1")).toBe("/factory/f1");
    expect(inAppPath("javascript:alert(1)")).toBeNull();
    expect(inAppPath("//evil.example/x")).toBeNull();
  });

  function renderLine(
    item: Extract<RenderItem, { kind: "tool" }>,
    env?: Partial<FactoryTranscriptEnv>,
  ) {
    return render(
      <Env env={env}>
        <FactoryToolLine
          name={item.execution.name}
          arguments={item.execution.arguments}
          output={item.output}
          state={item.state}
        />
      </Env>,
    );
  }

  const noScriptLinks = (root: HTMLElement) =>
    [...root.querySelectorAll("a")].filter(
      (a) => !/^(https?:\/\/|\/(?![/\\]))/.test(a.getAttribute("href") ?? ""),
    );

  it.each(["javascript:alert(document.domain)", "data:text/html,x", "//evil.example/app"])(
    "drops a preview link to %j",
    (app) => {
      const item = toolItem("pl_api", "sys_factory_env", 0);
      const { container } = renderLine({
        ...item,
        execution: {
          ...item.execution,
          arguments: { ...item.execution.arguments, urls: { app } },
        },
      });
      expect(screen.getByTestId("factory-tool-line")).toHaveTextContent("Preview #9630 ready");
      expect(screen.queryByTestId("factory-tool-open-preview")).toBeNull();
      expect(noScriptLinks(container)).toEqual([]);
    },
  );

  it("falls back to the cockpit review for a demo link with a script scheme", () => {
    const item = toolItem("pl_api", "sys_factory_demo", 0);
    const out = parseToolOutputJson(item.output)!;
    const { container } = renderLine({
      ...item,
      output: JSON.stringify({ ...out, demo_url: "javascript:alert(2)" }),
    });
    expect(screen.getByTestId("factory-demo-open")).toHaveAttribute(
      "href",
      `/factory/${ATLAS.id}?review=9630`,
    );
    expect(noScriptLinks(container)).toEqual([]);
  });

  it("drops a GitHub link whose PR url is not http(s)", () => {
    const item = toolItem("pl_api", "sys_factory_pr", 0);
    const { container } = renderLine(
      {
        ...item,
        execution: {
          ...item.execution,
          arguments: { ...item.execution.arguments, url: "javascript:alert(3)//pull/7" },
        },
      },
      { factoryId: null },
    );
    expect(screen.getByTestId("factory-tool-line")).toHaveTextContent("PR #7 registered");
    expect(screen.queryByTestId("factory-tool-open-github")).toBeNull();
    expect(container.querySelectorAll("a")).toHaveLength(0);
  });
});

describe("FactoryPostCard", () => {
  const post = () => toolItem("atlas", "sys_session_post", 0);

  it("reads 'sent' without delivery events and names the target", () => {
    const item = post();
    render(
      <Env>
        <FactoryPostCard
          arguments={item.execution.arguments}
          output={item.output}
          state={item.state}
        />
      </Env>,
    );
    const card = screen.getByTestId("factory-post-card");
    expect(card).toHaveAttribute("data-delivery", "sent");
    expect(card).toHaveAttribute("data-mail-id", "mail_5f60718293a4b5c6");
    expect(within(card).getByText("PL · db-version")).toBeInTheDocument();
    expect(within(card).getByText("(project db-version)")).toBeInTheDocument();
    expect(within(card).getByText("request")).toBeInTheDocument();
  });

  it("shows the delivery receipt joined by mail id", () => {
    const item = post();
    render(
      <Env
        env={{
          deliveryFor: (mailId) =>
            mailId === "mail_5f60718293a4b5c6" ? { state: "delivered", at: 1793017861 } : null,
        }}
      >
        <FactoryPostCard
          arguments={item.execution.arguments}
          output={item.output}
          state={item.state}
        />
      </Env>,
    );
    const chip = screen.getByTestId("factory-post-delivery");
    expect(chip).toHaveAttribute("data-state", "delivered");
    expect(chip).toHaveTextContent("delivered");
  });

  it("marks a refused post as failed with the error code", () => {
    const item = toolItem("atlas", "sys_session_post", 2);
    render(
      <Env>
        <FactoryPostCard
          arguments={item.execution.arguments}
          output={item.output}
          state={item.state}
        />
      </Env>,
    );
    expect(screen.getByTestId("factory-post-card")).toHaveAttribute("data-delivery", "failed");
    expect(screen.getByTestId("factory-post-delivery")).toHaveAttribute("title", "rate_limited");
  });

  it("flags a restart and expands to the full message", () => {
    const item = toolItem("atlas", "sys_session_post", 1);
    render(
      <Env>
        <FactoryPostCard
          arguments={item.execution.arguments}
          output={item.output}
          state={item.state}
        />
      </Env>,
    );
    expect(screen.getByText("restart")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button")[0]!);
    expect(screen.getByText("mail_60718293a4b5c6d7")).toBeInTheDocument();
  });

  it.each([
    ["cancelled", "cancelled"],
    ["no-output", "unconfirmed"],
  ] as const)("never reads 'sent' for a %s call (shows %s)", (state, label) => {
    const item = post();
    render(
      <Env>
        <FactoryPostCard arguments={item.execution.arguments} output={null} state={state} />
      </Env>,
    );
    expect(screen.getByTestId("factory-post-card")).toHaveAttribute("data-delivery", label);
    const chip = screen.getByTestId("factory-post-delivery");
    expect(chip).toHaveTextContent(label);
    expect(chip).not.toHaveTextContent("sent");
    expect(chip.className).not.toContain("text-success");
  });

  it("does not read 'sent' for output without a mail id", () => {
    render(
      <Env>
        <FactoryPostCard
          arguments={{ to: "lead", message: "hi" }}
          output={JSON.stringify({ status: "queued" })}
          state="output-available"
        />
      </Env>,
    );
    expect(screen.getByTestId("factory-post-card")).toHaveAttribute("data-delivery", "unconfirmed");
  });

  it("shows sending while the call runs", () => {
    render(
      <Env>
        <FactoryPostCard
          arguments={{ to: "lead", message: "hi" }}
          output={null}
          state="input-available"
        />
      </Env>,
    );
    expect(screen.getByTestId("factory-post-card")).toHaveAttribute("data-delivery", "sending");
  });
});

describe("FactoryToolLine", () => {
  function renderLine(item: Extract<RenderItem, { kind: "tool" }>) {
    return render(
      <Env>
        <FactoryToolLine
          name={item.execution.name}
          arguments={item.execution.arguments}
          output={item.output}
          state={item.state}
        />
      </Env>,
    );
  }

  it("summarizes the roster with a warning count and an expandable table", () => {
    renderLine(toolItem("atlas", "sys_factory_roster", 1));
    const line = screen.getByTestId("factory-tool-line");
    expect(line).toHaveTextContent("Roster 4 members · 3 sub-agents · 1 ⚠");
    fireEvent.click(within(line).getByRole("button"));
    expect(screen.getByTestId("factory-roster-table")).toHaveTextContent("PL · db-version");
  });

  it.each([
    ["atlas", "sys_factory_set", 0, "Beacon → 2 proj · 0 ICs · 0 ⚠"],
    ["pl_api", "sys_factory_set", 0, "Stage claim → planning"],
    ["atlas", "sys_factory_set", 2, "State of PL · db-version → paused"],
    ["atlas", "sys_factory_spawn", 1, "Spawned PL · api-version · provisioning"],
    [
      "pl_api",
      "sys_factory_ask",
      0,
      "Asked D-3 · Should /version and Settings show the full or the short sha?",
    ],
    ["pl_api", "sys_factory_pr", 0, "PR #9630 registered · ready ✗ checks pending"],
    ["pl_api", "sys_factory_pr", 1, "PR #9630 claimed ready · ready ✓"],
    ["sentinel", "sys_factory_pr", 0, "PR #9630 · Sentinel verified · ready ✓"],
    ["pl_api", "sys_factory_env", 0, "Preview #9630 ready @7767f0d"],
    ["pl_api", "sys_factory_env", 1, "Preview #9631 deploying @b2c3d4e"],
  ] as const)("%s %s #%i → %s", (transcript, tool, nth, text) => {
    renderLine(toolItem(transcript, tool, nth));
    expect(screen.getByTestId("factory-tool-line")).toHaveTextContent(text);
  });

  it("links a spawn to the new session and an ask to its decision", () => {
    renderLine(toolItem("atlas", "sys_factory_spawn", 1));
    expect(screen.getByTestId("factory-tool-open-session")).toHaveAttribute(
      "href",
      `/c/${PL_API.id}`,
    );
    cleanup();
    renderLine(toolItem("pl_api", "sys_factory_ask", 0));
    expect(screen.getByTestId("factory-tool-open-cockpit")).toHaveAttribute(
      "href",
      `/factory/${ATLAS.id}?decision=D-3`,
    );
  });

  it("links a ready preview to its app URL", () => {
    renderLine(toolItem("pl_api", "sys_factory_env", 0));
    expect(screen.getByTestId("factory-tool-open-preview")).toHaveAttribute(
      "href",
      "https://app-b693ef0a-2c66-p9630.envs.sandbox.caffeinelabs.io",
    );
  });

  it("renders a published demo as a card with status, PR and files", () => {
    renderLine(toolItem("pl_api", "sys_factory_demo", 0));
    const card = screen.getByTestId("factory-demo-card");
    expect(card).toHaveAttribute("data-status", "PASS");
    expect(card).toHaveTextContent("PR #9630 @7767f0d");
    expect(card).toHaveTextContent("Settings shows the API version");
    expect(card).toHaveTextContent("video · poster · 2 screenshots · api");
    expect(screen.getByTestId("factory-demo-open")).toHaveAttribute(
      "href",
      expect.stringContaining("?review=9630"),
    );
  });

  it("shows the error code of a refused call", () => {
    renderLine({
      ...toolItem("atlas", "sys_factory_spawn", 0),
      output: JSON.stringify({ error: "duplicate_project", project: "api-version" }),
    });
    const line = screen.getByTestId("factory-tool-line");
    expect(line).toHaveAttribute("data-tone", "danger");
    expect(line).toHaveTextContent("Spawn failed Lead · api-version · duplicate_project");
  });

  it.each([
    ["atlas", "sys_factory_spawn", 1, "cancelled", "Spawn cancelled PL · api-version", "Spawned"],
    ["pl_api", "sys_factory_pr", 1, "no-output", "PR no result #9630", "claimed ready"],
    ["pl_api", "sys_factory_ask", 0, "cancelled", "Ask cancelled Should /version", "D-3"],
    ["pl_api", "sys_factory_env", 0, "no-output", "Preview no result #9630 → ready", "@7767f0d"],
    ["atlas", "sys_factory_roster", 1, "cancelled", "Roster cancelled", "members"],
  ] as const)("%s %s #%i %s reads as unsettled", (transcript, tool, nth, state, text, absent) => {
    renderLine({ ...toolItem(transcript, tool, nth), output: null, state });
    const line = screen.getByTestId("factory-tool-line");
    expect(line).toHaveAttribute("data-tone", "muted");
    expect(line).toHaveTextContent(text);
    expect(line).not.toHaveTextContent(absent);
    expect(screen.queryByTestId("factory-tool-open-session")).toBeNull();
    expect(screen.queryByTestId("factory-tool-open-preview")).toBeNull();
  });

  it("renders a cancelled demo publish as a line, not a published card", () => {
    renderLine({ ...toolItem("pl_api", "sys_factory_demo", 0), output: null, state: "cancelled" });
    expect(screen.queryByTestId("factory-demo-card")).toBeNull();
    expect(screen.getByTestId("factory-tool-line")).toHaveTextContent(
      "Demo publish cancelled PR #9630 · @7767f0d",
    );
  });

  it("names a running call by what it is doing", () => {
    renderLine({
      ...toolItem("pl_api", "sys_factory_demo", 0),
      output: null,
      state: "input-available",
    });
    expect(screen.getByTestId("factory-tool-line")).toHaveTextContent("Publishing demo PR #9630");
  });
});

describe("HeartbeatGroup", () => {
  it("renders the folded beats as one row and expands each tick", () => {
    const bubbles = foldFactoryHeartbeats(bubblesOf("atlas"));
    const group = bubbles.find((b) => b.kind === "heartbeat_group")!;
    render(
      <Env>
        <BubbleView bubble={group} />
      </Env>,
    );
    const row = screen.getByTestId("heartbeat-group");
    expect(row).toHaveAttribute("data-count", "2");
    expect(row).toHaveTextContent(/^2 heartbeats · no change · .+–.+$/);
    fireEvent.click(within(row).getByRole("button", { expanded: false }));
    const ticks = screen.getByTestId("heartbeat-group-ticks");
    expect(within(ticks).getAllByRole("listitem")).toHaveLength(2);
    expect(ticks).toHaveTextContent("Nothing changed.");
    expect(ticks).toHaveTextContent("HEARTBEAT_OK · 2 PLs provisioning");
  });
});

describe("BlockRenderer factory dispatch", () => {
  it("renders posts and outcomes as factory rows and keeps them visible after the fold", () => {
    const turn = bubblesOf("pl_api").find(
      (b): b is Extract<Bubble, { kind: "assistant" }> =>
        b.kind === "assistant" &&
        b.items.some((i) => i.kind === "tool" && i.execution.name.endsWith("sys_factory_demo")),
    )!;
    // A bookkeeping call (beacon) folds; the outcomes and the post do not.
    const items = [toolItem("pl_api", "sys_factory_set", 0), ...turn.items];
    render(
      <Env>
        <BlockRenderer items={items} sessionStatus="idle" turnLifecycle="completed" />
      </Env>,
    );
    // The settled turn folds its trace behind "Worked", yet the outcomes stay.
    expect(screen.getByTestId("turn-worked-fold")).toBeInTheDocument();
    expect(screen.getByTestId("factory-demo-card")).toBeInTheDocument();
    expect(screen.getByTestId("factory-post-card")).toBeInTheDocument();
    expect(screen.getAllByTestId("factory-tool-line").map((l) => l.dataset.tool)).toEqual([
      "sys_factory_env",
      "sys_factory_pr",
    ]);
  });
});
